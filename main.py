from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from uuid import uuid4
import psycopg2
import os

# ------------------------
# Config / DB
# ------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Neon غالباً يحتاج SSL
if DATABASE_URL and "sslmode=" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = DATABASE_URL + f"{sep}sslmode=require"

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(DATABASE_URL)

app = FastAPI(
    title="Usability World Day AR Backend",
    version="1.1"
)

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

CollectibleType = Literal["UI", "UX", "GOLD"]

class CollectibleRequest(BaseModel):
    type: CollectibleType
    points: int
    matrix: List[float] = Field(..., description="16 floats (4x4 world transform)")

# ------------------------
# Health / Debug
# ------------------------

@app.get("/health")
def health():
    return {"ok": True, "version": "1.1"}

@app.get("/db/ping")
def db_ping():
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        conn.close()
        return {"db": "ok"}
    except Exception as e:
        raise HTTPException(500, detail=f"DB error: {str(e)}")

# ------------------------
# Users
# ------------------------

@app.post("/users/register")
def register_guest(req: RegisterRequest):
    """
    Idempotent: لو نفس deviceId رجّع نفس userId
    """
    try:
        conn = db()
        cur = conn.cursor()

        cur.execute("SELECT id, name, is_guest FROM users WHERE device_id=%s", (req.deviceId,))
        row = cur.fetchone()
        if row:
            conn.close()
            return {"userId": row[0], "name": row[1] or "Guest", "isGuest": bool(row[2])}

        user_id = str(uuid4())
        cur.execute(
            """
            INSERT INTO users (id, device_id, name, email, is_guest, points)
            VALUES (%s,%s,%s,NULL,true,0)
            """,
            (user_id, req.deviceId, "Guest")
        )
        conn.commit()
        conn.close()
        return {"userId": user_id, "name": "Guest", "isGuest": True}

    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.post("/users/claim")
def claim_user(req: ClaimRequest):
    """
    يحوّل الضيف لمشارك (name/email) — النقاط تبقى
    """
    try:
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
            (req.name.strip(), req.email.strip().lower(), req.userId)
        )
        conn.commit()
        conn.close()
        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))

# ------------------------
# Zones
# ------------------------

@app.post("/zones/auto")
def auto_zone(req: AutoZoneRequest):
    """
    ينشئ Zone جديدة. (استخدمه 3 مرات عشان تطلع 3 zoneIds وتثبتهم)
    """
    try:
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

    except Exception as e:
        raise HTTPException(500, detail=str(e))

# ------------------------
# Collectibles
# ------------------------

@app.get("/zones/{zone_id}/collectibles")
def list_collectibles(zone_id: str):
    """
    يرجّع كل العناصر الحالية في الزون (هذا اللي يخلي التوزيع يظهر للمستخدمين)
    """
    try:
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
            {"id": r[0], "type": r[1], "points": r[2], "matrix": r[3]}
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.post("/zones/{zone_id}/collectibles")
def create_collectible(zone_id: str, req: CollectibleRequest):
    """
    المدير يوزّع عنصر UI/UX/GOLD في الزون ويُحفظ في DB
    """
    try:
        if len(req.matrix) != 16:
            raise HTTPException(422, detail="matrix must have exactly 16 floats")

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

        # رجّع كل البيانات عشان تقدر ترسمه مباشرة في iOS بدون GET
        return {"id": cid, "type": req.type, "points": req.points, "matrix": req.matrix}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.post("/collectibles/{collectible_id}/collect")
def collect_item(
    collectible_id: str,
    userId: str = Query(..., description="userId from /users/register"),
):
    """
    يجمع عنصر: يزيد نقاط المستخدم ويحذف العنصر من DB
    """
    try:
        conn = db()
        cur = conn.cursor()

        cur.execute("SELECT points FROM collectibles WHERE id=%s", (collectible_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, detail="collectible not found")

        points = int(row[0])

        cur.execute("UPDATE users SET points = points + %s WHERE id=%s", (points, userId))
        cur.execute("DELETE FROM collectibles WHERE id=%s", (collectible_id,))

        conn.commit()
        conn.close()

        return {"awardedPoints": points}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))

# ------------------------
# Leaderboard
# ------------------------

@app.get("/zones/{zone_id}/leaderboard")
def leaderboard(zone_id: str):
    """
    حاليا: أفضل 10 (عالمي) — لاحقاً نضيف فلترة per zone بسهولة
    """
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(NULLIF(name,''), 'Guest') AS name, points
            FROM users
            ORDER BY points DESC
            LIMIT 10
            """
        )
        rows = cur.fetchall()
        conn.close()

        return [{"name": r[0], "points": int(r[1])} for r in rows]

    except Exception as e:
        raise HTTPException(500, detail=str(e))
