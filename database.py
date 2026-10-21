"""MySQL persistence and business operations for FoodBridge (MySQL/MariaDB only)."""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv(Path(__file__).resolve().parent / ".env")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("foodbridge.db")

IS_PRODUCTION = bool(os.getenv("RENDER")) or os.getenv("APP_ENV", "").lower() == "production"

# Report states that count as a real result. Failed lookups are never stored,
# so they can no longer show up as a "trust available" badge.
SAVEABLE_REPORT_STATES = {"Google review results found", "Web review results found", "Profile information found"}
FAILED_REPORT_STATES = (
    "API key not configured", "API request failed", "Research unavailable",
    "No Google review excerpts found", "No web review excerpts found", "No profile information found",
)

_legacy_backend = os.getenv("FOODBRIDGE_DB_BACKEND", "mysql").strip().lower()
if _legacy_backend not in {"mysql", "mariadb"}:
    raise RuntimeError(
        "SQLite is no longer supported. FoodBridge now runs on MySQL/MariaDB only. "
        "Remove FOODBRIDGE_DB_BACKEND (or set it to 'mysql') and set the MYSQL_* variables."
    )


# Only NGOs that completed a pickup from a restaurant may review it. Set to false to allow any NGO.
REVIEWS_REQUIRE_PICKUP = os.getenv("FOODBRIDGE_REVIEWS_REQUIRE_PICKUP", "true").strip().lower() in {"1", "true", "yes", "on"}


