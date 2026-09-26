import os
import math
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
import database

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trust_engine")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODELS_LIST = [
    m.strip() for m in os.getenv(
        "GEMINI_MODELS",
        "gemini-2.5-flash,gemini-2.5-flash-lite"
    ).split(",") if m.strip()
]

SPATIAL_CLUSTER_RADIUS_METERS = float(os.getenv("SPATIAL_CLUSTER_RADIUS_METERS", 500))
VERIFICATION_THRESHOLD = int(os.getenv("VERIFICATION_THRESHOLD", 75))
COMMUNITY_HAZARD_THRESHOLD = int(os.getenv("COMMUNITY_HAZARD_THRESHOLD", 35))
MAX_STRIKES_BEFORE_QUARANTINE = int(os.getenv("MAX_STRIKES_BEFORE_QUARANTINE", 3))

ai_client = None
if GEMINI_API_KEY and GEMINI_API_KEY != "your_actual_gemini_api_key_here":
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logger.warning(f"Failed to initialize GenAI client: {e}")


def haversine_distance_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def call_gemini_with_fallback(prompt: str, system_instruction: str = "") -> str:
    if not ai_client:
        logger.info("GenAI client not configured. Running heuristic fallback.")
        return ""

    for model_name in MODELS_LIST:
        try:
            logger.info(f"Attempting inference with model: {model_name}")
            config = types.GenerateContentConfig(
                temperature=0.2,
                system_instruction=system_instruction if system_instruction else None
            )
            response = ai_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )
            if response and response.text:
                logger.info(f"Successfully generated response with: {model_name}")
                return response.text.strip()
        except Exception as err:
            logger.warning(f"Model {model_name} failed: {err}. Falling back to next model...")
            continue

    logger.error("All models in the cascade failed or were busy.")
    return ""


def analyze_report_with_ai(category: str, description: str):
    system_instruction = (
        "You are the Daily Bugle's Chief Intelligence Engine. "
        "Analyze citizen reports and return strictly valid JSON matching this schema: "
        '{"clean_title": "string", "concise_summary": "string", "severity": "LOW|MEDIUM|HIGH", '
        '"credibility_modifier": int, "explain_reason": "string"}'
    )
    prompt = f"Category: {category}\nRaw Description: {description}"
    raw_response = call_gemini_with_fallback(prompt, system_instruction)

    if not raw_response:
        return {
            "clean_title": f"Reported {category} Alert",
            "concise_summary": description[:120] + ("..." if len(description) > 120 else ""),
            "severity": "MEDIUM",
            "credibility_modifier": 0,
            "explain_reason": "AI cascade unavailable; heuristic parsing applied."
        }

    try:
        clean_json = raw_response.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean_json)
        parsed.setdefault("clean_title", f"Reported {category} Alert")
        parsed.setdefault("concise_summary", description[:120])
        parsed.setdefault("credibility_modifier", 0)
        return parsed
    except Exception as parse_err:
        logger.warning(f"JSON parsing error: {parse_err}. Raw output was: {raw_response}")
        return {
            "clean_title": f"Reported {category} Event",
            "concise_summary": description[:120],
            "severity": "MEDIUM",
            "credibility_modifier": 0,
            "explain_reason": "Heuristic formatting fallback applied."
        }


