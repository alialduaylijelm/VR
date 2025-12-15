import os
import re
from uuid import uuid4
from typing import List, Optional, Any

import psycopg2
from psycopg2.extras import Json
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel


DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is missing")

app = FastAPI(title="Usability World Day AR Backend", version="2.0")


# ------------------------------------------------------------
# DB helpers
# ------------------------------------------------------------
def db():
    return psycopg2.connect(DATABASE_URL)


def one(cur):
    return cur.fetchone()


def many(cur):
    return cur.fetchall()


# ------------------------------------------------------------
# Simple email validation (NO email-validator dependency)
# ------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def validate_email(email: str) -> None:
    if not email or not EMAIL_RE.match(email.strip()):
        raise HTTPException(status_code=400, detail="invalid email format")


# ------------------------------------------------------------
# Models
# ------------------------------------------------------------
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
    type: str            # "UI" / "UX" / "GOLD"
    points: int
    matrix: List[float]  # 16 floats


class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]


class LeaderboardRow(BaseModel):
    name: str
    points: int


# ------------------------------------------------------------
# Health
# ------------------------------------------------------------
@app.get("/")
def root():
    return {"ok": True, "service": app.title, "version": app.version}


@app.get("/health")
def health():
    return {"ok": True}


# ------------------------------------------------------------
# Users
# ------------------------------------------------------------
@app.post("/users/register")
def register_guest(req: RegisterRequest):
    if not req.deviceId:
        raise HTTPException(status_code=400, detail="deviceId required")

    conn = db()
    cur = conn.cursor()

    # Find existing
    cur.execute("SELECT id, name, is_guest, points FROM users WHERE device_id=%s", (req.deviceId,))
    row = one(cur)
    if row:
        conn.close()
        return {"userId": row[0], "name": row[1], "isGuest": bool(row[2]), "points": row[3]}

    # Create guest
    user_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO users (id, device_id, name, email, is_guest, points, created_at)
        VALUES (%s, %s, %s, NULL, true, 0, NOW())
        """,
        (user_id, req.deviceId, "Guest"),
    )
    conn.commit()
    conn.close()

    return {"userId": user_id, "name": "Guest", "isGuest": True, "points": 0}


@app.post("/users/claim")
def claim_user(req: ClaimRequest):
    if not req.userId:
        raise HTTPException(status_code=400, detail="userId required")
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="name required")

    validate_email(req.email)

    conn = db()
    cur = conn.cursor()

    # ensure user exists
    cur.execute("SELECT id FROM users WHERE id=%s", (req.userId,))
    if not one(cur):
        conn.close()
        raise HTTPException(status_code=404, detail="user not found")

    cur.execute(
        """
        UPDATE users
        SET name=%s, email=%s, is_guest=false
        WHERE id=%s
        """,
        (req.name.strip(), req.email.strip().lower(), req.userId),
    )
    conn.commit()
    conn.close()

    return {"ok": True}


# ------------------------------------------------------------
# Zones
# ------------------------------------------------------------
@app.post("/zones/auto")
def auto_zone(req: AutoZoneRequest):
    zone_id = str(uuid4())
    join_code = zone_id[:6].upper()

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO zones (id, join_code, lat, lng, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        """,
        (zone_id, join_code, req.lat, req.lng),
    )
    conn.commit()
    conn.close()

    return {"zoneId": zone_id, "joinCode": join_code}


