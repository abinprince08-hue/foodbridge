"""FoodBridge backend API for donation matching and FoodTrust research."""
from __future__ import annotations

import json
import os
import re
import uuid

from datetime import datetime
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, session, render_template
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# Load environment settings before importing database.py, which reads DB_BACKEND.
import database as db
from tinyfish_client import research_ngo, research_restaurant, validate_public_url

app = Flask(__name__)
IS_PRODUCTION = bool(os.getenv("RENDER")) or os.getenv("APP_ENV", "").lower() == "production"
secret_key = os.getenv("FOODBRIDGE_SECRET_KEY", "").strip()
admin_password = os.getenv("FOODBRIDGE_ADMIN_PASSWORD", "").strip()
if IS_PRODUCTION and (not secret_key or secret_key.startswith("replace-") or len(secret_key) < 32):
    raise RuntimeError("Set a unique FOODBRIDGE_SECRET_KEY of at least 32 characters in your hosting environment.")
if IS_PRODUCTION and (not admin_password or admin_password.startswith("replace-") or len(admin_password) < 12):
    raise RuntimeError("Set a strong FOODBRIDGE_ADMIN_PASSWORD of at least 12 characters before deploying.")
app.secret_key = secret_key or "local-only-change-this-secret"

frontend_origins = [
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5500,http://127.0.0.1:5500",
    ).split(",")
    if origin.strip()
]
CORS(app, resources={r"/api/*": {"origins": frontend_origins}}, supports_credentials=True)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.getenv(
        "SESSION_COOKIE_SAMESITE", "None" if IS_PRODUCTION else "Lax"
    ),
    SESSION_COOKIE_SECURE=IS_PRODUCTION or os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    JSON_SORT_KEYS=False,
)

db.init_db()


def api_error(message, status=400):

    return jsonify({"error": str(message)}), status





def body_json():

    return request.get_json(silent=True) or {}





def require_roles(*roles):

    def decorate(function):

        @wraps(function)

        def wrapped(*args, **kwargs):

            user = db.get_user(session.get("user_id", "")) if session.get("user_id") else None

            if not user: return api_error("Please log in to continue.", 401)

            if user["role"] != "admin" and user["status"] != "approved":

                return api_error("Your registration is awaiting admin approval." if user["status"] == "pending" else "This registration was rejected. Contact FoodBridge support.", 403)

            if roles and user["role"] not in roles: return api_error("You do not have permission to do that.", 403)

            g.current_user = user

            return function(*args, **kwargs)

        return wrapped

    return decorate





def public_user(user):

    if not user: return None

    result = {key: user[key] for key in ("id", "username", "email", "role", "profile_id", "status")}

    if user["role"] == "restaurant": result["profile"] = db.one("SELECT id,name,location,website FROM restaurants WHERE id=?", (user["profile_id"],))

    elif user["role"] == "ngo": result["profile"] = db.one("SELECT id,name,location,service_area,need,capacity,info_status,website FROM ngos WHERE id=?", (user["profile_id"],))

    return result





@app.get("/api/auth/me")

def auth_me():

    user = db.get_user(session.get("user_id", "")) if session.get("user_id") else None

    return jsonify({"user": public_user(user)})





@app.post("/api/auth/login")

def auth_login():

    data = body_json()

    username = str(data.get("username", "")).strip().lower()

    password = str(data.get("password", ""))

    user = db.get_user_by_username(username) if username else None

    if not user or not check_password_hash(user["password_hash"], password):

        return api_error("Username or password is incorrect.", 401)

    session.clear()

    session["user_id"] = user["id"]

    return jsonify({"user": public_user(user)})





@app.post("/api/auth/logout")

def auth_logout():

    session.clear()

    return jsonify({"message": "You are logged out."})





@app.post("/api/auth/register")