def process_new_report(user_id: str, category: str, description: str, lat: float, lng: float, is_spatial: int, image_url: str):
    conn = database.get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute(
            "INSERT INTO users (id, username, name, password_hash, trust_score, strike_count, is_quarantined, created_at) VALUES (?, ?, ?, '', ?, ?, ?, ?)",
            (user_id, f"reporter_{user_id[-6:]}", f"Reporter_{user_id[-4:]}", 0.50, 0, 0, now)
        )
        conn.commit()
        user_trust = 0.50
        is_quarantined = 0
    else:
        user_trust = user["trust_score"]
        is_quarantined = user["is_quarantined"]

    if is_quarantined:
        conn.commit()
        conn.close()
        return {"status": "quarantined", "message": "Account under quarantine audit. Report held."}

    ai_analysis = analyze_report_with_ai(category, description)
    matched_incident_id = None

    if is_spatial and lat and lng:
        cursor.execute("SELECT * FROM incidents WHERE status != 'BUSTED' AND is_spatial = 1")
        active_incidents = cursor.fetchall()
        for inc in active_incidents:
            dist = haversine_distance_meters(lat, lng, inc["latitude"], inc["longitude"])
            if dist <= SPATIAL_CLUSTER_RADIUS_METERS:
                matched_incident_id = inc["id"]
                break

    if matched_incident_id:
        cursor.execute("SELECT * FROM incidents WHERE id = ?", (matched_incident_id,))
        inc = cursor.fetchone()
        new_count = inc["report_count"] + 1

        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM reports WHERE incident_id = ?", (matched_incident_id,))
        existing_distinct_users = cursor.fetchone()[0]
        cursor.execute(
            "SELECT 1 FROM reports WHERE incident_id = ? AND user_id = ? LIMIT 1",
            (matched_incident_id, user_id)
        )
        user_already_reported = cursor.fetchone() is not None
        distinct_users = existing_distinct_users + (0 if user_already_reported else 1)

        reasons = [
            {"label": f"Corroborated by {new_count} citizen reports", "value": f"+{min(25, new_count * 5)}"},
            {"label": f"Source diversity: {distinct_users} independent reporters", "value": f"+{min(20, distinct_users * 4)}"},
            {"label": f"Geospatial proximity verified within {int(SPATIAL_CLUSTER_RADIUS_METERS)}m", "value": "+15"}
        ]
        if image_url:
            reasons.append({"label": "Photo evidence attached", "value": "+10"})
        if ai_analysis.get("explain_reason"):
            reasons.append({"label": ai_analysis["explain_reason"], "value": f"{ai_analysis.get('credibility_modifier', 0):+d}"})

        calculated_score = min(96, 25 + (new_count * 7) + (distinct_users * 5) + ai_analysis.get("credibility_modifier", 0))

        status = inc["status"]
        if category == "Obstruction" and calculated_score >= COMMUNITY_HAZARD_THRESHOLD and status != "VERIFIED":
            status = "COMMUNITY"
        elif calculated_score >= VERIFICATION_THRESHOLD:
            status = "VERIFIED"

        cursor.execute("""
            UPDATE incidents
            SET report_count = ?, source_diversity = ?, confidence_score = ?, status = ?,
                explainability_json = ?, updated_at = ?
            WHERE id = ?
        """, (new_count, distinct_users, calculated_score, status, json.dumps(reasons), now, matched_incident_id))

        assigned_id = matched_incident_id
        final_score = calculated_score
    else:
        base_score = int(user_trust * 50) + (10 if image_url else 0) + ai_analysis.get("credibility_modifier", 0)
        reasons = [
            {"label": "Initial eyewitness filing", "value": f"+{base_score}"},
            {"label": "Awaiting local corroboration", "value": "-10"}
        ]
        if image_url:
            reasons.append({"label": "Visual documentation attached", "value": "+10"})

        status = "COMMUNITY" if category == "Obstruction" and base_score >= COMMUNITY_HAZARD_THRESHOLD else "REVIEW"

        cursor.execute("""
            INSERT INTO incidents (
                title, summary, full_article, primary_image, category, latitude, longitude, is_spatial,
                confidence_score, status, explainability_json, report_count, source_diversity, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ai_analysis["clean_title"], ai_analysis["concise_summary"], description, image_url or "", category,
            lat, lng, is_spatial, base_score, status, json.dumps(reasons), 1, 1, now, now
        ))
        assigned_id = cursor.lastrowid
        final_score = base_score

    cursor.execute("""
        INSERT INTO reports (user_id, incident_id, category, description, latitude, longitude, is_spatial, image_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, assigned_id, category, description, lat, lng, is_spatial, image_url, now))

    conn.commit()
    conn.close()
    return {"status": "success", "incident_id": assigned_id, "score": final_score}


def update_incident_status(incident_id: int, action: str):
    conn = database.get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    if action == "VERIFY":
        cursor.execute("UPDATE incidents SET status = 'VERIFIED', confidence_score = 95, updated_at = ? WHERE id = ?", (now, incident_id))
    elif action == "DISPUTE":
        cursor.execute("UPDATE incidents SET status = 'BUSTED', confidence_score = 10, updated_at = ? WHERE id = ?", (now, incident_id))
        cursor.execute("SELECT DISTINCT user_id FROM reports WHERE incident_id = ?", (incident_id,))
        users = cursor.fetchall()
        for u in users:
            cursor.execute(f"""
                UPDATE users
                SET strike_count = strike_count + 1,
                    trust_score = MAX(0.05, trust_score - 0.20),
                    is_quarantined = CASE WHEN strike_count + 1 >= {MAX_STRIKES_BEFORE_QUARANTINE} THEN 1 ELSE 0 END
                WHERE id = ?
            """, (u["user_id"],))

    conn.commit()
    conn.close()
    return True
