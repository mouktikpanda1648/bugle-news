import os
import sqlite3
import json
import random
import uuid
from datetime import datetime, timedelta

import bcrypt
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# Load .env BEFORE reading any os.getenv() values below.
load_dotenv()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bugle.db")

DEBUG = os.getenv("DEBUG", "True").strip().lower() in ("1", "true", "yes")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587) or 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

OTP_RATE_LIMIT_PER_WINDOW = int(os.getenv("OTP_RATE_LIMIT_PER_WINDOW", 5))
OTP_RATE_LIMIT_WINDOW_MINUTES = int(os.getenv("OTP_RATE_LIMIT_WINDOW_MINUTES", 15))
OTP_MAX_VERIFY_ATTEMPTS = int(os.getenv("OTP_MAX_VERIFY_ATTEMPTS", 5))

EDITOR_INVITE_CODE = os.getenv("EDITOR_INVITE_CODE", "")


# ---------------------------------------------------------------------------
# Connection & password helpers
# ---------------------------------------------------------------------------

def get_connection():
    """Returns a SQLite connection with dict-like row access and WAL mode enabled."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    pwd_bytes = plain_password.encode("utf-8")[:72]
    try:
        return bcrypt.checkpw(pwd_bytes, hashed_password.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Email delivery (optional — the app works fully without SMTP configured)
# ---------------------------------------------------------------------------

def send_real_email_otp(target_email: str, code: str) -> bool:
    """Sends a genuine email via SMTP if credentials are provided. Never raises."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = f"The Daily Bugle <{SMTP_USER}>"
        msg["To"] = target_email
        msg["Subject"] = f"Your Daily Bugle Press Clearance Code: {code}"
        body = f"""
        <html>
          <body style="font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 20px;">
            <h2 style="color: #e11d48; margin-bottom: 5px;">THE DAILY BUGLE</h2>
            <p style="font-size: 14px; color: #94a3b8;">Civic Intelligence Network Verification</p>
            <hr style="border: 0; border-top: 1px solid #334155; margin: 15px 0;" />
            <p>Your 6-digit newsroom clearance code is:</p>
            <div style="font-size: 32px; font-weight: bold; letter-spacing: 6px; color: #38bdf8; padding: 10px 0;">
              {code}
            </div>
            <p style="font-size: 12px; color: #64748b;">This code expires in 5 minutes. If you did not request this pass, disregard this transmission.</p>
          </body>
        </html>
        """
        msg.attach(MIMEText(body, "html"))
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=8)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, target_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"[SMTP Error] Could not dispatch email via SMTP: {e}")
        return False


# ---------------------------------------------------------------------------
# OTP lifecycle: request (rate-limited), verify (attempt-limited)
# ---------------------------------------------------------------------------

def create_otp_token(identifier: str, purpose: str = "LOGIN", expiry_minutes: int = 5):
    """
    Generates and stores a 6-digit OTP. Returns a dict:
      {"ok": True, "code": "123456", "delivered_via": "email"|"terminal", "dev_code": "123456"|None}
      {"ok": False, "error": "..."}
    dev_code is only populated when DEBUG=True, so the frontend can display it
    directly — this is what makes the flow work reliably at a hackathon with
    zero external email/SMS infrastructure configured.
    """
    clean_id = identifier.strip().lower()
    conn = get_connection()
    cursor = conn.cursor()

    # Rate limit: cap OTP requests per identifier within a rolling window.
    window_start = (datetime.utcnow() - timedelta(minutes=OTP_RATE_LIMIT_WINDOW_MINUTES)).isoformat()
    cursor.execute("""
        SELECT COUNT(*) FROM otp_tokens
        WHERE identifier = ? AND purpose = ? AND created_at > ?
    """, (clean_id, purpose, window_start))
    recent_count = cursor.fetchone()[0]
    if recent_count >= OTP_RATE_LIMIT_PER_WINDOW:
        conn.close()
        return {"ok": False, "error": "Too many code requests. Please wait a few minutes and try again."}

    otp_code = f"{random.randint(100000, 999999)}"
    now = datetime.utcnow()
    expires_at = (now + timedelta(minutes=expiry_minutes)).isoformat()

    cursor.execute("""
        UPDATE otp_tokens SET is_used = 1
        WHERE identifier = ? AND purpose = ? AND is_used = 0
    """, (clean_id, purpose))

    cursor.execute("""
        INSERT INTO otp_tokens (identifier, otp_code, purpose, expires_at, is_used, attempt_count, created_at)
        VALUES (?, ?, ?, ?, 0, 0, ?)
    """, (clean_id, otp_code, purpose, expires_at, now.isoformat()))
    conn.commit()
    conn.close()

    print("\n=======================================================")
    print(" [THE DAILY BUGLE DISPATCH - SECURE ACCESS TOKEN]")
    print(f" Target: {clean_id} | Purpose: {purpose}")
    print(f" Verification Code: >>> {otp_code} <<< (Valid for {expiry_minutes} mins)")
    print("=======================================================\n")

    delivered_via = "terminal"
    if "@" in clean_id:
        if send_real_email_otp(clean_id, otp_code):
            delivered_via = "email"

    return {
        "ok": True,
        "delivered_via": delivered_via,
        "dev_code": otp_code if DEBUG else None,
    }


