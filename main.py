import os
import shutil
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

import database
import trust_engine

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
if not SECRET_KEY:
    # Never silently run with a guessable key. Generate a per-process key so
    # the app still boots for local/hackathon use, but warn loudly so it
    # doesn't ship to production like this.
    import secrets
    SECRET_KEY = secrets.token_hex(32)
    print("\n[WARNING] JWT_SECRET_KEY is not set in your .env file.")
    print("[WARNING] A random key was generated for this run only — all sessions")
    print("[WARNING] will be invalidated on restart. Set JWT_SECRET_KEY in .env.\n")

ALGORITHM = "HS256"
COOKIE_NAME = "bugle_session"

app = FastAPI(title="The Daily Bugle News & Trust Engine")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

database.init_db()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- Session & Authentication Helpers ---

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=24))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None
    except jwt.PyJWTError:
        return None


def set_session_cookie(response: JSONResponse, user: dict):
    token = create_access_token({"sub": user["username"], "user_id": user["id"]})
    response.set_cookie(key=COOKIE_NAME, value=token, httponly=True, samesite="lax", max_age=86400)


def public_user(user: dict) -> dict:
    return {
        "username": user["username"],
        "name": user["name"],
        "role": user["role"],
        "trust_score": user["trust_score"],
        "strike_count": user["strike_count"],
        "is_quarantined": bool(user["is_quarantined"]),
    }


# --- HTML Page Routes ---

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(TEMPLATES_DIR, "index.html"))


@app.get("/incident/{incident_id}")
def serve_incident_detail_page(incident_id: int):
    return FileResponse(os.path.join(TEMPLATES_DIR, "incident_detail.html"))


@app.get("/login")
def serve_login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(os.path.join(TEMPLATES_DIR, "login.html"))


@app.get("/report")
def serve_report_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/report", status_code=302)
    return FileResponse(os.path.join(TEMPLATES_DIR, "report.html"))


@app.get("/desk")
def serve_desk_page(request: Request):
    user = get_current_user(request)
    if not user or user.get("role") != "EDITOR":
        return RedirectResponse(url="/login?redirect=/desk", status_code=302)
    return FileResponse(os.path.join(TEMPLATES_DIR, "desk.html"))


@app.get("/profile")
def serve_profile_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/profile", status_code=302)
    return FileResponse(os.path.join(TEMPLATES_DIR, "profile.html"))


# --- Authentication API (passwordless OTP — email or phone) ---

@app.post("/api/auth/otp/request")
def request_otp(identifier: str = Form(...), purpose: str = Form("LOGIN")):
    clean_id = identifier.strip().lower()
    if not clean_id:
        return JSONResponse({"error": "Please enter an email address or phone number."}, status_code=400)

    result = database.create_otp_token(clean_id, purpose=purpose, expiry_minutes=5)
    if not result["ok"]:
        return JSONResponse({"error": result["error"]}, status_code=429)

    payload = {
        "status": "success",
        "message": "Verification code dispatched.",
        "delivered_via": result["delivered_via"],
    }
    if result.get("dev_code"):
        payload["dev_code"] = result["dev_code"]
        payload["dev_note"] = "DEBUG mode: code shown here because no SMTP relay is configured."
    return payload


@app.post("/api/auth/otp/verify")
def verify_otp(
    identifier: str = Form(...),
    otp_code: str = Form(...),
    editor_invite_code: str = Form(""),
    purpose: str = Form("LOGIN"),
):
    clean_id = identifier.strip().lower()
    result = database.verify_otp_token(clean_id, otp_code, purpose=purpose)
    if not result["ok"]:
        return JSONResponse({"error": result["error"]}, status_code=400)

    user = database.sync_or_create_user(clean_id, editor_invite_code=editor_invite_code)

    response = JSONResponse({"status": "success", "user": public_user(user)})
    set_session_cookie(response, user)
    return response


