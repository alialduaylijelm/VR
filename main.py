from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
from uuid import uuid4
import psycopg2
import os

DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(title="Usability World Day AR Backend", version="1.0")

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

class JoinZoneRequest(BaseModel):
    joinCode: str
    userId: str

class AutoZoneRequest(BaseModel):
    lat: float
    lng: float

class CollectibleRequest(BaseModel):
    type: str
    points: int
    matrix: list  # لازم تكون 16 float

class CollectRequest(BaseModel):
    userId: str

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
        conn.close()
        return {"userId": row[0], "name": row[1], "isGuest": row[2]}

    user_id = str(uuid4())
    cur.execute("""
        INSERT INTO users (id, device_id, name, is_guest, points)
        VALUES (%s,%s,%s,true,0)
    """, (user_id, req.deviceId, "Guest"))
    conn.commit()
    conn.close()

    return {"userId": user_id, "name": "Guest", "isGuest": True}

@app.post("/users/claim")
def claim_user(req: ClaimRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET name=%s, email=%s, is_guest=false
        WHERE id=%s
        RETURNING id, name, email
    """, (req.name, req.email, req.userId))
    row = cur.fetchone()
    conn.commit()
    conn.close()

    if not row:
        raise HTTPException(404, "User not found")

    return {"ok": True, "userId": row[0], "name": row[1], "email": row[2]}

# ------------------------
# Zones
# ------------------------
@app.post("/zones/auto")
def auto_zone(req: AutoZoneRequest):
    # لو تبي “ثابت” لا تستخدم auto في كل مرة (استخدم join)
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

@app.post("/zones/join")
def join_zone(req: JoinZoneRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, join_code FROM zones WHERE join_code=%s", (req.joinCode,))
    z = cur.fetchone()
    if not z:
        conn.close()
        raise HTTPException(404, "Zone not found")

    zone_id = z[0]

    # سجل العضوية (لازم جدول zone_members)
    cur.execute("""
        INSERT INTO zone_members (zone_id, user_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
    """, (zone_id, req.userId))

    conn.commit()
    conn.close()

    return {"zoneId": zone_id, "joinCode": req.joinCode}

# ------------------------
# Collectibles
# ------------------------
@app.get("/zones/{zone_id}/collectibles")
def list_collectibles(zone_id: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, type, matrix
        FROM collectibles
        WHERE zone_id=%s
    """, (zone_id,))
    rows = cur.fetchall()
    conn.close()

    return [{"id": r[0], "type": r[1], "matrix": r[2]} for r in rows]

@app.post("/zones/{zone_id}/collectibles")
def create_collectible(zone_id: str, req: CollectibleRequest):
    cid = str(uuid4())
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO collectibles (id, zone_id, type, points, matrix)
        VALUES (%s,%s,%s,%s,%s)
    """, (cid, zone_id, req.type, req.points, req.matrix))
    conn.commit()
    conn.close()

    return {"id": cid, "type": req.type, "matrix": req.matrix}

@app.post("/collectibles/{collectible_id}/collect")
def collect_item(collectible_id: str, req: CollectRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT points FROM collectibles WHERE id=%s", (collectible_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Collectible not found")

    points = row[0]

    cur.execute("UPDATE users SET points = points + %s WHERE id=%s", (points, req.userId))
    cur.execute("DELETE FROM collectibles WHERE id=%s", (collectible_id,))

    conn.commit()
    conn.close()

    return {"ok": True, "points": points}

# ------------------------
# Leaderboard (per zone)
# ------------------------
@app.get("/zones/{zone_id}/leaderboard")
def leaderboard(zone_id: str):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT u.id, u.name, u.points
        FROM zone_members zm
        JOIN users u ON u.id = zm.user_id
        WHERE zm.zone_id = %s
        ORDER BY u.points DESC
        LIMIT 10
    """, (zone_id,))

    data = [{"id": r[0], "name": r[1], "points": r[2]} for r in cur.fetchall()]
    conn.close()
    return data