def verify_otp_token(identifier: str, code: str, purpose: str = "LOGIN") -> dict:
    """Checks validity, expiry, and attempt count of the entered token."""
    clean_id = identifier.strip().lower()
    clean_code = code.strip()
    now_iso = datetime.utcnow().isoformat()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM otp_tokens
        WHERE identifier = ? AND purpose = ? AND is_used = 0 AND expires_at > ?
        ORDER BY id DESC LIMIT 1
    """, (clean_id, purpose, now_iso))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return {"ok": False, "error": "No active code found. Please request a new one."}

    if row["attempt_count"] >= OTP_MAX_VERIFY_ATTEMPTS:
        cursor.execute("UPDATE otp_tokens SET is_used = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        return {"ok": False, "error": "Too many incorrect attempts. Please request a new code."}

    if row["otp_code"] != clean_code:
        cursor.execute("UPDATE otp_tokens SET attempt_count = attempt_count + 1 WHERE id = ?", (row["id"],))
        conn.commit()
        remaining = OTP_MAX_VERIFY_ATTEMPTS - (row["attempt_count"] + 1)
        conn.close()
        return {"ok": False, "error": f"Incorrect code. {max(remaining, 0)} attempt(s) remaining."}

    cursor.execute("UPDATE otp_tokens SET is_used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# User account management
# ---------------------------------------------------------------------------

def find_user_by_identifier(identifier: str):
    clean_id = identifier.strip().lower()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ? OR phone = ? OR username = ?", (clean_id, clean_id, clean_id))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def sync_or_create_user(identifier: str, editor_invite_code: str = ""):
    """Finds an existing account for this identifier, or provisions a new one.

    Role is never inferred from the identifier text (that was a privilege
    escalation bug — anyone could type "editor" into their email). Editor
    clearance is granted only when a valid EDITOR_INVITE_CODE is supplied.
    """
    existing = find_user_by_identifier(identifier)
    if existing:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), existing["id"]))
        conn.commit()
        conn.close()
        return existing

    clean_id = identifier.strip().lower()
    is_email = "@" in clean_id
    user_id = f"usr_{uuid.uuid4().hex[:10]}"
    username_base = clean_id.split("@")[0] if is_email else clean_id
    username = username_base

    conn = get_connection()
    cursor = conn.cursor()

    # Ensure username uniqueness
    suffix = 0
    while True:
        cursor.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        if not cursor.fetchone():
            break
        suffix += 1
        username = f"{username_base}{suffix}"

    role = "CITIZEN"
    if editor_invite_code and EDITOR_INVITE_CODE and editor_invite_code.strip() == EDITOR_INVITE_CODE:
        role = "EDITOR"

    now = datetime.utcnow().isoformat()
    cursor.execute("""
        INSERT INTO users (id, username, name, email, phone, password_hash, role, is_verified,
                            trust_score, strike_count, is_quarantined, created_at, last_login_at)
        VALUES (?, ?, ?, ?, ?, '', ?, 1, 0.50, 0, 0, ?, ?)
    """, (
        user_id, username, username,
        clean_id if is_email else "",
        clean_id if not is_email else "",
        role, now, now
    ))
    conn.commit()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    new_user = dict(cursor.fetchone())
    conn.close()
    return new_user


# ---------------------------------------------------------------------------
# Schema & seed data
# ---------------------------------------------------------------------------

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        phone TEXT,
        password_hash TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'CITIZEN',
        is_verified INTEGER DEFAULT 0,
        trust_score REAL DEFAULT 0.50,
        strike_count INTEGER DEFAULT 0,
        is_quarantined INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        last_login_at TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS otp_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identifier TEXT NOT NULL,
        otp_code TEXT NOT NULL,
        purpose TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        is_used INTEGER DEFAULT 0,
        attempt_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        full_article TEXT NOT NULL,
        primary_image TEXT DEFAULT '',
        category TEXT NOT NULL,
        latitude REAL DEFAULT 0.0,
        longitude REAL DEFAULT 0.0,
        is_spatial INTEGER DEFAULT 1,
        confidence_score INTEGER DEFAULT 50,
        status TEXT DEFAULT 'REVIEW',
        explainability_json TEXT NOT NULL,
        report_count INTEGER DEFAULT 1,
        source_diversity INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        incident_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        latitude REAL DEFAULT 0.0,
        longitude REAL DEFAULT 0.0,
        is_spatial INTEGER DEFAULT 1,
        image_url TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
    );
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_coords ON incidents(latitude, longitude);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_incident ON reports(incident_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_otp_lookup ON otp_tokens(identifier, purpose, is_used);")

    now = datetime.utcnow().isoformat()

    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
        INSERT INTO users (id, username, name, email, phone, password_hash, role, is_verified, trust_score, strike_count, is_quarantined, created_at, last_login_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "usr_editor", "editor", "J. Jonah Jameson", "editor@dailybugle.com", "+919876543210",
            "", "EDITOR", 1, 1.0, 0, 0, now, now
        ))
        cursor.execute("""
        INSERT INTO users (id, username, name, email, phone, password_hash, role, is_verified, trust_score, strike_count, is_quarantined, created_at, last_login_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "usr_peter", "peter", "Peter Parker", "peter@dailybugle.com", "+919876543211",
            "", "CITIZEN", 1, 0.95, 0, 0, now, now
        ))

    cursor.execute("SELECT COUNT(*) FROM incidents")
    if cursor.fetchone()[0] == 0:
        exp_fire = json.dumps([
            {"label": "Independent eyewitness accounts", "value": "+24"},
            {"label": "Direct photo evidence matches scene", "value": "+18"},
            {"label": "Geographic cluster tight within 120m", "value": "+15"}
        ])
        article_fire = (
            "Emergency response units arrived at KIIT Square at approximately 10:45 AM following reports "
            "of a sudden explosion at the secondary power distribution unit. Heavy smoke billowed across the main intersection, "
            "impacting traffic toward Nandankanan Road.\n\n"
            "Fire brigade teams managed to contain the blaze within 30 minutes, preventing electrical fires from spreading to adjacent commercial blocks. "
            "Municipal power grid authorities confirmed maintenance teams are isolating lines. Commuters are advised to divert via Infocity Avenue."
        )
        cursor.execute("""
        INSERT INTO incidents (title, summary, full_article, primary_image, category, latitude, longitude, is_spatial, confidence_score, status, explainability_json, report_count, source_diversity, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Electrical Substation Fire at KIIT Square",
            "Transformer burst near KIIT Square causing power outages; emergency crews en route.",
            article_fire, "", "Fire", 20.3533, 85.8189, 1, 82, "VERIFIED", exp_fire, 7, 5, now, now
        ))
        inc_fire_id = cursor.lastrowid
        cursor.execute("""
        INSERT INTO reports (user_id, incident_id, category, description, latitude, longitude, is_spatial, image_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("usr_peter", inc_fire_id, "Fire", "Huge black smoke and sparks coming out of transformer.", 20.3533, 85.8189, 1, "", now))

        exp_roadblock = json.dumps([
            {"label": "3 commuter reports filed", "value": "+18"},
            {"label": "Matching coordinates along NH-16", "value": "+14"}
        ])
        article_roadblock = (
            "Following persistent overnight showers, substantial water accumulation coupled with an uprooted banyan tree "
            "has obstructed two outbound lanes on the NH-16 service corridor near Patia.\n\n"
            "Civic cleanup machinery is currently on-site clearing timber debris. Traffic police have instituted temporary single-lane routing. "
            "Expected clearance time is approximately 2 hours."
        )
        cursor.execute("""
        INSERT INTO incidents (title, summary, full_article, primary_image, category, latitude, longitude, is_spatial, confidence_score, status, explainability_json, report_count, source_diversity, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Waterlogging & Fallen Tree on NH-16 Service Road",
            "Severe waterlogging and a large tree branch blocking two lanes near Patia.",
            article_roadblock, "", "Obstruction", 20.3588, 85.8201, 1, 46, "COMMUNITY", exp_roadblock, 3, 3, now, now
        ))

        exp_busted = json.dumps([
            {"label": "Recycled earthquake stock image detected", "value": "-35"},
            {"label": "Zero municipal emergency call correlation", "value": "-25"}
        ])
        article_busted = (
            "Circulating WhatsApp claims depicting cracked structural pillars along the Rasulgarh flyover have been evaluated and debunked. "
            "Our automated visual provenance verification identified the circulating graphic as an archival photo from a 2018 infrastructure incident elsewhere.\n\n"
            "National Highways Authority of India (NHAI) structural inspectors conducted a physical survey this morning and confirmed zero structural flaws."
        )
        cursor.execute("""
        INSERT INTO incidents (title, summary, full_article, primary_image, category, latitude, longitude, is_spatial, confidence_score, status, explainability_json, report_count, source_diversity, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Viral Rumor: False Flyover Crack Claim",
            "Social media rumor claiming structural collapse on Rasulgarh flyover debunked by NHAI engineers.",
            article_busted, "", "Disaster", 20.3012, 85.8566, 1, 10, "BUSTED", exp_busted, 8, 1, now, now
        ))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database schema successfully configured (OTP tokens, users, incidents, reports).")