def _now(precision="seconds"):
    return datetime.now().isoformat(timespec=precision)


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------
class MySQLConnection:
    """Thin adapter so the app can keep its question-mark SQL."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        if sql.strip().upper() in ("BEGIN", "BEGIN IMMEDIATE"):
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


def _settings():
    """Read and validate MySQL settings. Fail loudly instead of guessing in production."""
    host = os.getenv("MYSQL_HOST", "").strip()
    password = os.getenv("MYSQL_PASSWORD", "")
    if IS_PRODUCTION:
        missing = [name for name, value in (("MYSQL_HOST", host), ("MYSQL_USER", os.getenv("MYSQL_USER", "").strip()),
                                            ("MYSQL_PASSWORD", password), ("MYSQL_DATABASE", os.getenv("MYSQL_DATABASE", "").strip()))
                   if not value]
        if missing:
            raise RuntimeError("Missing required production database settings: " + ", ".join(missing))
    elif not password:
        raise RuntimeError("MYSQL_PASSWORD is missing. Set it in your .env file.")
    return {
        "host": host or "127.0.0.1",
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": password,
        "database": os.getenv("MYSQL_DATABASE", "foodbridge"),
    }


def connect():
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:
        raise RuntimeError("PyMySQL is required. Run: pip install pymysql") from exc

    options = {**_settings(), "charset": "utf8mb4", "cursorclass": DictCursor,
               "autocommit": False, "connect_timeout": 10}

    ca_value = os.getenv("MYSQL_SSL_CA", "").strip()
    if ca_value:
        ca_path = Path(ca_value)
        if not ca_path.is_absolute():
            ca_path = Path(__file__).resolve().parent / ca_path
        if not ca_path.is_file():
            raise RuntimeError(f"MYSQL_SSL_CA certificate was not found: {ca_path}")
        options.update({"ssl_ca": str(ca_path), "ssl_verify_cert": True, "ssl_verify_identity": True})
    elif os.getenv("MYSQL_SSL_REQUIRED", "false").lower() == "true":
        options.update({"ssl_verify_cert": True, "ssl_verify_identity": True})

    try:
        return MySQLConnection(pymysql.connect(**options))
    except pymysql.MySQLError as exc:
        raise RuntimeError(
            f"Cannot connect to MySQL at {options['host']}:{options['port']} "
            f"(database '{options['database']}', user '{options['user']}'): {exc}"
        ) from exc


def rows(sql, params=()):
    with connect() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def one(sql, params=()):
    with connect() as db:
        r = db.execute(sql, params).fetchone()
        return dict(r) if r else None


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
_TAIL = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"

SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS restaurants (
      id VARCHAR(64) PRIMARY KEY, name VARCHAR(255) NOT NULL, contact TEXT, email VARCHAR(320), phone VARCHAR(64),
      location VARCHAR(255), website TEXT, created_at VARCHAR(32) NOT NULL
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS ngos (
      id VARCHAR(64) PRIMARY KEY, name VARCHAR(255) NOT NULL, contact TEXT, email VARCHAR(320), phone VARCHAR(64),
      location VARCHAR(255), service_area VARCHAR(255), mission TEXT, categories LONGTEXT, capacity INTEGER,
      arrangements TEXT, need TEXT, info_status VARCHAR(100) DEFAULT 'Information provided',
      created_at VARCHAR(32) NOT NULL, website TEXT NULL
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS donations (
      id VARCHAR(64) PRIMARY KEY, ref VARCHAR(32) UNIQUE NOT NULL, restaurant_id VARCHAR(64) NOT NULL,
      title VARCHAR(255) NOT NULL, quantity INTEGER NOT NULL, unit VARCHAR(50) NOT NULL, category VARCHAR(100) NOT NULL,
      description TEXT, allergens TEXT, prepared_at VARCHAR(32), available_from VARCHAR(32), deadline VARCHAR(32) NOT NULL,
      location VARCHAR(255) NOT NULL, handling TEXT, packaging VARCHAR(255), refrigeration TINYINT DEFAULT 0,
      contact_instructions TEXT, image_url TEXT, status VARCHAR(50) NOT NULL DEFAULT 'Available',
      created_at VARCHAR(32) NOT NULL, completed_quantity INTEGER DEFAULT 0,
      KEY idx_donations_restaurant (restaurant_id, created_at),
      KEY idx_donations_status (status),
      CONSTRAINT fk_donations_restaurant FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS requests (
      id VARCHAR(64) PRIMARY KEY, donation_id VARCHAR(64) NOT NULL, ngo_id VARCHAR(64) NOT NULL,
      quantity INTEGER NOT NULL, pickup_at VARCHAR(32) NOT NULL, pickup_contact TEXT, message TEXT,
      status VARCHAR(50) NOT NULL DEFAULT 'Pending', created_at VARCHAR(32) NOT NULL,
      KEY idx_requests_donation (donation_id, status),
      KEY idx_requests_ngo (ngo_id, created_at),
      CONSTRAINT fk_requests_donation FOREIGN KEY (donation_id) REFERENCES donations(id),
      CONSTRAINT fk_requests_ngo FOREIGN KEY (ngo_id) REFERENCES ngos(id)
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS foodtrust_reports (
      id VARCHAR(64) PRIMARY KEY, restaurant_id VARCHAR(64), ngo_id VARCHAR(64), website TEXT NOT NULL,
      state VARCHAR(120) NOT NULL, evidence LONGTEXT NOT NULL, created_at VARCHAR(32) NOT NULL,
      KEY idx_reports_restaurant (restaurant_id, created_at),
      KEY idx_reports_ngo (ngo_id, created_at),
      CONSTRAINT fk_reports_restaurant FOREIGN KEY (restaurant_id) REFERENCES restaurants(id),
      CONSTRAINT fk_reports_ngo FOREIGN KEY (ngo_id) REFERENCES ngos(id)
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS activity (
      id VARCHAR(64) PRIMARY KEY, kind VARCHAR(100) NOT NULL, message TEXT NOT NULL, donation_id VARCHAR(64),
      created_at VARCHAR(32) NOT NULL, `read` TINYINT DEFAULT 0,
      CONSTRAINT fk_activity_donation FOREIGN KEY (donation_id) REFERENCES donations(id)
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS users (
      id VARCHAR(64) PRIMARY KEY, username VARCHAR(80) NOT NULL, email VARCHAR(320) NOT NULL,
      password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL, profile_id VARCHAR(64),
      status VARCHAR(20) NOT NULL DEFAULT 'pending', created_at VARCHAR(32) NOT NULL,
      UNIQUE KEY idx_users_username (username), UNIQUE KEY idx_users_email (email)
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS contact_messages (
      id VARCHAR(64) PRIMARY KEY, name VARCHAR(255) NOT NULL, email VARCHAR(320) NOT NULL,
      subject VARCHAR(255) NOT NULL, message TEXT NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'New',
      created_at VARCHAR(32) NOT NULL
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS chat_conversations (
      id VARCHAR(64) PRIMARY KEY, donation_id VARCHAR(64) NOT NULL, restaurant_id VARCHAR(64) NOT NULL,
      ngo_id VARCHAR(64) NOT NULL, created_at VARCHAR(32) NOT NULL, updated_at VARCHAR(32) NOT NULL,
      UNIQUE KEY uq_chat_donation_ngo (donation_id, ngo_id),
      KEY idx_chat_conversations_restaurant_updated (restaurant_id, updated_at),
      CONSTRAINT fk_chat_conversation_donation FOREIGN KEY (donation_id) REFERENCES donations(id) ON DELETE CASCADE,
      CONSTRAINT fk_chat_conversation_restaurant FOREIGN KEY (restaurant_id) REFERENCES restaurants(id),
      CONSTRAINT fk_chat_conversation_ngo FOREIGN KEY (ngo_id) REFERENCES ngos(id)
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS chat_messages (
      id VARCHAR(64) PRIMARY KEY, conversation_id VARCHAR(64) NOT NULL, sender_user_id VARCHAR(64) NOT NULL,
      sender_role VARCHAR(20) NOT NULL, sender_name VARCHAR(255) NOT NULL, body TEXT NOT NULL,
      created_at VARCHAR(32) NOT NULL,
      KEY idx_chat_messages_conversation_time (conversation_id, created_at),
      CONSTRAINT fk_chat_message_conversation FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE,
      CONSTRAINT fk_chat_message_sender FOREIGN KEY (sender_user_id) REFERENCES users(id)
    ) {_TAIL}""",
    f"""CREATE TABLE IF NOT EXISTS restaurant_reviews (
      id VARCHAR(64) PRIMARY KEY,
      restaurant_id VARCHAR(64) NOT NULL,
      ngo_id VARCHAR(64) NOT NULL,
      rating INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
      review TEXT,
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      UNIQUE KEY unique_restaurant_ngo (restaurant_id, ngo_id),
      CONSTRAINT fk_review_restaurant FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE,
      CONSTRAINT fk_review_ngo FOREIGN KEY (ngo_id) REFERENCES ngos(id) ON DELETE CASCADE
    ) {_TAIL}""",
]


