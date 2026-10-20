"""SQLite/MySQL persistence and business operations for FoodBridge."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

DB_BACKEND = os.getenv("FOODBRIDGE_DB_BACKEND", "sqlite").strip().lower()
DB_PATH = Path(os.getenv("FOODBRIDGE_DB", Path(__file__).with_name("foodbridge.db")))
_MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")


def _mysql_password() -> str:
    """Read the database password from the environment; never prompt on a server."""
    password = os.getenv("MYSQL_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "MYSQL_PASSWORD is missing. Set it in .env locally or in your hosting provider's environment settings."
        )
    return password


class MySQLConnection:
    """Small DB-API adapter so the app can keep its existing question-mark SQL."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            self.connection.begin()
            return self.connection.cursor()
        sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT IGNORE INTO", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\s+COLLATE\s+NOCASE\b", "", sql, flags=re.IGNORECASE)
        cursor = self.connection.cursor()
        cursor.execute(sql.replace("?", "%s"), params)
        return cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()


def connect():
    if DB_BACKEND in {"mysql", "mariadb"}:
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise RuntimeError("MySQL support requires PyMySQL. Install requirements.txt.") from exc
        connection_options = {
            "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": _mysql_password(),
            "database": os.getenv("MYSQL_DATABASE", "foodbridge"),
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "autocommit": False,
            "connect_timeout": 10,
        }
        ca_value = os.getenv("MYSQL_SSL_CA", "").strip()
        if ca_value:
            ca_path = Path(ca_value)
            if not ca_path.is_absolute():
                ca_path = Path(__file__).resolve().parent / ca_path
            if not ca_path.is_file():
                raise RuntimeError(f"MYSQL_SSL_CA certificate was not found: {ca_path}")
            connection_options.update({
                "ssl_ca": str(ca_path),
                "ssl_verify_cert": True,
                "ssl_verify_identity": True,
            })
        elif os.getenv("MYSQL_SSL_REQUIRED", "false").lower() == "true":
            connection_options.update({"ssl_verify_cert": True, "ssl_verify_identity": True})
        connection = pymysql.connect(**connection_options)
        return MySQLConnection(connection)
    if DB_BACKEND != "sqlite":
        raise RuntimeError("FOODBRIDGE_DB_BACKEND must be 'sqlite' or 'mysql'.")
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_sqlite_schema():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS restaurants (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, contact TEXT, email TEXT, phone TEXT,
          location TEXT, website TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ngos (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, contact TEXT, email TEXT, phone TEXT,
          location TEXT, service_area TEXT, mission TEXT, categories TEXT, capacity INTEGER,
          arrangements TEXT, need TEXT, info_status TEXT DEFAULT 'Information provided', created_at TEXT NOT NULL,
          website TEXT
        );
        CREATE TABLE IF NOT EXISTS donations (
          id TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL, restaurant_id TEXT NOT NULL REFERENCES restaurants(id),
          title TEXT NOT NULL, quantity INTEGER NOT NULL, unit TEXT NOT NULL, category TEXT NOT NULL,
          description TEXT, allergens TEXT, prepared_at TEXT, available_from TEXT, deadline TEXT NOT NULL,
          location TEXT NOT NULL, handling TEXT, packaging TEXT, refrigeration INTEGER DEFAULT 0,
          contact_instructions TEXT, image_url TEXT, status TEXT NOT NULL DEFAULT 'Available',
          created_at TEXT NOT NULL, completed_quantity INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS requests (
          id TEXT PRIMARY KEY, donation_id TEXT NOT NULL REFERENCES donations(id), ngo_id TEXT NOT NULL REFERENCES ngos(id),
          quantity INTEGER NOT NULL, pickup_at TEXT NOT NULL, pickup_contact TEXT, message TEXT,
          status TEXT NOT NULL DEFAULT 'Pending', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS foodtrust_reports (
          id TEXT PRIMARY KEY, restaurant_id TEXT REFERENCES restaurants(id), ngo_id TEXT REFERENCES ngos(id), website TEXT NOT NULL,
          state TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activity (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, message TEXT NOT NULL, donation_id TEXT,
          created_at TEXT NOT NULL, read INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, email TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('restaurant','ngo','admin')),
          profile_id TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contact_messages (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL, subject TEXT NOT NULL,
          message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'New', created_at TEXT NOT NULL
        );
        """)
        ngo_columns = {column[1] for column in db.execute("PRAGMA table_info(ngos)").fetchall()}
        if "website" not in ngo_columns:
            db.execute("ALTER TABLE ngos ADD COLUMN website TEXT DEFAULT ''")
        report_columns = {column[1] for column in db.execute("PRAGMA table_info(foodtrust_reports)").fetchall()}
        if "ngo_id" not in report_columns:
            db.execute("ALTER TABLE foodtrust_reports ADD COLUMN ngo_id TEXT REFERENCES ngos(id)")
        user_columns = {column[1] for column in db.execute("PRAGMA table_info(users)").fetchall()}
        if "username" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN username TEXT")
            db.execute("UPDATE users SET username=lower(substr(email,1,instr(email,'@')-1)) WHERE username IS NULL")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE)")


def _init_mysql_schema():
    schema = [
        """CREATE TABLE IF NOT EXISTS restaurants (
          id VARCHAR(64) PRIMARY KEY, name VARCHAR(255) NOT NULL, contact TEXT, email VARCHAR(320), phone VARCHAR(64),
          location VARCHAR(255), website TEXT, created_at VARCHAR(32) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS ngos (
          id VARCHAR(64) PRIMARY KEY, name VARCHAR(255) NOT NULL, contact TEXT, email VARCHAR(320), phone VARCHAR(64),
          location VARCHAR(255), service_area VARCHAR(255), mission TEXT, categories LONGTEXT, capacity INTEGER,
          arrangements TEXT, need TEXT, info_status VARCHAR(100) DEFAULT 'Information provided', created_at VARCHAR(32) NOT NULL,
          website TEXT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS donations (
          id VARCHAR(64) PRIMARY KEY, ref VARCHAR(32) UNIQUE NOT NULL, restaurant_id VARCHAR(64) NOT NULL,
          title VARCHAR(255) NOT NULL, quantity INTEGER NOT NULL, unit VARCHAR(50) NOT NULL, category VARCHAR(100) NOT NULL,
          description TEXT, allergens TEXT, prepared_at VARCHAR(32), available_from VARCHAR(32), deadline VARCHAR(32) NOT NULL,
          location VARCHAR(255) NOT NULL, handling TEXT, packaging VARCHAR(255), refrigeration TINYINT DEFAULT 0,
          contact_instructions TEXT, image_url TEXT, status VARCHAR(50) NOT NULL DEFAULT 'Available',
          created_at VARCHAR(32) NOT NULL, completed_quantity INTEGER DEFAULT 0,
          CONSTRAINT fk_donations_restaurant FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS requests (
          id VARCHAR(64) PRIMARY KEY, donation_id VARCHAR(64) NOT NULL, ngo_id VARCHAR(64) NOT NULL,
          quantity INTEGER NOT NULL, pickup_at VARCHAR(32) NOT NULL, pickup_contact TEXT, message TEXT,
          status VARCHAR(50) NOT NULL DEFAULT 'Pending', created_at VARCHAR(32) NOT NULL,
          CONSTRAINT fk_requests_donation FOREIGN KEY (donation_id) REFERENCES donations(id),
          CONSTRAINT fk_requests_ngo FOREIGN KEY (ngo_id) REFERENCES ngos(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS foodtrust_reports (
          id VARCHAR(64) PRIMARY KEY, restaurant_id VARCHAR(64), ngo_id VARCHAR(64), website TEXT NOT NULL,
          state VARCHAR(120) NOT NULL, evidence LONGTEXT NOT NULL, created_at VARCHAR(32) NOT NULL,
          CONSTRAINT fk_reports_restaurant FOREIGN KEY (restaurant_id) REFERENCES restaurants(id),
          CONSTRAINT fk_reports_ngo FOREIGN KEY (ngo_id) REFERENCES ngos(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS activity (
          id VARCHAR(64) PRIMARY KEY, kind VARCHAR(100) NOT NULL, message TEXT NOT NULL, donation_id VARCHAR(64),
          created_at VARCHAR(32) NOT NULL, `read` TINYINT DEFAULT 0,
          CONSTRAINT fk_activity_donation FOREIGN KEY (donation_id) REFERENCES donations(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS users (
          id VARCHAR(64) PRIMARY KEY, username VARCHAR(80) NOT NULL, email VARCHAR(320) NOT NULL,
          password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL, profile_id VARCHAR(64),
          status VARCHAR(20) NOT NULL DEFAULT 'pending', created_at VARCHAR(32) NOT NULL,
          UNIQUE KEY idx_users_username (username), UNIQUE KEY idx_users_email (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS contact_messages (
          id VARCHAR(64) PRIMARY KEY, name VARCHAR(255) NOT NULL, email VARCHAR(320) NOT NULL,
          subject VARCHAR(255) NOT NULL, message TEXT NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'New',
          created_at VARCHAR(32) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    ]
    with connect() as db:
        for statement in schema:
            db.execute(statement)


def _seed_demo_data():
    with connect() as db:
        if not db.execute("SELECT 1 FROM restaurants LIMIT 1").fetchone():
            now = datetime.now().isoformat(timespec="minutes")
            r1, r2 = "demo-restaurant-1", "demo-restaurant-2"
            r3 = "demo-restaurant-3"
            n1, n2, n3 = "demo-ngo-1", "demo-ngo-2", "demo-ngo-3"
            db.execute("INSERT INTO restaurants VALUES (?,?,?,?,?,?,?,?)", (r1, "Green Bowl Kitchen", "Maya Rao", "hello@greenbowl.example", "+91 98765 00001", "Connaught Place, New Delhi", "https://example.com", now))
            db.execute("INSERT INTO restaurants VALUES (?,?,?,?,?,?,?,?)", (r2, "Sunrise Bakery", "Arjun Mehta", "team@sunrise.example", "+91 98765 00002", "Karol Bagh, New Delhi", "https://example.org", now))
            db.execute("INSERT INTO restaurants VALUES (?,?,?,?,?,?,?,?)", (r3, "Singh Sahib", "", "", "+91 11 4560 7359", "Sector 5, Dwarka, New Delhi", "https://singhsahib.in", now))
            ngo_fields = "id,name,contact,email,phone,location,service_area,mission,categories,capacity,arrangements,need,info_status,created_at,website"
            db.execute(f"INSERT INTO ngos ({ngo_fields}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (n1, "Seva Community Kitchen", "Anita Sharma", "hello@seva.example", "+91 98765 10001", "New Delhi", "Central Delhi", "Serving warm meals to families and older adults.", json.dumps(["Vegetarian", "Bakery", "Packaged"]), 80, "Pickup van; insulated boxes available", "30 vegetarian meals by 2 PM", "Information provided", now, ""))
            db.execute(f"INSERT INTO ngos ({ngo_fields}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (n2, "Neighbourhood Food Circle", "Kabir Singh", "team@foodcircle.example", "+91 98765 10002", "New Delhi", "Central and North Delhi", "A volunteer-led food redistribution network.", json.dumps(["Vegetarian", "Vegan", "Bakery"]), 45, "Volunteer pickup; please call on arrival", "Bakery items for evening distribution", "Information provided", now, ""))
            db.execute(f"INSERT INTO ngos ({ngo_fields}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (n3, "Abhyanga Foundation", "", "info@abhyangafoundation.org", "+91 11 4019 5993", "K.G. Marg, New Delhi", "New Delhi", "Works across health, livelihoods, education, social protection and community meal support.", json.dumps(["Vegetarian", "Packaged", "Bakery"]), 60, "Coordinate pickup with the organisation", "Current need not provided", "Information provided", now, "https://www.abhyangafoundation.org"))
            samples = [
              ("FB-1042",r1,"40 vegetarian meal boxes",40,"meals","Vegetarian","Freshly prepared rice, dal and seasonal vegetables.","Contains dairy; other allergens not confirmed",(datetime.now()+timedelta(hours=1)).isoformat(timespec="minutes"),(datetime.now()+timedelta(hours=4)).isoformat(timespec="minutes"),"Connaught Place, New Delhi","Kept covered; collect promptly","Individual meal boxes",1,"Use the side entrance"),
              ("FB-1041",r2,"Assorted bakery packs",24,"packs","Bakery","A mix of breads and pastries from today's bake.","May contain gluten, dairy and nuts",(datetime.now()+timedelta(hours=2)).isoformat(timespec="minutes"),(datetime.now()+timedelta(hours=6)).isoformat(timespec="minutes"),"Karol Bagh, New Delhi","Store in a clean, dry place","Paper bags",0,"Ask for the donation shelf"),
              ("FB-1040",r3,"Vegetarian lunch thalis",32,"meals","Vegetarian","Fictional demo listing: a surplus of vegetarian lunch thalis for community pickup.","Allergen details not confirmed",(datetime.now()+timedelta(hours=1)).isoformat(timespec="minutes"),(datetime.now()+timedelta(hours=3)).isoformat(timespec="minutes"),"Lajpat Nagar, New Delhi","Collect promptly; handling details to be confirmed","Meal boxes",0,"Call the restaurant before arrival")]
            for ref, rid, title, qty, unit, cat, desc, allergens, available, deadline, location, handling, packaging, fridge, instructions in samples:
                db.execute("INSERT INTO donations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),ref,rid,title,qty,unit,cat,desc,allergens,available,available,deadline,location,handling,packaging,fridge,instructions,None,"Available",now,0))
        now = datetime.now().isoformat(timespec="seconds")
        if not db.execute("SELECT 1 FROM restaurants WHERE id='demo-restaurant-3'").fetchone():
            db.execute("INSERT INTO restaurants VALUES (?,?,?,?,?,?,?,?)", ("demo-restaurant-3", "Singh Sahib", "", "", "+91 11 4560 7359", "Sector 5, Dwarka, New Delhi", "https://singhsahib.in", now))
        if not db.execute("SELECT 1 FROM ngos WHERE id='demo-ngo-3'").fetchone():
            db.execute("INSERT INTO ngos (id,name,contact,email,phone,location,service_area,mission,categories,capacity,arrangements,need,info_status,created_at,website) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("demo-ngo-3", "Abhyanga Foundation", "", "info@abhyangafoundation.org", "+91 11 4019 5993", "K.G. Marg, New Delhi", "New Delhi", "Works across health, livelihoods, education, social protection and community meal support.", json.dumps(["Vegetarian", "Packaged", "Bakery"]), 60, "Coordinate pickup with the organisation", "Current need not provided", "Information provided", now, "https://www.abhyangafoundation.org"))
        db.execute("UPDATE ngos SET website='https://www.abhyangafoundation.org',email='info@abhyangafoundation.org',phone='+91 11 4019 5993',location='K.G. Marg, New Delhi',service_area='New Delhi',mission='Works across health, livelihoods, education, social protection and community meal support.' WHERE id='demo-ngo-3'")
        db.execute("UPDATE restaurants SET contact='',email='',phone='+91 11 4560 7359',location='Sector 5, Dwarka, New Delhi',website='https://singhsahib.in' WHERE id='demo-restaurant-3'")
        if not db.execute("SELECT 1 FROM donations WHERE ref='FB-1040'").fetchone():
            available = (datetime.now()+timedelta(hours=1)).isoformat(timespec="minutes")
            deadline = (datetime.now()+timedelta(hours=3)).isoformat(timespec="minutes")
            db.execute("INSERT INTO donations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),"FB-1040","demo-restaurant-3","Vegetarian lunch thalis",32,"meals","Vegetarian","Fictional demo listing: a surplus of vegetarian lunch thalis for community pickup.","Allergen details not confirmed",available,available,deadline,"Lajpat Nagar, New Delhi","Collect promptly; handling details to be confirmed","Meal boxes",0,"Call the restaurant before arrival",None,"Available",now,0))
        now = datetime.now().isoformat(timespec="minutes")
        # Keep stable demo usernames when upgrading a database from the earlier email-login MVP.
        for username, email in (("admin","admin@foodbridge.local"),("greenbowl","hello@greenbowl.example"),("seva","team@seva.example")):
            db.execute("UPDATE users SET username=? WHERE email=? COLLATE NOCASE", (username,email))
        demo_password_hash = generate_password_hash("DemoFoodBridge!26")
        admin_password_hash = generate_password_hash(os.getenv("FOODBRIDGE_ADMIN_PASSWORD", "admin 123"))
        demo_users = [
            ("admin", "admin@foodbridge.local", admin_password_hash, "admin", None, "approved"),
            ("greenbowl", "hello@greenbowl.example", demo_password_hash, "restaurant", "demo-restaurant-1", "approved"),
            ("seva", "team@seva.example", demo_password_hash, "ngo", "demo-ngo-1", "approved"),
            ("singhsahib", "singhsahib@foodbridge.example", demo_password_hash, "restaurant", "demo-restaurant-3", "approved"),
            ("abhyanga", "abhyanga@foodbridge.example", demo_password_hash, "ngo", "demo-ngo-3", "approved"),
        ]
        for username, email, password_hash, role, profile_id, status in demo_users:
            db.execute("INSERT OR IGNORE INTO users (id,username,email,password_hash,role,profile_id,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), username, email, password_hash, role, profile_id, status, now))
        # Apply the requested demo admin password to an already-existing migrated account too.
        db.execute("UPDATE users SET password_hash=? WHERE username='admin' AND role='admin'", (admin_password_hash,))


def _ensure_admin_user():
    """Create a configured administrator when demo seeding is disabled."""
    username = os.getenv("FOODBRIDGE_ADMIN_USERNAME", "admin").strip().lower()
    email = os.getenv("FOODBRIDGE_ADMIN_EMAIL", "admin@foodbridge.local").strip().lower()
    password = os.getenv("FOODBRIDGE_ADMIN_PASSWORD", "")
    if not password:
        return

    existing = one("SELECT id, role FROM users WHERE username=?", (username,))
    if existing:
        if existing["role"] != "admin":
            raise RuntimeError("FOODBRIDGE_ADMIN_USERNAME is already used by a non-admin account.")
        # Do not reset a live administrator's password on every restart.
        return

    with connect() as conn:
        conn.execute(
            "INSERT INTO users (id,username,email,password_hash,role,profile_id,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), username, email, generate_password_hash(password), "admin", None, "approved", datetime.now().isoformat(timespec="seconds")),
        )


def init_db():
    if DB_BACKEND in {"mysql", "mariadb"}:
        _init_mysql_schema()
    elif DB_BACKEND == "sqlite":
        _init_sqlite_schema()
    else:
        raise RuntimeError("FOODBRIDGE_DB_BACKEND must be 'sqlite' or 'mysql'.")

    seed_setting = os.getenv("FOODBRIDGE_SEED_DEMO_DATA")
    if seed_setting is None:
        seed_demo = DB_BACKEND == "sqlite"
    else:
        seed_demo = seed_setting.strip().lower() in {"1", "true", "yes", "on"}
    if seed_demo:
        _seed_demo_data()
    else:
        if DB_BACKEND in {"mysql", "mariadb"} and not os.getenv("FOODBRIDGE_ADMIN_PASSWORD", "").strip():
            raise RuntimeError(
                "Set FOODBRIDGE_ADMIN_PASSWORD before initializing a new cloud database, "
                "or explicitly enable FOODBRIDGE_SEED_DEMO_DATA for a local demo only."
            )
        _ensure_admin_user()


def get_user_by_username(username):
    return one("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.strip(),))


def get_user_by_email(email):
    return one("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email.strip(),))


def get_user(user_id):
    return one("SELECT * FROM users WHERE id=?", (user_id,))


def add_user(username, email, password_hash, role, profile_id, status="pending"):
    user_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute("INSERT INTO users (id,username,email,password_hash,role,profile_id,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (user_id,username.strip().lower(),email.strip().lower(),password_hash,role,profile_id,status,datetime.now().isoformat(timespec="seconds")))
    return user_id


def rows(sql, params=()):
    with connect() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def one(sql, params=()):
    with connect() as db:
        r = db.execute(sql, params).fetchone()
        return dict(r) if r else None


def create_restaurant(name, contact, email, phone, location, website):
    rid = str(uuid.uuid4())
    with connect() as db:
        db.execute("INSERT INTO restaurants VALUES (?,?,?,?,?,?,?,?)", (rid,name,contact,email,phone,location,website,datetime.now().isoformat(timespec="minutes")))
    return rid


def create_ngo(name, contact, email, phone, location, service_area, mission, categories, capacity, arrangements, need, website=""):
    nid = str(uuid.uuid4())
    with connect() as db:
        db.execute("INSERT INTO ngos (id,name,contact,email,phone,location,service_area,mission,categories,capacity,arrangements,need,info_status,created_at,website) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (nid,name,contact,email,phone,location,service_area,mission,json.dumps(categories),capacity,arrangements,need,"Information provided",datetime.now().isoformat(timespec="minutes"),website))
    return nid


def create_donation(data):
    did, ref = str(uuid.uuid4()), "FB-" + uuid.uuid4().hex[:6].upper()
    now = datetime.now().isoformat(timespec="minutes")
    with connect() as db:
        db.execute("""INSERT INTO donations
        (id,ref,restaurant_id,title,quantity,unit,category,description,allergens,prepared_at,available_from,deadline,location,handling,packaging,refrigeration,contact_instructions,image_url,status,created_at,completed_quantity)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""", (did,ref,data["restaurant_id"],data["title"],data["quantity"],data["unit"],data["category"],data["description"],data["allergens"],data["prepared_at"],data["available_from"],data["deadline"],data["location"],data["handling"],data["packaging"],int(data["refrigeration"]),data["contact_instructions"],data.get("image_url"),"Available",now))
        db.execute("INSERT INTO activity VALUES (?,?,?,?,?,0)", (str(uuid.uuid4()),"Donation listed",f"{data['title']} is now available ({ref}).",did,now))
    return did, ref


def submit_request(donation_id, ngo_id, quantity, pickup_at, pickup_contact, message):
    now = datetime.now().isoformat(timespec="minutes")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        lock_suffix = " FOR UPDATE" if DB_BACKEND in {"mysql", "mariadb"} else ""
        donation = db.execute("SELECT * FROM donations WHERE id=?" + lock_suffix, (donation_id,)).fetchone()
        if not donation or donation["status"] not in ("Available", "Requested") or datetime.fromisoformat(donation["deadline"]) <= datetime.now():
            raise ValueError("This donation is no longer available.")
        if quantity < 1 or quantity > donation["quantity"]:
            raise ValueError("Requested quantity must fit the available amount.")
        rid = str(uuid.uuid4())
        db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)", (rid,donation_id,ngo_id,quantity,pickup_at,pickup_contact,message,"Pending",now))
        db.execute("UPDATE donations SET status='Requested' WHERE id=?", (donation_id,))
        db.execute("INSERT INTO activity VALUES (?,?,?,?,?,0)", (str(uuid.uuid4()),"New request",f"A new request was submitted for {donation['ref']}.",donation_id,now))
    return rid


def update_request(request_id, action):
    states = {"accept": ("Accepted", "Accepted"), "decline": ("Declined", "Available")}
    if action not in states: raise ValueError("Unsupported request action.")
    req_status, donation_status = states[action]
    now = datetime.now().isoformat(timespec="minutes")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        lock_suffix = " FOR UPDATE" if DB_BACKEND in {"mysql", "mariadb"} else ""
        req = db.execute("SELECT * FROM requests WHERE id=?" + lock_suffix, (request_id,)).fetchone()
        if not req or req["status"] != "Pending": raise ValueError("This request is no longer pending.")
        db.execute("UPDATE requests SET status=? WHERE id=?", (req_status, request_id))
        if action == "accept": db.execute("UPDATE requests SET status='Declined' WHERE donation_id=? AND id<>? AND status='Pending'", (req["donation_id"],request_id))
        if action == "decline" and db.execute("SELECT 1 FROM requests WHERE donation_id=? AND status='Pending' LIMIT 1", (req["donation_id"],)).fetchone():
            donation_status = "Requested"
        db.execute("UPDATE donations SET status=? WHERE id=?", (donation_status,req["donation_id"]))
        db.execute("INSERT INTO activity VALUES (?,?,?,?,?,0)", (str(uuid.uuid4()),"Request updated",f"A donation request was {req_status.lower()}.",req["donation_id"],now))


def advance_donation(donation_id, status):
    allowed = {"Picked up": "Accepted", "Completed": "Picked up", "Cancelled": ("Available", "Requested", "Accepted")}
    if status not in allowed: raise ValueError("Unsupported status change.")
    now = datetime.now().isoformat(timespec="minutes")
    with connect() as db:
        current = db.execute("SELECT status,quantity FROM donations WHERE id=?", (donation_id,)).fetchone()
        if not current: raise ValueError("Donation not found.")
        valid = allowed[status]
        if current["status"] not in (valid if isinstance(valid, tuple) else (valid,)): raise ValueError(f"Cannot move {current['status']} to {status}.")
        db.execute("UPDATE donations SET status=?,completed_quantity=CASE WHEN ?='Completed' THEN quantity ELSE completed_quantity END WHERE id=?", (status,status,donation_id))
        db.execute("INSERT INTO activity VALUES (?,?,?,?,?,0)", (str(uuid.uuid4()),"Donation status",f"Donation status changed to {status}.",donation_id,now))


def save_report(restaurant_id, website, state, evidence):
    with connect() as db:
        db.execute("INSERT INTO foodtrust_reports (id,restaurant_id,website,state,evidence,created_at) VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()),restaurant_id,website,state,json.dumps(evidence),datetime.now().isoformat(timespec="minutes")))


def latest_report(restaurant_id):
    result = one("SELECT * FROM foodtrust_reports WHERE restaurant_id=? ORDER BY created_at DESC LIMIT 1", (restaurant_id,))
    if result: result["evidence"] = json.loads(result["evidence"])
    return result


def save_ngo_report(ngo_id, website, state, evidence):
    with connect() as db:
        db.execute("INSERT INTO foodtrust_reports (id,ngo_id,website,state,evidence,created_at) VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()),ngo_id,website,state,json.dumps(evidence),datetime.now().isoformat(timespec="minutes")))


def latest_ngo_report(ngo_id):
    result = one("SELECT * FROM foodtrust_reports WHERE ngo_id=? ORDER BY created_at DESC LIMIT 1", (ngo_id,))
    if result: result["evidence"] = json.loads(result["evidence"])
    return result
