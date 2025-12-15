from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, EmailStr
from typing import List, Optional, Literal
from uuid import uuid4
import psycopg2
import psycopg2.extras
import os
import math
from contextlib import contextmanager
from datetime import datetime


DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(
    title="Usability World Day AR Backend",
    version="2.0"
)

# ------------------------------------------------------------
# DB helpers
# ------------------------------------------------------------
@contextmanager
def db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()

def now_utc():
    return datetime.utcnow()

def haversine_m(lat1, lon1, lat2, lon2):
    # distance in meters
    R = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# ------------------------------------------------------------
# Startup: create tables (one-time, safe)
# ------------------------------------------------------------
@app.on_event("startup")
def startup_create_tables():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY,
            device_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL DEFAULT 'Guest',
            email TEXT,
            is_guest BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS zones (
            id UUID PRIMARY KEY,
            join_code TEXT UNIQUE NOT NULL,
            lat DOUBLE PRECISION,
            lng DOUBLE PRECISION,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS collectibles (
            id UUID PRIMARY KEY,
            zone_id UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
            type TEXT NOT NULL,                -- UI / UX / GOLD
            points INT NOT NULL,
            matrix JSONB NOT NULL,             -- 16 floats
            created_by UUID,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)

        # points per user per zone (leaderboard per zone)
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

# ------------------------------------------------------------
# Models
# ------------------------------------------------------------
CollectType = Literal["UI", "UX", "GOLD"]

class RegisterRequest(BaseModel):
    deviceId: str

class RegisterResponse(BaseModel):
    userId: str
    name: str
    isGuest: bool
    points: int

class ClaimRequest(BaseModel):
    userId: str
    name: str
    email: EmailStr

class ClaimResponse(BaseModel):
    ok: bool
    name: str
    email: str
    isGuest: bool

class AutoZoneRequest(BaseModel):
    lat: float
    lng: float

class AutoZoneResponse(BaseModel):
    zoneId: str
    joinCode: str

class CollectibleCreateRequest(BaseModel):
    type: CollectType
    points: int
    matrix: List[float]  # 16 floats
    createdBy: Optional[str] = None

class CollectibleDTO(BaseModel):
    id: str
    type: CollectType
    points: int
    matrix: List[float]

class CollectResponse(BaseModel):
    awardedPoints: int
    totalPoints: int

class LeaderboardEntry(BaseModel):
    name: str
    points: int

# ------------------------------------------------------------
# Users
# ------------------------------------------------------------
@app.post("/users/register", response_model=RegisterResponse)
def register_guest(req: RegisterRequest):
    with db() as conn:
        cur = conn.cursor()

        cur.execute("SELECT id, name, is_guest FROM users WHERE device_id=%s", (req.deviceId,))
        row = cur.fetchone()

        if row:
            user_id = str(row[0])
            name = row[1]
            is_guest = bool(row[2])
            # points unknown without zone -> return 0 (app will show points per zone)
            return RegisterResponse(userId=user_id, name=name, isGuest=is_guest, points=0)

        user_id = str(uuid4())
        cur.execute("""
            INSERT INTO users (id, device_id, name, is_guest)
            VALUES (%s,%s,%s,true)
        """, (user_id, req.deviceId, "Guest"))

        conn.commit()

        return RegisterResponse(userId=user_id, name="Guest", isGuest=True, points=0)

@app.post("/users/claim", response_model=ClaimResponse)
def claim_user(req: ClaimRequest):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
            SET name=%s, email=%s, is_guest=false
            WHERE id=%s
            RETURNING name, email, is_guest
        """, (req.name.strip(), req.email.strip().lower(), req.userId))

        row = cur.fetchone()
        if not row:
            raise HTTPException(404, detail="user not found")

        conn.commit()

        return ClaimResponse(ok=True, name=row[0], email=row[1], isGuest=bool(row[2]))

@app.get("/users/{user_id}/points")
def get_user_points(user_id: str, zoneId: str = Query(...)):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT points
            FROM user_zone_points
            WHERE user_id=%s AND zone_id=%s
        """, (user_id, zoneId))
        row = cur.fetchone()
        return {"points": int(row[0]) if row else 0}

# ------------------------------------------------------------
# Zones
# ------------------------------------------------------------
# NOTE: بدل ما ننشئ Zone كل مرة، نبحث عن Zone قريبة (مثلاً ضمن 150m)
AUTO_ZONE_RADIUS_M = float(os.environ.get("AUTO_ZONE_RADIUS_M", "150"))

