from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List
from uuid import uuid4
import psycopg2
import os

# =====================================================
# CONFIG
# =====================================================
DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(
    title="Usability World Day AR Backend",
    version="1.1"
)

def db():
    return psycopg2.connect(DATABASE_URL)

# =====================================================
# MODELS
# =====================================================

class RegisterRequest(BaseModel):
    deviceId: str

class ClaimRequest(BaseModel):
    userId: str
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=5, max_length=200)

class AutoZoneRequest(BaseModel):
    lat: float
    lng: float

class CollectibleRequest(BaseModel):
    type: str          # UI / UX / GOLD
    points: int
    matrix: List[float]

# =====================================================
# USERS
# =====================================================

@app.post("/users/register")
def register_guest(req: RegisterRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, points FROM users WHERE device_id=%s",
        (req.deviceId,)
    )
    row = cur.fetchone()

    if row:
        return {
            "userId": row[0],
            "name": "Guest",
            "isGuest": True,
            "points": row[1]
        }

    user_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO users (id, device_id, name, is_guest, points)
        VALUES (%s,%s,%s,true,0)
        """,
        (user_id, req.deviceId, "Guest")
    )

    conn.commit()
    conn.close()

    return {
        "userId": user_id,
        "name": "Guest",
        "isGuest": True,
        "points": 0
    }

@app.post("/users/claim")
def claim_user(req: ClaimRequest):
    # تحقق بسيط للإيميل
    if "@" not in req.email or "." not in req.email:
        raise HTTPException(status_code=400, detail="invalid email")

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET name=%s, email=%s, is_guest=false
        WHERE id=%s
        """,
        (req.name, req.email, req.userId)
    )

    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/users/{user_id}/points")
def user_points(user_id: str, zoneId: str = Query(...)):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT points FROM users WHERE id=%s",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "user not found")

    return {"points": row[0]}

# =====================================================
# ZONES
# =====================================================

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
        (zone_id, join_code, req.lat, req.lng)
    )
    conn.commit()
    conn.close()

    return {
        "zoneId": zone_id,
        "joinCode": join_code
    }

# =====================================================
# COLLECTIBLES
# =====================================================

@app.get("/zones/{zone_id}/collectibles")
def list_collectibles(zone_id: str):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, type, points, matrix
        FROM collectibles
        WHERE zone_id=%s
        """,
        (zone_id,)
    )

    rows = cur.fetchall()
    conn.close()

    return [
        {
            "id": r[0],
            "type": r[1],
            "points": r[2],
            "matrix": r[3]
        }
        for r in rows
    ]

@app.post("/zones/{zone_id}/collectibles")
def create_collectible(zone_id: str, req: CollectibleRequest):
    cid = str(uuid4())

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO collectibles (id, zone_id, type, points, matrix)
        VALUES (%s,%s,%s,%s,%s::double precision[])
        """,
        (cid, zone_id, req.type, req.points, req.matrix)
    )

    conn.commit()
    conn.close()

    return {
        "id": cid,
        "type": req.type,
        "points": req.points,
        "matrix": req.matrix
    }

@app.post("/collectibles/{collectible_id}/collect")
def collect_item(collectible_id: str, userId: str = Query(...)):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT points FROM collectibles WHERE id=%s",
        (collectible_id,)
    )
    row = cur.fetchone()

    if not row:
        conn.close()
        raise HTTPException(404, "collectible not found")

    points = row[0]

    # زوّد نقاط المستخدم
    cur.execute(
        "UPDATE users SET points = points + %s WHERE id=%s",
        (points, userId)
    )

    # احذف العنصر
    cur.execute(
        "DELETE FROM collectibles WHERE id=%s",
        (collectible_id,)
    )

    conn.commit()
    conn.close()

    return {"awardedPoints": points}

# =====================================================
# LEADERBOARD
# =====================================================

@app.get("/zones/{zone_id}/leaderboard")
def leaderboard(zone_id: str):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT name, points
        FROM users
        WHERE is_guest=false
        ORDER BY points DESC
        LIMIT 20
        """
    )

    rows = cur.fetchall()
    conn.close()

    return [
        {"name": r[0], "points": r[1]}
        for r in rows
    ]