def _ensure_column(db, table, column, definition):
    """Tiny migration helper for databases created by an older version."""
    found = db.execute(
        "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=? AND COLUMN_NAME=?", (table, column)).fetchone()
    if not found["c"]:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        log.info("Migrated: added %s.%s", table, column)


def _init_schema():
    with connect() as db:
        for statement in SCHEMA:
            db.execute(statement)
        _ensure_column(db, "ngos", "website", "TEXT NULL")
        _ensure_column(db, "foodtrust_reports", "ngo_id", "VARCHAR(64) NULL")
        # Remove failed lookups saved by older versions so they stop counting as trust reports.
        marks = ",".join("?" for _ in FAILED_REPORT_STATES)
        db.execute(f"DELETE FROM foodtrust_reports WHERE state IN ({marks})", FAILED_REPORT_STATES)


# --------------------------------------------------------------------------
# Seeding and admin
# --------------------------------------------------------------------------
def _log_activity(db, kind, message, donation_id, now):
    db.execute("INSERT INTO activity (id,kind,message,donation_id,created_at,`read`) VALUES (?,?,?,?,?,0)",
               (str(uuid.uuid4()), kind, message, donation_id, now))


def _seed_demo_data():
    """Local demo data only. Never runs in production."""
    with connect() as db:
        if db.execute("SELECT 1 AS x FROM restaurants LIMIT 1").fetchone():
            return
        now = _now("minutes")
        r_cols = "id,name,contact,email,phone,location,website,created_at"
        db.execute(f"INSERT INTO restaurants ({r_cols}) VALUES (?,?,?,?,?,?,?,?)",
                   ("demo-restaurant-1", "Green Bowl Kitchen", "Maya Rao", "hello@greenbowl.example", "+91 98765 00001", "Connaught Place, New Delhi", "https://example.com", now))
        db.execute(f"INSERT INTO restaurants ({r_cols}) VALUES (?,?,?,?,?,?,?,?)",
                   ("demo-restaurant-2", "Sunrise Bakery", "Arjun Mehta", "team@sunrise.example", "+91 98765 00002", "Karol Bagh, New Delhi", "https://example.org", now))
        db.execute(f"INSERT INTO restaurants ({r_cols}) VALUES (?,?,?,?,?,?,?,?)",
                   ("demo-restaurant-3", "Singh Sahib", "", "", "+91 11 4560 7359", "Sector 5, Dwarka, New Delhi", "https://singhsahib.in", now))

        n_cols = "id,name,contact,email,phone,location,service_area,mission,categories,capacity,arrangements,need,info_status,created_at,website"
        ngo_sql = f"INSERT INTO ngos ({n_cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        db.execute(ngo_sql, ("demo-ngo-1", "Seva Community Kitchen", "Anita Sharma", "hello@seva.example", "+91 98765 10001", "New Delhi", "Central Delhi", "Serving warm meals to families and older adults.", json.dumps(["Vegetarian", "Bakery", "Packaged"]), 80, "Pickup van; insulated boxes available", "30 vegetarian meals by 2 PM", "Information provided", now, ""))
        db.execute(ngo_sql, ("demo-ngo-2", "Neighbourhood Food Circle", "Kabir Singh", "team@foodcircle.example", "+91 98765 10002", "New Delhi", "Central and North Delhi", "A volunteer-led food redistribution network.", json.dumps(["Vegetarian", "Vegan", "Bakery"]), 45, "Volunteer pickup; please call on arrival", "Bakery items for evening distribution", "Information provided", now, ""))
        db.execute(ngo_sql, ("demo-ngo-3", "Abhyanga Foundation", "", "info@abhyangafoundation.org", "+91 11 4019 5993", "K.G. Marg, New Delhi", "New Delhi", "Works across health, livelihoods, education, social protection and community meal support.", json.dumps(["Vegetarian", "Packaged", "Bakery"]), 60, "Coordinate pickup with the organisation", "Current need not provided", "Information provided", now, "https://www.abhyangafoundation.org"))

        def hours(n):
            return (datetime.now() + timedelta(hours=n)).isoformat(timespec="minutes")

        samples = [
            ("FB-1042", "demo-restaurant-1", "40 vegetarian meal boxes", 40, "meals", "Vegetarian", "Freshly prepared rice, dal and seasonal vegetables.", "Contains dairy; other allergens not confirmed", hours(1), hours(4), "Connaught Place, New Delhi", "Kept covered; collect promptly", "Individual meal boxes", 1, "Use the side entrance"),
            ("FB-1041", "demo-restaurant-2", "Assorted bakery packs", 24, "packs", "Bakery", "A mix of breads and pastries from today's bake.", "May contain gluten, dairy and nuts", hours(2), hours(6), "Karol Bagh, New Delhi", "Store in a clean, dry place", "Paper bags", 0, "Ask for the donation shelf"),
            ("FB-1040", "demo-restaurant-3", "Vegetarian lunch thalis", 32, "meals", "Vegetarian", "Fictional demo listing: a surplus of vegetarian lunch thalis for community pickup.", "Allergen details not confirmed", hours(1), hours(3), "Sector 5, Dwarka, New Delhi", "Collect promptly; handling details to be confirmed", "Meal boxes", 0, "Call the restaurant before arrival"),
        ]
        d_cols = ("id,ref,restaurant_id,title,quantity,unit,category,description,allergens,prepared_at,available_from,"
                  "deadline,location,handling,packaging,refrigeration,contact_instructions,image_url,status,created_at,completed_quantity")
        for (ref, rid, title, qty, unit, cat, desc, allergens, available, deadline, location,
             handling, packaging, fridge, instructions) in samples:
            db.execute(f"INSERT INTO donations ({d_cols}) VALUES ({','.join('?' * 21)})",
                       (str(uuid.uuid4()), ref, rid, title, qty, unit, cat, desc, allergens, available, available,
                        deadline, location, handling, packaging, fridge, instructions, None, "Available", now, 0))

        demo_hash = generate_password_hash("DemoFoodBridge!26")
        for username, email, role, profile_id in (
            ("greenbowl", "hello@greenbowl.example", "restaurant", "demo-restaurant-1"),
            ("seva", "team@seva.example", "ngo", "demo-ngo-1"),
            ("singhsahib", "singhsahib@foodbridge.example", "restaurant", "demo-restaurant-3"),
            ("abhyanga", "abhyanga@foodbridge.example", "ngo", "demo-ngo-3"),
        ):
            db.execute("INSERT IGNORE INTO users (id,username,email,password_hash,role,profile_id,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), username, email, demo_hash, role, profile_id, "approved", now))
    log.warning("Demo data seeded (local only). Demo password is public: do not use in production.")


def _ensure_admin_user(demo_mode):
    """Create the admin once. An existing admin's password is only reset on explicit request."""
    username = os.getenv("FOODBRIDGE_ADMIN_USERNAME", "admin").strip().lower()
    email = os.getenv("FOODBRIDGE_ADMIN_EMAIL", "admin@foodbridge.local").strip().lower()
    password = os.getenv("FOODBRIDGE_ADMIN_PASSWORD", "").strip()
    if not password:
        if not demo_mode:
            raise RuntimeError("Set FOODBRIDGE_ADMIN_PASSWORD so an administrator account can be created.")
        password = "LocalAdmin!2026"
        log.warning("FOODBRIDGE_ADMIN_PASSWORD not set; using the local-only default admin password.")

    existing = one("SELECT id, role FROM users WHERE username=?", (username,))
    with connect() as db:
        if existing:
            if existing["role"] != "admin":
                raise RuntimeError("FOODBRIDGE_ADMIN_USERNAME is already used by a non-admin account.")
            if os.getenv("FOODBRIDGE_RESET_ADMIN_PASSWORD", "false").lower() == "true":
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), existing["id"]))
                log.warning("Admin password was reset from FOODBRIDGE_ADMIN_PASSWORD. Remove FOODBRIDGE_RESET_ADMIN_PASSWORD now.")
            return
        db.execute("INSERT INTO users (id,username,email,password_hash,role,profile_id,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), username, email, generate_password_hash(password), "admin", None, "approved", _now()))
    log.info("Admin account '%s' created.", username)


def init_db():
    cfg = _settings()
    _init_schema()

    # Startup report: if these counts drop to demo-only values after a restart, the database is not persistent.
    info = one("SELECT DATABASE() AS db, VERSION() AS version")
    counts = one("SELECT (SELECT COUNT(*) FROM users) AS users, (SELECT COUNT(*) FROM ngos) AS ngos, "
                 "(SELECT COUNT(*) FROM restaurants) AS restaurants, (SELECT COUNT(*) FROM donations) AS donations")
    log.info("Database: MySQL %s at %s:%s/%s | users=%s ngos=%s restaurants=%s donations=%s",
             info["version"], cfg["host"], cfg["port"], info["db"],
             counts["users"], counts["ngos"], counts["restaurants"], counts["donations"])

    seed_setting = os.getenv("FOODBRIDGE_SEED_DEMO_DATA", "false").strip().lower() in {"1", "true", "yes", "on"}
    if seed_setting and IS_PRODUCTION:
        log.warning("FOODBRIDGE_SEED_DEMO_DATA is ignored in production.")
    demo_mode = seed_setting and not IS_PRODUCTION
    if demo_mode:
        _seed_demo_data()
    _ensure_admin_user(demo_mode)


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
def get_user_by_username(username):
    return one("SELECT * FROM users WHERE username=?", (username.strip(),))


def get_user_by_email(email):
    return one("SELECT * FROM users WHERE email=?", (email.strip(),))


def get_user(user_id):
    return one("SELECT * FROM users WHERE id=?", (user_id,))


def add_user(username, email, password_hash, role, profile_id, status="pending"):
    user_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute("INSERT INTO users (id,username,email,password_hash,role,profile_id,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (user_id, username.strip().lower(), email.strip().lower(), password_hash, role, profile_id, status, _now()))
    return user_id


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------
def create_restaurant(name, contact, email, phone, location, website):
    rid = str(uuid.uuid4())
    with connect() as db:
        db.execute("INSERT INTO restaurants (id,name,contact,email,phone,location,website,created_at) VALUES (?,?,?,?,?,?,?,?)",
                   (rid, name, contact, email, phone, location, website, _now("minutes")))
    return rid


def create_ngo(name, contact, email, phone, location, service_area, mission, categories, capacity,
               arrangements, need, website=""):
    nid = str(uuid.uuid4())
    with connect() as db:
        db.execute("INSERT INTO ngos (id,name,contact,email,phone,location,service_area,mission,categories,capacity,"
                   "arrangements,need,info_status,created_at,website) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (nid, name, contact, email, phone, location, service_area, mission, json.dumps(categories),
                    capacity, arrangements, need, "Information provided", _now("minutes"), website))
    return nid


# --------------------------------------------------------------------------
# Donations and requests
# --------------------------------------------------------------------------
def create_donation(data):
    did, ref = str(uuid.uuid4()), "FB-" + uuid.uuid4().hex[:6].upper()
    now = _now("minutes")
    with connect() as db:
        db.execute(
            "INSERT INTO donations (id,ref,restaurant_id,title,quantity,unit,category,description,allergens,prepared_at,"
            "available_from,deadline,location,handling,packaging,refrigeration,contact_instructions,image_url,status,"
            "created_at,completed_quantity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (did, ref, data["restaurant_id"], data["title"], data["quantity"], data["unit"], data["category"],
             data["description"], data["allergens"], data["prepared_at"], data["available_from"], data["deadline"],
             data["location"], data["handling"], data["packaging"], int(data["refrigeration"]),
             data["contact_instructions"], data.get("image_url"), "Available", now))
        _log_activity(db, "Donation listed", f"{data['title']} is now available ({ref}).", did, now)
    return did, ref