# ------------------------------------------------------------
# Collectibles
# ------------------------------------------------------------
@app.get("/zones/{zone_id}/collectibles", response_model=List[CollectibleDTO])
def list_collectibles(zone_id: str):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, type, points, matrix
        FROM collectibles
        WHERE zone_id=%s
        ORDER BY created_at ASC
        """,
        (zone_id,),
    )

    rows = many(cur)
    conn.close()

    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "type": r[1],
                "points": int(r[2]),
                "matrix": list(r[3]) if isinstance(r[3], (list, tuple)) else (r[3] or []),
            }
        )
    return out


@app.post("/zones/{zone_id}/collectibles", response_model=CollectibleDTO)
def create_collectible(zone_id: str, req: CollectibleRequest):
    t = (req.type or "").strip().upper()
    if t not in ("UI", "UX", "GOLD"):
        raise HTTPException(status_code=400, detail="type must be UI/UX/GOLD")
    if not isinstance(req.matrix, list) or len(req.matrix) != 16:
        raise HTTPException(status_code=400, detail="matrix must be 16 floats")

    cid = str(uuid4())

    conn = db()
    cur = conn.cursor()

    # Ensure zone exists (optional but nice)
    cur.execute("SELECT id FROM zones WHERE id=%s", (zone_id,))
    if not one(cur):
        conn.close()
        raise HTTPException(status_code=404, detail="zone not found")

    cur.execute(
        """
        INSERT INTO collectibles (id, zone_id, type, points, matrix, created_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        RETURNING id, type, points, matrix
        """,
        (cid, zone_id, t, int(req.points), Json(req.matrix)),
    )
    row = one(cur)
    conn.commit()
    conn.close()

    return {"id": row[0], "type": row[1], "points": int(row[2]), "matrix": list(row[3])}


@app.post("/collectibles/{collectible_id}/collect")
def collect_item(collectible_id: str, userId: str = Query(...)):
    conn = db()
    cur = conn.cursor()

    # Load collectible + zone
    cur.execute(
        "SELECT id, zone_id, points FROM collectibles WHERE id=%s",
        (collectible_id,),
    )
    c = one(cur)
    if not c:
        conn.close()
        raise HTTPException(status_code=404, detail="collectible not found")

    zone_id = c[1]
    pts = int(c[2])

    # Ensure user exists
    cur.execute("SELECT id FROM users WHERE id=%s", (userId,))
    if not one(cur):
        conn.close()
        raise HTTPException(status_code=404, detail="user not found")

    # Award points
    cur.execute("UPDATE users SET points = points + %s WHERE id=%s", (pts, userId))

    # Delete collectible (so nobody else can collect it)
    cur.execute("DELETE FROM collectibles WHERE id=%s", (collectible_id,))

    conn.commit()
    conn.close()

    return {"awardedPoints": pts, "zoneId": zone_id}


# ------------------------------------------------------------
# Leaderboard (per zone)
# ------------------------------------------------------------
@app.get("/zones/{zone_id}/leaderboard", response_model=List[LeaderboardRow])
def leaderboard(zone_id: str, limit: int = 10):
    # ✅ Leaderboard حسب الزون: نجمع نقاط المستخدمين بناء على عمليات الجمع داخل الزون
    # عشان ما نحتاج جدول events، نستخدم users.points (عام) + نضيف جدول user_zone_points لو تبي دقة أعلى.
    #
    # الحل البسيط الآن:
    # - نخلي users.points عام (مثل ما سوينا)
    # - ونطلع top users (global) أو (نفس الزون)؟
    #
    # بما أن عندك Zones متعددة وتبي Leaderboard "حقيقي" لكل زون،
    # نستخدم جدول user_zone_points (موجود في SQL تحت).
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT u.name, uz.points
        FROM user_zone_points uz
        JOIN users u ON u.id = uz.user_id
        WHERE uz.zone_id = %s
        ORDER BY uz.points DESC
        LIMIT %s
        """,
        (zone_id, int(limit)),
    )
    rows = many(cur)
    conn.close()

    return [{"name": r[0], "points": int(r[1])} for r in rows]


@app.get("/zones/{zone_id}/users/{user_id}/points")
def user_points(zone_id: str, user_id: str):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT points FROM user_zone_points WHERE zone_id=%s AND user_id=%s",
        (zone_id, user_id),
    )
    row = one(cur)
    conn.close()
    return {"points": int(row[0]) if row else 0}


# ------------------------------------------------------------
# IMPORTANT: Update per-zone points when collecting
# ------------------------------------------------------------
# نعمل override بسيط: endpoint ثاني يجمع ويحدّث user_zone_points
@app.post("/collectibles/{collectible_id}/collect_v2")
def collect_item_v2(collectible_id: str, userId: str = Query(...)):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, zone_id, points FROM collectibles WHERE id=%s", (collectible_id,))
    c = one(cur)
    if not c:
        conn.close()
        raise HTTPException(status_code=404, detail="collectible not found")

    zone_id = c[1]
    pts = int(c[2])

    cur.execute("SELECT id FROM users WHERE id=%s", (userId,))
    if not one(cur):
        conn.close()
        raise HTTPException(status_code=404, detail="user not found")

    # Update global points
    cur.execute("UPDATE users SET points = points + %s WHERE id=%s", (pts, userId))

    # ✅ Update per-zone points
    cur.execute(
        """
        INSERT INTO user_zone_points (zone_id, user_id, points)
        VALUES (%s, %s, %s)
        ON CONFLICT (zone_id, user_id)
        DO UPDATE SET points = user_zone_points.points + EXCLUDED.points
        """,
        (zone_id, userId, pts),
    )

    # Delete collectible
    cur.execute("DELETE FROM collectibles WHERE id=%s", (collectible_id,))

    conn.commit()
    conn.close()

    return {"awardedPoints": pts, "zoneId": zone_id}