@app.post("/api/auth/logout")
def logout_user():
    response = JSONResponse({"status": "success", "message": "Session invalidated"})
    response.delete_cookie(key=COOKIE_NAME)
    return response


@app.get("/api/auth/me")
def current_user_session(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"authenticated": False})
    return {"authenticated": True, **public_user(user)}


# --- Incidents & Article API Endpoints ---

@app.get("/api/incidents")
def get_incidents(view: str = "wire"):
    conn = database.get_connection()
    cursor = conn.cursor()

    if view == "wire":
        cursor.execute("SELECT * FROM incidents WHERE status != 'BUSTED' ORDER BY updated_at DESC")
    elif view == "radar":
        cursor.execute("""
            SELECT * FROM incidents
            WHERE is_spatial = 1 AND status IN ('VERIFIED', 'COMMUNITY')
            ORDER BY confidence_score DESC
        """)
    elif view == "graveyard":
        cursor.execute("SELECT * FROM incidents WHERE status = 'BUSTED' ORDER BY updated_at DESC")
    elif view == "desk":
        cursor.execute("SELECT * FROM incidents ORDER BY confidence_score DESC, updated_at DESC")
    else:
        cursor.execute("SELECT * FROM incidents ORDER BY updated_at DESC")

    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


@app.get("/api/incidents/{incident_id}")
def get_single_incident(incident_id: int):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    incident = cursor.fetchone()
    if not incident:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident article not found")

    incident_dict = dict(incident)
    cursor.execute("""
        SELECT r.*, u.name as reporter_name, u.role as reporter_role, u.trust_score as reporter_trust
        FROM reports r
        LEFT JOIN users u ON r.user_id = u.id
        WHERE r.incident_id = ?
        ORDER BY r.created_at ASC
    """, (incident_id,))
    reports = [dict(r) for r in cursor.fetchall()]
    conn.close()

    incident_dict["reports"] = reports
    return incident_dict


@app.post("/api/reports")
async def submit_citizen_report(
    request: Request,
    category: str = Form(...),
    description: str = Form(...),
    latitude: float = Form(0.0),
    longitude: float = Form(0.0),
    is_spatial: int = Form(1),
    image: UploadFile = File(None),
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Verified sign-in required to submit scoops."}, status_code=401)

    image_rel_url = ""
    if image and image.filename:
        safe_filename = f"{int(datetime.utcnow().timestamp() * 1000)}_{image.filename.replace(' ', '_')}"
        save_path = os.path.join(UPLOADS_DIR, safe_filename)
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
        image_rel_url = f"/static/uploads/{safe_filename}"

    result = trust_engine.process_new_report(
        user_id=user["id"],
        category=category,
        description=description,
        lat=latitude,
        lng=longitude,
        is_spatial=is_spatial,
        image_url=image_rel_url,
    )
    return JSONResponse(result)


@app.get("/api/reports/mine")
def get_my_reports(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Sign-in required."}, status_code=401)

    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.id, r.description, r.category, r.created_at, r.image_url,
               i.id as incident_id, i.title, i.status, i.confidence_score
        FROM reports r
        JOIN incidents i ON r.incident_id = i.id
        WHERE r.user_id = ?
        ORDER BY r.created_at DESC
    """, (user["id"],))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"user": public_user(user), "reports": rows}


@app.post("/api/incidents/{incident_id}/action")
def update_incident_action(incident_id: int, action: str = Form(...), request: Request = None):
    user = get_current_user(request)
    if not user or user.get("role") != "EDITOR":
        return JSONResponse({"error": "Unauthorized: Editor credentials required."}, status_code=403)

    success = trust_engine.update_incident_status(incident_id, action)
    return {"success": success, "incident_id": incident_id, "action": action}


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 8000))
    print(f"Daily Bugle server running at http://{host}:{port}")
    uvicorn.run("main:app", host=host, port=port, reload=True)