def submit_request(donation_id, ngo_id, quantity, pickup_at, pickup_contact, message):
    now = _now("minutes")
    with connect() as db:
        db.execute("BEGIN")
        donation = db.execute("SELECT * FROM donations WHERE id=? FOR UPDATE", (donation_id,)).fetchone()
        if (not donation or donation["status"] not in ("Available", "Requested")
                or datetime.fromisoformat(donation["deadline"]) <= datetime.now()):
            raise ValueError("This donation is no longer available.")
        if quantity < 1 or quantity > donation["quantity"]:
            raise ValueError("Requested quantity must fit the available amount.")
        if db.execute("SELECT 1 AS x FROM requests WHERE donation_id=? AND ngo_id=? AND status='Pending' LIMIT 1",
                      (donation_id, ngo_id)).fetchone():
            raise ValueError("You already have a pending request for this donation.")
        rid = str(uuid.uuid4())
        db.execute("INSERT INTO requests (id,donation_id,ngo_id,quantity,pickup_at,pickup_contact,message,status,created_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (rid, donation_id, ngo_id, quantity, pickup_at, pickup_contact, message, "Pending", now))
        db.execute("UPDATE donations SET status='Requested' WHERE id=?", (donation_id,))
        _log_activity(db, "New request", f"A new request was submitted for {donation['ref']}.", donation_id, now)
    return rid


def update_request(request_id, action):
    """Accept or decline a request. Returns the new request status (e.g. 'Accepted' or 'Declined')."""
    states = {"accept": ("Accepted", "Accepted"), "decline": ("Declined", "Available"),
              "reject": ("Declined", "Available")}
    if action not in states:
        raise ValueError("Unsupported request action.")
    req_status, donation_status = states[action]
    now = _now("minutes")
    with connect() as db:
        db.execute("BEGIN")
        req = db.execute("SELECT * FROM requests WHERE id=? FOR UPDATE", (request_id,)).fetchone()
        if not req or req["status"] != "Pending":
            raise ValueError("This request is no longer pending.")
        donation = db.execute("SELECT status FROM donations WHERE id=? FOR UPDATE", (req["donation_id"],)).fetchone()
        if not donation or donation["status"] not in ("Available", "Requested"):
            raise ValueError("This donation can no longer accept or decline requests.")
        db.execute("UPDATE requests SET status=? WHERE id=?", (req_status, request_id))
        if req_status == "Accepted":
            db.execute("UPDATE requests SET status='Declined' WHERE donation_id=? AND id<>? AND status='Pending'",
                       (req["donation_id"], request_id))
        elif db.execute("SELECT 1 AS x FROM requests WHERE donation_id=? AND status='Pending' LIMIT 1",
                        (req["donation_id"],)).fetchone():
            donation_status = "Requested"
        db.execute("UPDATE donations SET status=? WHERE id=?", (donation_status, req["donation_id"]))
        _log_activity(db, "Request updated", f"A donation request was {req_status.lower()}.", req["donation_id"], now)
    return req_status


def advance_donation(donation_id, status):
    allowed = {"Picked up": ("Accepted",), "Completed": ("Picked up",),
               "Cancelled": ("Available", "Requested", "Accepted")}
    if status not in allowed:
        raise ValueError("Unsupported status change.")
    now = _now("minutes")
    with connect() as db:
        db.execute("BEGIN")
        current = db.execute("SELECT status,quantity FROM donations WHERE id=? FOR UPDATE", (donation_id,)).fetchone()
        if not current:
            raise ValueError("Donation not found.")
        if current["status"] not in allowed[status]:
            raise ValueError(f"Cannot move {current['status']} to {status}.")

        if status == "Completed":
            # Count what the accepted NGO actually collected, not the whole listing.
            accepted = db.execute("SELECT quantity FROM requests WHERE donation_id=? AND status='Accepted' LIMIT 1",
                                  (donation_id,)).fetchone()
            delivered = accepted["quantity"] if accepted else current["quantity"]
            db.execute("UPDATE donations SET status=?, completed_quantity=? WHERE id=?", (status, delivered, donation_id))
        else:
            db.execute("UPDATE donations SET status=? WHERE id=?", (status, donation_id))
            if status == "Cancelled":
                db.execute("UPDATE requests SET status='Declined' WHERE donation_id=? AND status='Pending'", (donation_id,))
        _log_activity(db, "Donation status", f"Donation status changed to {status}.", donation_id, now)


# --------------------------------------------------------------------------
# FoodTrust reports
# --------------------------------------------------------------------------
def _save(column, owner_id, website, state, evidence):
    """Store only successful lookups. Returns True if a report was saved."""
    if state not in SAVEABLE_REPORT_STATES:
        return False
    with connect() as db:
        db.execute(f"INSERT INTO foodtrust_reports (id,{column},website,state,evidence,created_at) VALUES (?,?,?,?,?,?)",
                   (str(uuid.uuid4()), owner_id, website, state, json.dumps(evidence), _now("microseconds")))
    return True


def _latest(column, owner_id):
    result = one(f"SELECT * FROM foodtrust_reports WHERE {column}=? ORDER BY created_at DESC, id DESC LIMIT 1", (owner_id,))
    if result:
        result["evidence"] = json.loads(result["evidence"])
    return result


def save_report(restaurant_id, website, state, evidence):
    return _save("restaurant_id", restaurant_id, website, state, evidence)


def latest_report(restaurant_id):
    return _latest("restaurant_id", restaurant_id)


def save_ngo_report(ngo_id, website, state, evidence):
    return _save("ngo_id", ngo_id, website, state, evidence)


def latest_ngo_report(ngo_id):
    return _latest("ngo_id", ngo_id)


# --------------------------------------------------------------------------
# Restaurant reviews (written by NGOs)
# --------------------------------------------------------------------------
def ngo_can_review(restaurant_id, ngo_id):
    """True if this NGO has a completed pickup from the restaurant."""
    return bool(one(
        "SELECT 1 AS x FROM donations d JOIN requests q ON q.donation_id=d.id "
        "WHERE d.restaurant_id=? AND q.ngo_id=? AND q.status='Accepted' AND d.status='Completed' LIMIT 1",
        (restaurant_id, ngo_id)))


def upsert_review(restaurant_id, ngo_id, rating, review=""):
    """Create or update this NGO's single review of a restaurant."""
    if not one("SELECT id FROM restaurants WHERE id=?", (restaurant_id,)):
        raise LookupError("Restaurant not found.")
    if REVIEWS_REQUIRE_PICKUP and not ngo_can_review(restaurant_id, ngo_id):
        raise PermissionError("You can review a restaurant after completing a pickup from them.")
    now = _now("microseconds")
    with connect() as db:
        db.execute(
            "INSERT INTO restaurant_reviews (id,restaurant_id,ngo_id,rating,review,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON DUPLICATE KEY UPDATE rating=VALUES(rating), review=VALUES(review), updated_at=VALUES(updated_at)",
            (str(uuid.uuid4()), restaurant_id, ngo_id, rating, review, now, now))


def delete_review(restaurant_id, ngo_id):
    with connect() as db:
        cursor = db.execute("DELETE FROM restaurant_reviews WHERE restaurant_id=? AND ngo_id=?", (restaurant_id, ngo_id))
        return cursor.rowcount > 0


def reviews_for_restaurant(restaurant_id, limit=50):
    return rows(
        "SELECT r.id, r.rating, r.review, r.created_at, r.updated_at, n.id AS ngo_id, n.name AS ngo_name "
        "FROM restaurant_reviews r JOIN ngos n ON n.id=r.ngo_id "
        "WHERE r.restaurant_id=? ORDER BY r.updated_at DESC LIMIT ?", (restaurant_id, limit))


def _summary(row):
    return {"count": int(row["count"]), "average": round(float(row["average"]), 1) if row["average"] is not None else None}


def review_summary(restaurant_id):
    row = one("SELECT COUNT(*) AS count, AVG(rating) AS average FROM restaurant_reviews WHERE restaurant_id=?", (restaurant_id,))
    return _summary(row)


def review_summaries():
    """Average rating and count for every reviewed restaurant, in one query: {restaurant_id: {...}}."""
    return {r["restaurant_id"]: _summary(r) for r in rows(
        "SELECT restaurant_id, COUNT(*) AS count, AVG(rating) AS average FROM restaurant_reviews GROUP BY restaurant_id")}