def auth_register():

    data = body_json()

    role = str(data.get("role", "")).lower()

    email = str(data.get("email", "")).strip().lower()

    username = str(data.get("username", "")).strip().lower()

    password = str(data.get("password", ""))

    if role not in ("restaurant", "ngo"): return api_error("Choose restaurant or NGO registration.")

    if not re.fullmatch(r"[a-z0-9\_.-]{3,32}", username): return api_error("Username must be 3–32 characters using letters, numbers, dots, underscores or hyphens.")

    if not re.fullmatch(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email): return api_error("Enter a valid email address.")

    if len(password) < 10: return api_error("Use a password with at least 10 characters.")

    if db.get_user_by_username(username): return api_error("That username is already taken.", 409)

    if db.get_user_by_email(email): return api_error("An account with this email already exists.", 409)

    try:

        if role == "restaurant":

            name, location = str(data.get("name", "")).strip(), str(data.get("location", "")).strip()

            if not name or not location: return api_error("Restaurant name and location are required.")

            website = str(data.get("website", "")).strip()

            if website:

                try: website = validate_public_url(website)

                except ValueError as exc: return api_error(exc)

            profile_id = db.create_restaurant(name, data.get("contact", ""), email, data.get("phone", ""), location, website)

        else:

            name, location, area = (str(data.get(key, "")).strip() for key in ("name", "location", "service_area"))

            if not name or not location or not area: return api_error("Organisation name, location and service area are required.")

            categories = data.get("categories", [])

            if not isinstance(categories, list): return api_error("Food categories must be a list.")

            try: capacity = int(data.get("capacity", 1))

            except (TypeError, ValueError): return api_error("Meal capacity must be a whole number.")

            if capacity <= 0: return api_error("Meal capacity must be greater than zero.")

            website = str(data.get("website", "")).strip()

            if website:

                try: website = validate_public_url(website)

                except ValueError as exc: return api_error(exc)

            profile_id = db.create_ngo(name, data.get("contact", ""), email, data.get("phone", ""), location, area,

              data.get("mission", ""), categories, capacity, data.get("arrangements", ""), data.get("need", ""), website)

        user_id = db.add_user(username, email, generate_password_hash(password), role, profile_id, "pending")

    except Exception as exc:

        return api_error("Could not create this account. Check whether the email is already registered.", 409)

    session.clear()

    session["user_id"] = user_id

    user = db.get_user(user_id)

    return jsonify({"user": public_user(user), "message": "Registration submitted. An administrator must approve it before you can post or request donations."}), 201





def enriched_donations(sql="", params=()):

    base = """SELECT d.*, r.name restaurant_name, r.website restaurant_website,

      CASE WHEN EXISTS(SELECT 1 FROM foodtrust_reports f WHERE f.restaurant_id=r.id) THEN 1 ELSE 0 END trust_available

      FROM donations d JOIN restaurants r ON r.id=d.restaurant_id """

    return db.rows(base + sql, params)





@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/health")
def health():
    try:
        db.one("SELECT 1 AS ok")
        return jsonify({"status": "ok", "database": "connected"})
    except Exception:
        app.logger.exception("Health check database query failed")
        return jsonify({"status": "error", "database": "unavailable"}), 503





@app.get("/api/bootstrap")

def bootstrap():

    stats = db.one("""SELECT COUNT(*) posted,

      SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) completed,

      SUM(CASE WHEN status='Completed' THEN completed_quantity ELSE 0 END) meals

      FROM donations""") or {}

    organisations = db.one("SELECT (SELECT COUNT(*) FROM restaurants)+(SELECT COUNT(*) FROM ngos) AS total")

    latest = enriched_donations("ORDER BY d.created_at DESC LIMIT 4")

    return jsonify({

        "stats": {"posted": stats.get("posted") or 0, "completed": stats.get("completed") or 0,

                  "meals": stats.get("meals") or 0, "organisations": organisations["total"] or 0},

        "donations": latest,

        "restaurants": db.rows("SELECT id,name,location,website FROM restaurants ORDER BY name"),

        "ngos": db.rows("SELECT id,name,location,service_area,mission,categories,capacity,arrangements,need,info_status,website FROM ngos ORDER BY name"),

        "reports": {item["id"]: db.latest_report(item["id"]) for item in db.rows("SELECT id FROM restaurants")},

        "ngo_reports": {item["id"]: db.latest_ngo_report(item["id"]) for item in db.rows("SELECT id FROM ngos")},

    })





