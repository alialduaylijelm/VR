from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from uuid import uuid4
import psycopg2
import os

DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(title="Usability World Day AR Backend", version="1.1")

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
    type: str            # UI / UX / GOLD
    points: int
    matrix: list         # 16 floats

class CollectibleDTO(BaseModel):
    id: str
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

    cur.execute("SELECT id, name, is_guest, points FROM users WHERE device_id=%s", (req.deviceId,))
    row = cur.fetchone()

    if row:
        conn.close()
        return {"userId": row[0], "name": row[1] or "Guest", "isGuest": row[2], "points": row[3]}

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

    return {"userId": user_id, "name": "Guest", "isGuest": True, "points": 0}

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
# Zones (auto: nearest existing within radius, else create)
# ------------------------
@app.post("/zones/auto")
def auto_zone(req: AutoZoneRequest, radius_m: int = 400):
    """
    يرجع أقرب zone موجودة ضمن radius (بالمتر).
    إذا ما فيه، ينشئ zone جديدة.
    """
    conn = db()
    cur = conn.cursor()

    # Approx distance in meters (simple equirectangular-ish using degrees):
    # 1 deg lat ~ 111_000m, 1 deg lng ~ 111_000m * cos(lat)
    # We'll compare squared distance to avoid heavy funcs.
    cur.execute(
        """
        SELECT id, join_code, lat, lng
        FROM zones
        """
    )
    zones = cur.fetchall()

    best = None
    best_d2 = None
    for z in zones:
        zid, code, zlat, zlng = z
        if zlat is None or zlng is None:
            continue
        dx = (req.lat - zlat) * 111000.0
        dy = (req.lng - zlng) * 111000.0
        d2 = dx*dx + dy*dy
        if best_d2 is None or d2 < best_d2:
            best_d2 = d2
            best = (zid, code)

    if best is not None and best_d2 is not None:
        if best_d2 <= (radius_m * radius_m):
            conn.close()
            return {"zoneId": str(best[0]), "joinCode": best[1]}

    # create new zone
    zone_id = str(uuid4())
    join_code = zone_id[:6].upper()

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

# ------------------------
# Collectibles
# ------------------------
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
        (zone_id,)
    )

    data = [{"id": r[0], "type": r[1], "points": r[2], "matrix": r[3]} for r in cur.fetchall()]
    conn.close()
    return data

@app.post("/zones/{zone_id}/collectibles", response_model=CollectibleDTO)
def create_collectible(zone_id: str, req: CollectibleRequest):
    cid = str(uuid4())
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO collectibles (id, zone_id, type, points, matrix)
        VALUES (%s,%s,%s,%s,%s)
        """,
        (cid, zone_id, req.type, req.points, req.matrix)
    )
    conn.commit()
    conn.close()

    return {"id": cid, "type": req.type, "points": req.points, "matrix": req.matrix}

@app.post("/collectibles/{collectible_id}/collect")
def collect_item(collectible_id: str, userId: str = Query(...)):
    conn = db()
    cur = conn.cursor()

    # find collectible + zone
    cur.execute(
        "SELECT zone_id, points FROM collectibles WHERE id=%s",
        (collectible_id,)
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Collectible not found")

    zone_id, points = row[0], row[1]

    # prevent double collect
    cur.execute(
        """
        INSERT INTO collected_items (collectible_id, user_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (collectible_id, userId)
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(409, "Already collected")

    # ensure user is member of this zone (for leaderboard)
    cur.execute(
        """
        INSERT INTO zone_members (zone_id, user_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (zone_id, userId)
    )

    # add points
    cur.execute(
        "UPDATE users SET points = points + %s WHERE id=%s",
        (points, userId)
    )

    # delete collectible after collection
    cur.execute("DELETE FROM collectibles WHERE id=%s", (collectible_id,))

    conn.commit()
    conn.close()

    return {"points": points}

# ------------------------
# Leaderboard (per zone)
# ------------------------
@app.get("/zones/{zone_id}/leaderboard")
def leaderboard(zone_id: str):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT u.name, u.points
        FROM users u
        JOIN zone_members zm ON zm.user_id = u.id
        WHERE zm.zone_id = %s
        ORDER BY u.points DESC
        LIMIT 10
        """,
        (zone_id,)
    )

    data = [{"name": r[0], "points": r[1]} for r in cur.fetchall()]
    conn.close()
    return data
