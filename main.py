from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from uuid import uuid4
import psycopg2
import os

DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(
    title="Usability World Day AR Backend",
    version="1.0"
)

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
    email: str

class AutoZoneRequest(BaseModel):
    lat: float
    lng: float

class CollectibleRequest(BaseModel):
    type: str
    points: int
    matrix: list

# ------------------------
# Users
# ------------------------

@app.post("/users/register")
def register_guest(req: RegisterRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM users WHERE device_id=%s",
        (req.deviceId,)
    )
    row = cur.fetchone()

    if row:
        return {
            "userId": row[0],
            "name": "Guest",
            "isGuest": True
        }

    user_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO users (id, device_id, name, is_guest)
        VALUES (%s,%s,%s,true)
        """,
        (user_id, req.deviceId, "Guest")
    )
    conn.commit()
    conn.close()

    return {
        "userId": user_id,
        "name": "Guest",
        "isGuest": True
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
        (zone_id, join_code, req.lat, req.lng)
    )
    conn.commit()
    conn.close()

    return {
        "zoneId": zone_id,
        "joinCode": join_code
    }

# ------------------------
# Collectibles
# ------------------------

@app.post("/zones/{zone_id}/collectibles")
def create_collectible(zone_id: str, req: CollectibleRequest):
    cid = str(uuid4())
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO collectibles
        (id, zone_id, type, points, matrix)
        VALUES (%s,%s,%s,%s,%s)
        """,
        (cid, zone_id, req.type, req.points, req.matrix)
    )
    conn.commit()
    conn.close()
    return {"id": cid}

@app.post("/collectibles/{collectible_id}/collect")
def collect_item(collectible_id: str, userId: str):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT points FROM collectibles WHERE id=%s",
        (collectible_id,)
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404)

    points = row[0]

    cur.execute(
        "UPDATE users SET points = points + %s WHERE id=%s",
        (points, userId)
    )

    cur.execute(
        "DELETE FROM collectibles WHERE id=%s",
        (collectible_id,)
    )

    conn.commit()
    conn.close()

    return {"points": points}

# ------------------------
# Leaderboard
# ------------------------

@app.get("/zones/{zone_id}/leaderboard")
def leaderboard(zone_id: str):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT name, points
        FROM users
        ORDER BY points DESC
        LIMIT 10
        """
    )

    data = [
        {"name": r[0], "points": r[1]}
        for r in cur.fetchall()
    ]

    conn.close()
    return data