@app.get("/api/admin/registrations")

@require_roles("admin")

def admin_registrations():

    pending = db.rows("SELECT id,username,email,role,profile_id,status,created_at FROM users WHERE status='pending' ORDER BY created_at")

    rejected = db.rows("SELECT id,username,email,role,profile_id,status,created_at FROM users WHERE status='rejected' ORDER BY created_at DESC LIMIT 20")

    for user in pending + rejected:

        if user["role"] == "restaurant": user["profile"] = db.one("SELECT name,contact,email,phone,location,website FROM restaurants WHERE id=?", (user["profile_id"],))

        else: user["profile"] = db.one("SELECT name,contact,email,phone,location,service_area,mission,categories,capacity,arrangements,need,info_status,website FROM ngos WHERE id=?", (user["profile_id"],))

    return jsonify({"pending": pending, "rejected": rejected})





@app.post("/api/admin/registrations/<user_id>")

@require_roles("admin")

def admin_review_registration(user_id):

    action = body_json().get("action")

    if action not in ("approve", "reject"): return api_error("Choose approve or reject.")

    status = "approved" if action == "approve" else "rejected"

    with db.connect() as conn:

        cursor = conn.execute("UPDATE users SET status=? WHERE id=? AND role IN ('restaurant','ngo')", (status, user_id))

        if cursor.rowcount == 0: return api_error("Registration not found.", 404)

    return jsonify({"message": f"Registration {action}d."})





@app.get("/api/admin/organizations")

@require_roles("admin")

def admin_organizations():

    restaurants = db.rows("""SELECT r.*,u.username,u.status account_status FROM restaurants r

      LEFT JOIN users u ON u.profile_id=r.id AND u.role='restaurant' ORDER BY r.name""")

    ngos = db.rows("""SELECT n.*,u.username,u.status account_status FROM ngos n

      LEFT JOIN users u ON u.profile_id=n.id AND u.role='ngo' ORDER BY n.name""")

    return jsonify({"restaurants": restaurants, "ngos": ngos})





@app.get("/api/admin/contact-messages")

@require_roles("admin")

def admin_contact_messages():

    return jsonify(db.rows("SELECT * FROM contact_messages ORDER BY created_at DESC"))





@app.patch("/api/admin/contact-messages/<message_id>")

@require_roles("admin")

def update_contact_message(message_id):

    status = body_json().get("status")

    if status not in ("New", "Reviewed", "Closed"): return api_error("Choose New, Reviewed or Closed.")

    with db.connect() as conn:

        cursor = conn.execute("UPDATE contact_messages SET status=? WHERE id=?", (status,message_id))

        if cursor.rowcount == 0: return api_error("Message not found.", 404)

    return jsonify({"message": "Contact message updated."})





@app.post("/api/contact")

