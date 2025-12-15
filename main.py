from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Literal
from uuid import uuid4
from datetime import datetime
import psycopg2
import psycopg2.extras
import os
import re

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

app = FastAPI(title="Usability World Day AR Backend", version="2.0")


# ------------------------
# DB Helpers
# ------------------------
def db():
    # DictCursor عشان ترجع dict بدل tuple
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    conn = db()
    cur = conn.cursor()

    # tables
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY,
        device_id TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL DEFAULT 'Guest',
        email TEXT,
        is_guest BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS zones (
        id UUID PRIMARY KEY,
        join_code TEXT UNIQUE NOT NULL,
        lat DOUBLE PRECISION,
        lng DOUBLE PRECISION,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    # matrix as jsonb (list of 16 floats)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS collectibles (
        id UUID PRIMARY KEY,
        zone_id UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        points INTEGER NOT NULL,
        matrix JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    # points per zone
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_zone_points (
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        zone_id UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
        points INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, zone_id)
    );
    """)

    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


# ------------------------
# Models
# ------------------------
class RegisterRequest(BaseModel):
    deviceId: str

class ClaimRequest(BaseModel):
    userId: str
    name: str
    email: str

class AutoZoneRequest(BaseModel):
    lat: float
    lng: float

class CollectibleRequest(BaseModel):
    type: Literal["UI", "UX", "GOLD"]
    points: int
    matrix: List[float]  # لازم 16 رقم

class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]


# ------------------------
# Small validators
# ------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def ensure_matrix_ok(m: List[float]):
    if not isinstance(m, list) or len(m) != 16:
        raise HTTPException(400, detail="matrix must be a list of 16 numbers")


# ------------------------
# Users
# ------------------------
@app.post("/users/register")
def register_guest(req: RegisterRequest, zoneId: Optional[str] = Query(default=None)):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, name, is_guest FROM users WHERE device_id=%s", (req.deviceId,))
    row = cur.fetchone()

    if row:
        user_id = str(row["id"])
    else:
        user_id = str(uuid4())
        cur.execute(
            "INSERT INTO users (id, device_id, name, is_guest) VALUES (%s,%s,%s,true)",
            (user_id, req.deviceId, "Guest")
        )

    # points (optional)
    pts = 0
    if zoneId:
        try:
            cur.execute("""
                SELECT points FROM user_zone_points
                WHERE user_id=%s AND zone_id=%s
            """, (user_id, zoneId))
            p = cur.fetchone()
            pts = int(p["points"]) if p else 0
        except Exception:
            pts = 0

    conn.commit()
    conn.close()

    return {"userId": user_id, "name": "Guest", "isGuest": True, "points": pts}


@app.post("/users/claim")
def claim_user(req: ClaimRequest):
    # تحقق إيميل بسيط بدون email-validator
    if not EMAIL_RE.match(req.email.strip().lower()):
        raise HTTPException(400, detail="invalid email format")

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE id=%s", (req.userId,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, detail="user not found")

    cur.execute("""
        UPDATE users
        SET name=%s, email=%s, is_guest=false
        WHERE id=%s
    """, (req.name.strip(), req.email.strip().lower(), req.userId))

    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/users/{user_id}/points")
def user_points(user_id: str, zoneId: str = Query(...)):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT points FROM user_zone_points
        WHERE user_id=%s AND zone_id=%s
    """, (user_id, zoneId))
    row = cur.fetchone()

    conn.close()
    return {"points": int(row["points"]) if row else 0}


# ------------------------
# Zones
# ------------------------
@app.post("/zones/auto")
def auto_zone(req: AutoZoneRequest):
    zone_id = str(uuid4())
    join_code = zone_id[:6].upper()

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO zones (id, join_code, lat, lng)
        VALUES (%s,%s,%s,%s)
    """, (zone_id, join_code, req.lat, req.lng))

    conn.commit()
    conn.close()

    return {"zoneId": zone_id, "joinCode": join_code}


# ------------------------
# Collectibles
# ------------------------
@app.get("/zones/{zone_id}/collectibles")
def list_collectibles(zone_id: str):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, type, points, matrix
        FROM collectibles
        WHERE zone_id=%s
        ORDER BY created_at ASC
    """, (zone_id,))
    rows = cur.fetchall()

    conn.close()

    data = []
    for r in rows:
        data.append({
            "id": str(r["id"]),
            "type": r["type"],
            "points": int(r["points"]),
            "matrix": r["matrix"],  # jsonb يرجع list
        })
    return data


@app.post("/zones/{zone_id}/collectibles")
def create_collectible(zone_id: str, req: CollectibleRequest):
    ensure_matrix_ok(req.matrix)

    cid = str(uuid4())
    conn = db()
    cur = conn.cursor()

    # تأكد zone موجود
    cur.execute("SELECT id FROM zones WHERE id=%s", (zone_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, detail="zone not found")

    try:
        cur.execute("""
            INSERT INTO collectibles (id, zone_id, type, points, matrix)
            VALUES (%s,%s,%s,%s,%s::jsonb)
            RETURNING id, type, points, matrix
        """, (cid, zone_id, req.type, req.points, psycopg2.extras.Json(req.matrix)))
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(500, detail=f"db insert error: {str(e)}")

    row = cur.fetchone()
    conn.commit()
    conn.close()

    return {
        "id": str(row["id"]),
        "type": row["type"],
        "points": int(row["points"]),
        "matrix": row["matrix"],
    }


@app.post("/collectibles/{collectible_id}/collect")
def collect_item(collectible_id: str, userId: str = Query(...)):
    conn = db()
    cur = conn.cursor()

    # جيب العنصر
    cur.execute("""
        SELECT id, zone_id, type, points
        FROM collectibles
        WHERE id=%s
    """, (collectible_id,))
    item = cur.fetchone()
    if not item:
        conn.close()
        raise HTTPException(404, detail="collectible not found")

    zone_id = str(item["zone_id"])
    pts = int(item["points"])

    # تأكد user موجود
    cur.execute("SELECT id FROM users WHERE id=%s", (userId,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, detail="user not found")

    # upsert نقاط الزون
    cur.execute("""
        INSERT INTO user_zone_points (user_id, zone_id, points)
        VALUES (%s,%s,%s)
        ON CONFLICT (user_id, zone_id)
        DO UPDATE SET points = user_zone_points.points + EXCLUDED.points,
                      updated_at = NOW()
        RETURNING points
    """, (userId, zone_id, pts))
    new_total = int(cur.fetchone()["points"])

    # احذف العنصر من العالم
    cur.execute("DELETE FROM collectibles WHERE id=%s", (collectible_id,))

    conn.commit()
    conn.close()

    return {"awardedPoints": pts, "totalPoints": new_total, "zoneId": zone_id}


# ------------------------
# Leaderboard
# ------------------------
@app.get("/zones/{zone_id}/leaderboard")
def leaderboard(zone_id: str, limit: int = 10):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT u.name, uz.points
        FROM user_zone_points uz
        JOIN users u ON u.id = uz.user_id
        WHERE uz.zone_id=%s
        ORDER BY uz.points DESC
        LIMIT %s
    """, (zone_id, limit))

    rows = cur.fetchall()
    conn.close()

    return [{"name": r["name"], "points": int(r["points"])} for r in rows]
