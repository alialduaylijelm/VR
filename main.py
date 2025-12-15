from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from uuid import uuid4
import psycopg2
from psycopg2.extras import Json
import os

DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(
    title="Usability World Day AR Backend",
    version="1.1"
)

def db():
    # Render/Neon غالبًا يحتاج SSL
    return psycopg2.connect(DATABASE_URL)

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
    type: str = Field(..., description="UI / UX / GOLD")
    points: int
    matrix: List[float]  # 16 float

class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]

class LeaderboardRow(BaseModel):
    name: str
    points: int

# ------------------------
# Users
# ------------------------

@app.post("/users/register")
def register_guest(req: RegisterRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, zone_id, points FROM users WHERE device_id=%s", (req.deviceId,))
    row = cur.fetchone()

    if row:
        user_id, zone_id, points = row
        conn.close()
        return {
            "userId": user_id,
            "name": "Guest",
            "isGuest": True,
            "zoneId": zone_id,
            "points": points or 0
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
        "zoneId": None,
        "points": 0
    }

@app.post("/users/claim")
def claim_user(req: ClaimRequest):
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

    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(404, detail="User not found")

    conn.commit()
    conn.close()

    return {"ok": True}

# ------------------------
# Zones
# ------------------------

@app.post("/zones/auto")
def auto_zone(req: AutoZoneRequest):
    """
    Creates a new zone and returns zoneId + joinCode.
    (بسيطة للتجربة، لاحقًا ممكن نخليها تعيد نفس الـ zone حسب الموقع)
    """
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

    return {"zoneId": zone_id, "joinCode": join_code}

@app.post("/zones/{zone_id}/join")
def join_zone(zone_id: str, userId: str = Query(...)):
    """
    Assign a user to a zone.
    """
    conn = db()
    cur = conn.cursor()

    # Ensure zone exists
    cur.execute("SELECT id FROM zones WHERE id=%s", (zone_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, detail="Zone not found")

    cur.execute("UPDATE users SET zone_id=%s WHERE id=%s", (zone_id, userId))
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(404, detail="User not found")

    conn.commit()
    conn.close()
    return {"ok": True, "zoneId": zone_id}

# ------------------------
# Collectibles
# ------------------------

@app.post("/zones/{zone_id}/collectibles")
def create_collectible(zone_id: str, req: CollectibleRequest):
    """
    Create a collectible inside a zone.
    """
    cid = str(uuid4())

    conn = db()
    cur = conn.cursor()

    # ensure zone exists (avoid FK 500)
    cur.execute("SELECT id FROM zones WHERE id=%s", (zone_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, detail="Zone not found")

    # ✅ Important: wrap matrix with Json() so psycopg2 stores it properly
    cur.execute(
        """
        INSERT INTO collectibles (id, zone_id, type, points, matrix)
        VALUES (%s,%s,%s,%s,%s)
        """,
        (cid, zone_id, req.type, req.points, Json(req.matrix))
    )

    conn.commit()
    conn.close()
    return {"id": cid}

@app.get("/zones/{zone_id}/collectibles", response_model=List[CollectibleDTO])
def list_collectibles(zone_id: str):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, type, points, matrix FROM collectibles WHERE zone_id=%s", (zone_id,))
    rows = cur.fetchall()
    conn.close()

    return [
        {"id": r[0], "type": r[1], "points": r[2], "matrix": r[3]}
        for r in rows
    ]

@app.post("/collectibles/{collectible_id}/collect")
def collect_item(collectible_id: str, userId: str = Query(...)):
    conn = db()
    cur = conn.cursor()

    # get collectible (and its zone)
    cur.execute(
        "SELECT points, zone_id FROM collectibles WHERE id=%s",
        (collectible_id,)
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, detail="Collectible not found")

    points, zone_id = row

    # ensure user exists
    cur.execute("SELECT id FROM users WHERE id=%s", (userId,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, detail="User not found")

    # add points + bind zone (optional but helpful)
    cur.execute(
        "UPDATE users SET points = COALESCE(points,0) + %s, zone_id = COALESCE(zone_id,%s) WHERE id=%s",
        (points, zone_id, userId)
    )

    # delete collectible (one-time)
    cur.execute("DELETE FROM collectibles WHERE id=%s", (collectible_id,))

    conn.commit()
    conn.close()

    return {"points": points}

# ------------------------
# Leaderboard
# ------------------------

@app.get("/zones/{zone_id}/leaderboard", response_model=List[LeaderboardRow])
def leaderboard(zone_id: str):
    conn = db()
    cur = conn.cursor()

    # ✅ leaderboard per zone + exclude guests (optional)
    cur.execute(
        """
        SELECT name, points
        FROM users
        WHERE zone_id=%s AND is_guest=false
        ORDER BY points DESC
        LIMIT 10
        """,
        (zone_id,)
    )

    data = [{"name": r[0], "points": r[1] or 0} for r in cur.fetchall()]
    conn.close()
    return data

# ------------------------
# Health
# ------------------------

@app.get("/health")
def health():
    return {"ok": True}
