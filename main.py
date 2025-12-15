from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from uuid import uuid4
import psycopg2
from psycopg2.extras import Json
import os
import re

DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(title="Usability World Day AR Backend", version="2.0")


# ------------------------
# DB
# ------------------------
def db():
    return psycopg2.connect(DATABASE_URL)


# ------------------------
# Models
# ------------------------
class RegisterRequest(BaseModel):
    deviceId: str


class ClaimRequest(BaseModel):
    userId: str
    name: str
    email: str  # ✅ بدون EmailStr عشان ما يحتاج email-validator


class AutoZoneRequest(BaseModel):
    lat: float
    lng: float


class CollectibleRequest(BaseModel):
    type: str          # UI / UX / GOLD
    points: int
    matrix: List[float]  # 16 floats


class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]


class LeaderboardEntry(BaseModel):
    name: str
    points: int


class CollectResponse(BaseModel):
    awardedPoints: int
    totalPoints: int


# ------------------------
# Helpers
# ------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def ensure_schema():
    """
    Creates tables if they do not exist.
    Uses jsonb for matrix to make it easy.
    Uses a zone leaderboard table user_zone_points (per user per zone).
    """
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY,
        device_id TEXT UNIQUE,
        name TEXT NOT NULL DEFAULT 'Guest',
        email TEXT,
        is_guest BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS zones (
        id UUID PRIMARY KEY,
        join_code TEXT UNIQUE,
        lat DOUBLE PRECISION,
        lng DOUBLE PRECISION,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    # matrix jsonb
    cur.execute("""
    CREATE TABLE IF NOT EXISTS collectibles (
        id UUID PRIMARY KEY,
        zone_id UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        points INT NOT NULL,
        matrix JSONB NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    # per-zone points (leaderboard by zone)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_zone_points (
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        zone_id UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
        points INT NOT NULL DEFAULT 0,
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, zone_id)
    );
    """)

    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup():
    ensure_schema()


def upsert_user_zone_points(cur, user_id: str, zone_id: str, add_points: int) -> int:
    """
    Add points to user in a zone and return total.
    """
    cur.execute(
        """
        INSERT INTO user_zone_points (user_id, zone_id, points)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, zone_id)
        DO UPDATE SET
            points = user_zone_points.points + EXCLUDED.points,
            updated_at = NOW()
        RETURNING points;
        """,
        (user_id, zone_id, add_points),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def get_user_zone_points(cur, user_id: str, zone_id: str) -> int:
    cur.execute(
        "SELECT points FROM user_zone_points WHERE user_id=%s AND zone_id=%s",
        (user_id, zone_id),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


# ------------------------
# Health
# ------------------------
@app.get("/")
def root():
    return {"ok": True, "service": "Usability World Day AR Backend"}


# ------------------------
# Users
# ------------------------
@app.post("/users/register")
def register_guest(req: RegisterRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, name, is_guest FROM users WHERE device_id=%s", (req.deviceId,))
    row = cur.fetchone()

    if row:
        user_id = str(row[0])
        # points across ALL zones depends, so we return points=0 here (client can query per zone)
        conn.close()
        return {"userId": user_id, "name": row[1], "isGuest": bool(row[2]), "points": 0}

    user_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO users (id, device_id, name, is_guest)
        VALUES (%s,%s,%s,true)
        """,
        (user_id, req.deviceId, "Guest"),
    )
    conn.commit()
    conn.close()
    return {"userId": user_id, "name": "Guest", "isGuest": True, "points": 0}


@app.post("/users/claim")
def claim_user(req: ClaimRequest):
    name = (req.name or "").strip()
    email = (req.email or "").strip().lower()

    if len(name) < 2:
        raise HTTPException(400, detail="name too short")

    if not EMAIL_RE.match(email):
        raise HTTPException(400, detail="invalid email format")

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE id=%s", (req.userId,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, detail="user not found")

    cur.execute(
        """
        UPDATE users
        SET name=%s, email=%s, is_guest=false
        WHERE id=%s
        """,
        (name, email, req.userId),
    )

    conn.commit()
    conn.close()
    return {"ok": True}


# ------------------------
# Zones
# ------------------------
@app.post("/zones/auto")
def auto_zone(req: AutoZoneRequest):
    zone_id = str(uuid4())
    join_code = zone_id[:6].upper()

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO zones (id, join_code, lat, lng)
        VALUES (%s,%s,%s,%s)
        """,
        (zone_id, join_code, req.lat, req.lng),
    )
    conn.commit()
    conn.close()

    return {"zoneId": zone_id, "joinCode": join_code}


# ------------------------
# Collectibles
# ------------------------
@app.get("/zones/{zone_id}/collectibles", response_model=List[CollectibleDTO])
def list_collectibles(zone_id: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, type, points, matrix FROM collectibles WHERE zone_id=%s ORDER BY created_at DESC", (zone_id,))
    rows = cur.fetchall()
    conn.close()

    out = []
    for r in rows:
        out.append(
            {
                "id": str(r[0]),
                "type": r[1],
                "points": int(r[2]),
                "matrix": list(r[3]) if isinstance(r[3], list) else r[3],  # jsonb returns list
            }
        )
    return out


@app.post("/zones/{zone_id}/collectibles", response_model=CollectibleDTO)
def create_collectible(zone_id: str, req: CollectibleRequest):
    t = req.type.strip().upper()
    if t not in ("UI", "UX", "GOLD"):
        raise HTTPException(400, detail="type must be UI/UX/GOLD")

    if len(req.matrix) != 16:
        raise HTTPException(400, detail="matrix must contain 16 floats")

    cid = str(uuid4())

    conn = db()
    cur = conn.cursor()

    # ensure zone exists
    cur.execute("SELECT id FROM zones WHERE id=%s", (zone_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, detail="zone not found")

    cur.execute(
        """
        INSERT INTO collectibles (id, zone_id, type, points, matrix)
        VALUES (%s,%s,%s,%s,%s)
        """,
        (cid, zone_id, t, req.points, Json(req.matrix)),  # ✅ أهم تعديل
    )
    conn.commit()
    conn.close()

    return {"id": cid, "type": t, "points": req.points, "matrix": req.matrix}


@app.post("/collectibles/{collectible_id}/collect", response_model=CollectResponse)
def collect_item(collectible_id: str, userId: str = Query(...)):
    conn = db()
    cur = conn.cursor()

    # get collectible
    cur.execute("SELECT zone_id, points FROM collectibles WHERE id=%s", (collectible_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, detail="collectible not found")

    zone_id = str(row[0])
    awarded = int(row[1])

    # ensure user exists
    cur.execute("SELECT id FROM users WHERE id=%s", (userId,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, detail="user not found")

    # add points to this zone leaderboard table
    total = upsert_user_zone_points(cur, userId, zone_id, awarded)

    # delete collectible so no one can collect twice
    cur.execute("DELETE FROM collectibles WHERE id=%s", (collectible_id,))

    conn.commit()
    conn.close()

    return {"awardedPoints": awarded, "totalPoints": total}


# ------------------------
# Points (per zone)
# ------------------------
@app.get("/zones/{zone_id}/users/{user_id}/points")
def user_points(zone_id: str, user_id: str):
    conn = db()
    cur = conn.cursor()
    pts = get_user_zone_points(cur, user_id, zone_id)
    conn.close()
    return {"points": pts}


# ------------------------
# Leaderboard (per zone)
# ------------------------
@app.get("/zones/{zone_id}/leaderboard", response_model=List[LeaderboardEntry])
def leaderboard(zone_id: str):
    conn = db()
    cur = conn.cursor()

    # Top 10 for this zone
    cur.execute(
        """
        SELECT u.name, uz.points
        FROM user_zone_points uz
        JOIN users u ON u.id = uz.user_id
        WHERE uz.zone_id = %s
        ORDER BY uz.points DESC, uz.updated_at DESC
        LIMIT 10
        """,
        (zone_id,),
    )
    rows = cur.fetchall()
    conn.close()

    return [{"name": r[0], "points": int(r[1])} for r in rows]