@app.post("/zones/auto", response_model=AutoZoneResponse)
def auto_zone(req: AutoZoneRequest):
    with db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 1) إذا فيه Zone قريبة -> رجعها
        cur.execute("SELECT id, join_code, lat, lng FROM zones")
        zones = cur.fetchall()

        nearest = None
        nearest_d = None
        for z in zones:
            if z["lat"] is None or z["lng"] is None:
                continue
            d = haversine_m(req.lat, req.lng, float(z["lat"]), float(z["lng"]))
            if nearest is None or d < nearest_d:
                nearest = z
                nearest_d = d

        if nearest and nearest_d is not None and nearest_d <= AUTO_ZONE_RADIUS_M:
            return AutoZoneResponse(zoneId=str(nearest["id"]), joinCode=str(nearest["join_code"]))

        # 2) غير كذا -> أنشئ Zone جديدة
        zone_id = str(uuid4())
        join_code = zone_id.split("-")[0][:6].upper()

        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO zones (id, join_code, lat, lng)
            VALUES (%s,%s,%s,%s)
        """, (zone_id, join_code, req.lat, req.lng))

        conn.commit()

        return AutoZoneResponse(zoneId=zone_id, joinCode=join_code)

# (اختياري) إنشاء Zone يدويًا إذا تبي 3 Zones ثابتة من بدري
@app.post("/zones/create")
def create_zone_manual(lat: float = Query(...), lng: float = Query(...)):
    with db() as conn:
        cur = conn.cursor()
        zone_id = str(uuid4())
        join_code = zone_id.split("-")[0][:6].upper()
        cur.execute("""
            INSERT INTO zones (id, join_code, lat, lng)
            VALUES (%s,%s,%s,%s)
        """, (zone_id, join_code, lat, lng))
        conn.commit()
        return {"zoneId": zone_id, "joinCode": join_code}

# ------------------------------------------------------------
# Collectibles
# ------------------------------------------------------------
@app.get("/zones/{zone_id}/collectibles", response_model=List[CollectibleDTO])
def list_collectibles(zone_id: str):
    with db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, type, points, matrix
            FROM collectibles
            WHERE zone_id=%s
            ORDER BY created_at ASC
        """, (zone_id,))
        rows = cur.fetchall()

        out = []
        for r in rows:
            m = r["matrix"]
            # matrix stored as json -> could be list already
            out.append(CollectibleDTO(
                id=str(r["id"]),
                type=r["type"],
                points=int(r["points"]),
                matrix=list(m)
            ))
        return out

@app.post("/zones/{zone_id}/collectibles", response_model=CollectibleDTO)
def create_collectible(zone_id: str, req: CollectibleCreateRequest):
    if len(req.matrix) != 16:
        raise HTTPException(400, detail="matrix must be 16 floats")

    with db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cid = str(uuid4())

        cur.execute("""
            INSERT INTO collectibles (id, zone_id, type, points, matrix, created_by)
            VALUES (%s,%s,%s,%s,%s,%s)
            RETURNING id, type, points, matrix
        """, (cid, zone_id, req.type, req.points, psycopg2.extras.Json(req.matrix), req.createdBy))

        row = cur.fetchone()
        if not row:
            raise HTTPException(500, detail="failed to create collectible")

        conn.commit()

        return CollectibleDTO(
            id=str(row["id"]),
            type=row["type"],
            points=int(row["points"]),
            matrix=list(row["matrix"])
        )

@app.post("/collectibles/{collectible_id}/collect", response_model=CollectResponse)
def collect_item(collectible_id: str, userId: str = Query(...)):
    # Transaction: lock collectible, delete it, add points (per zone)
    with db() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Lock collectible so two users can't collect at the same time
            cur.execute("""
                SELECT id, zone_id, points
                FROM collectibles
                WHERE id=%s
                FOR UPDATE
            """, (collectible_id,))
            c = cur.fetchone()
            if not c:
                raise HTTPException(404, detail="collectible not found")

            zone_id = str(c["zone_id"])
            awarded = int(c["points"])

            # Delete collectible
            cur.execute("DELETE FROM collectibles WHERE id=%s", (collectible_id,))

            # Upsert user points for that zone
            cur.execute("""
                INSERT INTO user_zone_points (user_id, zone_id, points, updated_at)
                VALUES (%s,%s,%s,NOW())
                ON CONFLICT (user_id, zone_id)
                DO UPDATE SET points = user_zone_points.points + EXCLUDED.points,
                              updated_at = NOW()
                RETURNING points
            """, (userId, zone_id, awarded))

            total = cur.fetchone()
            total_points = int(total["points"]) if total else awarded

            conn.commit()
            return CollectResponse(awardedPoints=awarded, totalPoints=total_points)

        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(500, detail=str(e))

# ------------------------------------------------------------
# Leaderboard (per zone)
# ------------------------------------------------------------
@app.get("/zones/{zone_id}/leaderboard", response_model=List[LeaderboardEntry])
def leaderboard(zone_id: str, limit: int = 10):
    with db() as conn:
        cur = conn.cursor()
        # show only claimed users (is_guest=false) and with points table for that zone
        cur.execute("""
            SELECT u.name, uz.points
            FROM user_zone_points uz
            JOIN users u ON u.id = uz.user_id
            WHERE uz.zone_id=%s
              AND u.is_guest=false
              AND u.name IS NOT NULL
            ORDER BY uz.points DESC
            LIMIT %s
        """, (zone_id, limit))

        rows = cur.fetchall()
        return [{"name": r[0], "points": int(r[1])} for r in rows]