def contact_us():

    data = body_json()

    name, email, subject, message = (str(data.get(key, "")).strip() for key in ("name", "email", "subject", "message"))

    if not name or not re.fullmatch(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or not subject or not message:

        return api_error("Enter your name, a valid email, a subject and a message.")

    if len(message) > 5000: return api_error("Please keep your message under 5,000 characters.")

    with db.connect() as conn:

        conn.execute("INSERT INTO contact_messages (id,name,email,subject,message,status,created_at) VALUES (?,?,?,?,?,'New',?)",

                     (uuid.uuid4().hex,name,email,subject,message,datetime.now().isoformat(timespec="seconds")))

    return jsonify({"message": "Thanks for contacting FoodBridge. Your message has been saved."}), 201





@app.get("/api/foodtrust/status")

def foodtrust_status():

    return jsonify({"provider": "TinyFish Search + Fetch", "configured": bool(os.getenv("TINYFISH_API_KEY", "").strip()),

                    "backend": "Python Flask", "message": "Live research is available." if os.getenv("TINYFISH_API_KEY", "").strip() else "Set TINYFISH_API_KEY to enable live research."})





@app.get("/api/donations")

def list_donations():

    items = enriched_donations("ORDER BY d.created_at DESC")

    return jsonify(items)





@app.get("/api/donations/<donation_id>")

def donation_detail(donation_id):

    item = db.one("""SELECT d.*, r.name restaurant_name,r.location restaurant_location,r.website,

      r.id restaurant_id

      FROM donations d JOIN restaurants r ON r.id=d.restaurant_id WHERE d.id=?""", (donation_id,))

    if not item: return api_error("Donation not found.", 404)

    item["requests"] = []

    viewer = db.get_user(session.get("user_id", "")) if session.get("user_id") else None

    if viewer and viewer["role"] == "restaurant" and viewer["profile_id"] == item["restaurant_id"]:

        item["requests"] = db.rows("""SELECT q.*, n.name ngo_name,n.location ngo_location,n.contact ngo_contact

          FROM requests q JOIN ngos n ON n.id=q.ngo_id WHERE q.donation_id=? ORDER BY q.created_at DESC""", (donation_id,))

    elif viewer and viewer["role"] == "ngo":

        item["requests"] = db.rows("""SELECT q.*, n.name ngo_name,n.location ngo_location,n.contact ngo_contact

          FROM requests q JOIN ngos n ON n.id=q.ngo_id WHERE q.donation_id=? AND q.ngo_id=? ORDER BY q.created_at DESC""", (donation_id,viewer["profile_id"]))

    report = db.latest_report(item["restaurant_id"])

    item["foodtrust"] = report

    return jsonify(item)





@app.get("/api/ngos")

def list_ngos():

    return jsonify(db.rows("SELECT id,name,location,service_area,mission,categories,capacity,arrangements,need,info_status,website FROM ngos ORDER BY name"))





@app.patch("/api/ngos/<ngo_id>/need")
@require_roles("ngo")
def update_ngo_need(ngo_id):
    if db.get_user(session.get("user_id", ""))["profile_id"] != ngo_id:
        return api_error("You can only update your own NGO requirement.", 403)

    data = body_json()

    try: capacity = int(data.get("capacity", 0))

    except (TypeError, ValueError): return api_error("Capacity must be a whole number.")

    if capacity <= 0: return api_error("Capacity must be greater than zero.")

    with db.connect() as conn:

        cursor = conn.execute("UPDATE ngos SET need=?,service_area=?,capacity=? WHERE id=?",

                              (str(data.get("need", "")).strip(), str(data.get("service_area", "")).strip(), capacity, ngo_id))

        if cursor.rowcount == 0: return api_error("NGO profile not found.", 404)

    return jsonify({"message": "Requirement updated."})





@app.post("/api/restaurants")

@require_roles("admin")

def create_restaurant():

    data = body_json()

    name, location = str(data.get("name", "")).strip(), str(data.get("location", "")).strip()

    if not name or not location: return api_error("Restaurant name and location are required.")

    website = str(data.get("website", "")).strip()

    if website:

        try: website = validate_public_url(website)

        except ValueError as exc: return api_error(exc)

    rid = db.create_restaurant(name, data.get("contact", ""), data.get("email", ""), data.get("phone", ""), location, website)

    return jsonify({"id": rid, "message": "Profile created as information provided."}), 201





@app.post("/api/ngos")

@require_roles("admin")

def create_ngo():

    data = body_json()

    name, location, area = (str(data.get(key, "")).strip() for key in ("name", "location", "service_area"))

    if not all((name, location, area)): return api_error("Organisation name, location and service area are required.")

    categories = data.get("categories", [])

    if not isinstance(categories, list): return api_error("Categories must be a list.")

    try: capacity = int(data.get("capacity", 1))

    except (TypeError, ValueError): return api_error("Capacity must be a whole number.")

    if capacity <= 0: return api_error("Capacity must be greater than zero.")

    nid = db.create_ngo(name, data.get("contact", ""), data.get("email", ""), data.get("phone", ""), location, area,

                        data.get("mission", ""), categories, capacity, data.get("arrangements", ""), data.get("need", ""))

    return jsonify({"id": nid, "message": "Profile created as information provided."}), 201





@app.post("/api/donations")

@require_roles("restaurant")

def create_donation():

    data = body_json()

    required = ("restaurant_id", "title", "quantity", "unit", "category", "description", "deadline", "location")

    missing = [key for key in required if not str(data.get(key, "")).strip()]

    if missing: return api_error("Complete all required fields: " + ", ".join(missing))

    if data["restaurant_id"] != g.current_user["profile_id"]: return api_error("You can only publish donations for your own restaurant.", 403)

    restaurant = db.one("SELECT id FROM restaurants WHERE id=?", (data["restaurant_id"],))

    if not restaurant: return api_error("Restaurant profile not found.", 404)

    try:

        quantity = int(data["quantity"])

        deadline = datetime.fromisoformat(data["deadline"])

        available = datetime.fromisoformat(data.get("available_from") or datetime.now().isoformat(timespec="minutes"))

        prepared = datetime.fromisoformat(data.get("prepared_at") or datetime.now().isoformat(timespec="minutes"))

    except (TypeError, ValueError): return api_error("Enter a valid quantity and date/time.")

    if quantity <= 0: return api_error("Quantity must be greater than zero.")

    if deadline <= datetime.now(): return api_error("Pickup deadline must be in the future.")

    if available > deadline: return api_error("Available-from time must be before the pickup deadline.")

    image_url = str(data.get("image_url", "")).strip()

    if image_url:

        from urllib.parse import urlparse

        parsed = urlparse(image_url)

        if parsed.scheme not in ("https", "http") or not parsed.netloc: return api_error("Photo URL must be a valid http or https link.")

    record = {"restaurant_id": data["restaurant_id"], "title": str(data["title"]).strip(), "quantity": quantity,

      "unit": data["unit"], "category": data["category"], "description": str(data["description"]).strip(),

      "allergens": data.get("allergens", ""), "prepared_at": prepared.isoformat(timespec="minutes"),

      "available_from": available.isoformat(timespec="minutes"), "deadline": deadline.isoformat(timespec="minutes"),

      "location": str(data["location"]).strip(), "handling": data.get("handling", ""), "packaging": data.get("packaging", ""),

      "refrigeration": bool(data.get("refrigeration", False)), "contact_instructions": data.get("contact_instructions", ""),

      "image_url": image_url or None}

    donation_id, ref = db.create_donation(record)

    return jsonify({"id": donation_id, "ref": ref, "message": "Donation published."}), 201





@app.post("/api/donations/<donation_id>/requests")

@require_roles("ngo")

def submit_request(donation_id):

    data = body_json()

    if data.get("ngo_id") != g.current_user["profile_id"]: return api_error("You can only request donations for your own organisation.", 403)

    try:

        quantity = int(data.get("quantity", 0))

        pickup_at = datetime.fromisoformat(data.get("pickup_at", ""))

        if pickup_at <= datetime.now(): return api_error("Expected pickup time must be in the future.")

        if not db.one("SELECT id FROM ngos WHERE id=?", (data.get("ngo_id", ""),)): return api_error("NGO profile not found.", 404)

        request_id = db.submit_request(donation_id, data.get("ngo_id", ""), quantity,

          pickup_at.isoformat(timespec="minutes"), data.get("pickup_contact", ""), data.get("message", ""))

    except (ValueError, TypeError) as exc: return api_error(exc)

    return jsonify({"id": request_id, "message": "Request sent. The restaurant must approve it."}), 201





@app.post("/api/requests/<request_id>/<action>")

@require_roles("restaurant")

def request_action(request_id, action):

    owner = db.one("SELECT d.restaurant_id FROM requests q JOIN donations d ON d.id=q.donation_id WHERE q.id=?", (request_id,))

    if not owner: return api_error("Request not found.", 404)

    if owner["restaurant_id"] != g.current_user["profile_id"]: return api_error("You can only review requests for your own restaurant.", 403)

    try: db.update_request(request_id, action)

    except ValueError as exc: return api_error(exc)

    return jsonify({"message": f"Request {action}ed."})





@app.post("/api/donations/<donation_id>/status")

@require_roles("restaurant", "ngo")

def donation_status(donation_id):

    status = body_json().get("status", "")

    donation = db.one("SELECT * FROM donations WHERE id=?", (donation_id,))

    if not donation: return api_error("Donation not found.", 404)

    if g.current_user["role"] == "restaurant" and donation["restaurant_id"] != g.current_user["profile_id"]:

        return api_error("You can only update your own donation.", 403)

    if g.current_user["role"] == "ngo":

        accepted = db.one("SELECT id FROM requests WHERE donation_id=? AND ngo_id=? AND status='Accepted'", (donation_id,g.current_user["profile_id"]))

        if not accepted or status not in ("Picked up", "Completed"): return api_error("Only the accepted NGO can confirm pickup or completion.", 403)

    try: db.advance_donation(donation_id, status)

    except ValueError as exc: return api_error(exc)

    return jsonify({"message": f"Donation status changed to {status}."})





@app.post("/api/foodtrust")

@require_roles("restaurant", "admin")

def foodtrust():

    data = body_json()

    restaurant = db.one("SELECT * FROM restaurants WHERE id=?", (data.get("restaurant_id"),))

    if not restaurant: return api_error("Restaurant profile not found.", 404)

    if g.current_user["role"] == "restaurant" and restaurant["id"] != g.current_user["profile_id"]: return api_error("You can research only your own restaurant profile.", 403)

    try: website = validate_public_url(data.get("website") or restaurant["website"] or "")

    except ValueError as exc: return api_error(exc)

    report = research_restaurant(website, restaurant["name"])

    db.save_report(restaurant["id"], website, report["state"], report)

    return jsonify(report)





@app.post("/api/foodtrust/ngos")

@require_roles("admin")

def foodtrust_ngo():

    data = body_json()

    ngo = db.one("SELECT * FROM ngos WHERE id=?", (data.get("ngo_id"),))

    if not ngo: return api_error("NGO profile not found.", 404)

    website = str(data.get("website") or ngo["website"] or "").strip()

    if website:

        try: website = validate_public_url(website)

        except ValueError as exc: return api_error(exc)

    report = research_ngo(ngo["name"], website)

    db.save_ngo_report(ngo["id"], website, report["state"], report)

    return jsonify(report)





@app.get("/api/dashboard/<role>/<profile_id>")

@require_roles("restaurant", "ngo")

def dashboard(role, profile_id):

    if g.current_user["role"] != role or g.current_user["profile_id"] != profile_id:

        return api_error("You can only view your own dashboard.", 403)

    if role == "restaurant":

        profile = db.one("SELECT * FROM restaurants WHERE id=?", (profile_id,))

        items = enriched_donations("WHERE d.restaurant_id=? ORDER BY d.created_at DESC", (profile_id,))

        requests = db.rows("""SELECT q.*,d.ref,d.title,n.name ngo_name,n.location ngo_location,n.contact ngo_contact

          FROM requests q JOIN donations d ON d.id=q.donation_id JOIN ngos n ON n.id=q.ngo_id

          WHERE d.restaurant_id=? ORDER BY q.created_at DESC""", (profile_id,))

    elif role == "ngo":

        profile = db.one("SELECT * FROM ngos WHERE id=?", (profile_id,))

        items = []

        requests = db.rows("""SELECT q.*,d.ref,d.title,d.status donation_status,r.name restaurant_name

          FROM requests q JOIN donations d ON d.id=q.donation_id JOIN restaurants r ON r.id=d.restaurant_id

          WHERE q.ngo_id=? ORDER BY q.created_at DESC""", (profile_id,))

    else: return api_error("Unknown dashboard role.")

    if not profile: return api_error("Profile not found.", 404)

    return jsonify({"profile": profile, "donations": items, "requests": requests})





if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
