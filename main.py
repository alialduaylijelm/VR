import os
import re
from typing import List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
import psycopg2
import psycopg2.extras

# ============================================================
# Config
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    # لو تبي تشغله محليًا حط DATABASE_URL في env
    # مثال: postgres://user:pass@localhost:5432/dbname
    pass

app = FastAPI(title="VR Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # عدّلها لو تبي
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    con = db()
    try:
        with con, con.cursor() as cur:
            # Extensions (uuid)
            cur.execute("""CREATE EXTENSION IF NOT EXISTS "uuid-ossp";""")

            # Users
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
              id uuid PRIMARY KEY,
              device_id text UNIQUE NOT NULL,
              name text NOT NULL DEFAULT 'Guest',
              email text,
              is_guest boolean NOT NULL DEFAULT true,
              created_at timestamptz NOT NULL DEFAULT now()
            );
            """)

            # Zones
            cur.execute("""
            CREATE TABLE IF NOT EXISTS zones (
              id uuid PRIMARY KEY,
              join_code text UNIQUE NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now()
            );
            """)

            # Collectibles
            cur.execute("""
            CREATE TABLE IF NOT EXISTS collectibles (
              id uuid PRIMARY KEY,
              zone_id uuid NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
              type text NOT NULL CHECK (type in ('UI','UX','GOLD')),
              points int NOT NULL DEFAULT 0,
              matrix double precision[] NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now()
            );
            """)

            # Collections (who collected what)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS collectible_collections (
              id uuid PRIMARY KEY,
              collectible_id uuid NOT NULL REFERENCES collectibles(id) ON DELETE CASCADE,
              user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              zone_id uuid NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
              awarded_points int NOT NULL DEFAULT 0,
              collected_at timestamptz NOT NULL DEFAULT now(),
              UNIQUE (collectible_id, user_id)
            );
            """)

            # Points per user per zone (fast lookup)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_zone_points (
              user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              zone_id uuid NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
              points int NOT NULL DEFAULT 0,
              updated_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (user_id, zone_id)
            );
            """)
    finally:
        con.close()

@app.on_event("startup")
def on_startup():
    init_db()

# ============================================================
# Models
# ============================================================
class RegisterBody(BaseModel):
    deviceId: str = Field(min_length=3)

class RegisterResponse(BaseModel):
    userId: str
    name: str
    isGuest: bool
    points: int = 0

class ClaimRequest(BaseModel):
    userId: str
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr

class OkResponse(BaseModel):
    ok: bool = True

class AutoZoneBody(BaseModel):
    lat: float
    lng: float

class AutoZoneResponse(BaseModel):
    zoneId: str
    joinCode: str

class CreateCollectibleBody(BaseModel):
    type: str
    points: int
    matrix: List[float] = Field(min_length=16, max_length=16)

class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]

class CollectResponse(BaseModel):
    points: int  # نرجع points للعميل
    awardedPoints: int  # و awardedPoints برضه عشان التوافق

class LeaderboardEntry(BaseModel):
    name: str
    points: int

# ============================================================
# Helpers
# ============================================================
def mk_join_code(zone_id: str) -> str:
    # زي EA764F من uuid ea764f10...
    return zone_id.split("-")[0].upper()

def normalize_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    return name[:80] if name else "Player"

def safe_uuid(u: str) -> str:
    # نترك postgres يتحقق، لكن نضمن مو فاضي
    if not u or len(u) < 10:
        raise HTTPException(status_code=400, detail="invalid uuid")
    return u

# ============================================================
# Health
# ============================================================
@app.get("/health")
def health():
    return {"ok": True}

# ============================================================
# Users
# ============================================================
@app.post("/users/register", response_model=RegisterResponse)
def register_user(body: RegisterBody):
    con = db()
    try:
        with con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # if exists by device_id
            cur.execute("SELECT * FROM users WHERE device_id=%s", (body.deviceId,))
            row = cur.fetchone()
            if row:
                # return current points if possible (requires zoneId usually)
                return RegisterResponse(
                    userId=str(row["id"]),
                    name=row["name"],
                    isGuest=row["is_guest"],
                    points=0
                )

            uid = str(uuid4())
            cur.execute(
                "INSERT INTO users (id, device_id, name, is_guest) VALUES (%s,%s,'Guest',true)",
                (uid, body.deviceId),
            )
            return RegisterResponse(userId=uid, name="Guest", isGuest=True, points=0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db insert error: {e}")
    finally:
        con.close()

@app.post("/users/claim", response_model=OkResponse)
def claim_user(req: ClaimRequest):
    user_id = safe_uuid(req.userId)
    name = normalize_name(req.name)

    con = db()
    try:
        with con, con.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE id=%s", (user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="user not found")

            cur.execute(
                "UPDATE users SET name=%s, email=%s, is_guest=false WHERE id=%s",
                (name, req.email, user_id),
            )
            return OkResponse(ok=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db update error: {e}")
    finally:
        con.close()

@app.get("/users/{user_id}/points")
def user_points(user_id: str, zoneId: str = Query(...)):
    user_id = safe_uuid(user_id)
    zone_id = safe_uuid(zoneId)

    con = db()
    try:
        with con, con.cursor() as cur:
            cur.execute(
                "SELECT points FROM user_zone_points WHERE user_id=%s AND zone_id=%s",
                (user_id, zone_id),
            )
            row = cur.fetchone()
            return {"points": int(row[0]) if row else 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db read error: {e}")
    finally:
        con.close()

# ============================================================
# Zones
# ============================================================
@app.post("/zones/auto", response_model=AutoZoneResponse)
def auto_zone(_: AutoZoneBody):
    # أبسط شيء: يخلق Zone جديد كل مرة
    zid = str(uuid4())
    join = mk_join_code(zid)

    con = db()
    try:
        with con, con.cursor() as cur:
            cur.execute("INSERT INTO zones (id, join_code) VALUES (%s,%s)", (zid, join))
        return AutoZoneResponse(zoneId=zid, joinCode=join)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db insert error: {e}")
    finally:
        con.close()

@app.get("/zones/{zone_id}/collectibles", response_model=List[CollectibleDTO])
def list_collectibles(zone_id: str):
    zone_id = safe_uuid(zone_id)
    con = db()
    try:
        with con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, type, points, matrix FROM collectibles WHERE zone_id=%s ORDER BY created_at ASC",
                (zone_id,),
            )
            rows = cur.fetchall()
            return [
                CollectibleDTO(
                    id=str(r["id"]),
                    type=r["type"],
                    points=int(r["points"]),
                    matrix=[float(x) for x in r["matrix"]],
                )
                for r in rows
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db read error: {e}")
    finally:
        con.close()

@app.post("/zones/{zone_id}/collectibles", response_model=CollectibleDTO)
def create_collectible(zone_id: str, body: CreateCollectibleBody):
    zone_id = safe_uuid(zone_id)
    ctype = body.type.strip().upper()
    if ctype not in ("UI", "UX", "GOLD"):
        raise HTTPException(status_code=400, detail="type must be UI/UX/GOLD")

    if len(body.matrix) != 16:
        raise HTTPException(status_code=400, detail="matrix must have 16 numbers")

    cid = str(uuid4())
    con = db()
    try:
        with con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # ensure zone exists (create if not)
            cur.execute("SELECT id FROM zones WHERE id=%s", (zone_id,))
            if not cur.fetchone():
                join = mk_join_code(zone_id)
                cur.execute("INSERT INTO zones (id, join_code) VALUES (%s,%s)", (zone_id, join))

            cur.execute(
                "INSERT INTO collectibles (id, zone_id, type, points, matrix) VALUES (%s,%s,%s,%s,%s) "
                "RETURNING id,type,points,matrix",
                (cid, zone_id, ctype, int(body.points), body.matrix),
            )
            r = cur.fetchone()
            return CollectibleDTO(
                id=str(r["id"]),
                type=r["type"],
                points=int(r["points"]),
                matrix=[float(x) for x in r["matrix"]],
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db insert error: {e}")
    finally:
        con.close()

# ============================================================
# Collect (IMPORTANT)
# ============================================================
def _collect_internal(collectible_id: str, user_id: str) -> CollectResponse:
    collectible_id = safe_uuid(collectible_id)
    user_id = safe_uuid(user_id)

    con = db()
    try:
        with con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # validate user
            cur.execute("SELECT id FROM users WHERE id=%s", (user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="user not found")

            # validate collectible
            cur.execute("SELECT id, zone_id, points FROM collectibles WHERE id=%s", (collectible_id,))
            col = cur.fetchone()
            if not col:
                raise HTTPException(status_code=404, detail="collectible not found")

            zone_id = str(col["zone_id"])
            pts = int(col["points"])

            # already collected?
            cur.execute(
                "SELECT 1 FROM collectible_collections WHERE collectible_id=%s AND user_id=%s",
                (collectible_id, user_id),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="already collected")

            # insert collection
            cur.execute(
                "INSERT INTO collectible_collections (id, collectible_id, user_id, zone_id, awarded_points) "
                "VALUES (%s,%s,%s,%s,%s)",
                (str(uuid4()), collectible_id, user_id, zone_id, pts),
            )

            # upsert points
            cur.execute(
                "INSERT INTO user_zone_points (user_id, zone_id, points) VALUES (%s,%s,%s) "
                "ON CONFLICT (user_id, zone_id) DO UPDATE SET points = user_zone_points.points + EXCLUDED.points, updated_at=now()",
                (user_id, zone_id, pts),
            )

            return CollectResponse(points=pts, awardedPoints=pts)

    except HTTPException:
        raise
    except Exception as e:
        # هنا نطلع السبب الحقيقي بدل Internal Server Error
        raise HTTPException(status_code=500, detail=f"collect failed: {e}")
    finally:
        con.close()

@app.post("/collectibles/{collectible_id}/collect", response_model=CollectResponse)
def collect(collectible_id: str, userId: str = Query(...)):
    return _collect_internal(collectible_id, userId)

# Alias for Swift (لو كانت تستدعي collect_v2)
@app.post("/collectibles/{collectible_id}/collect_v2", response_model=CollectResponse)
def collect_v2(collectible_id: str, userId: str = Query(...)):
    return _collect_internal(collectible_id, userId)

# ============================================================
# Leaderboard
# ============================================================
@app.get("/zones/{zone_id}/leaderboard", response_model=List[LeaderboardEntry])
def leaderboard(zone_id: str):
    zone_id = safe_uuid(zone_id)
    con = db()
    try:
        with con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT COALESCE(u.name,'Guest') AS name, uz.points AS points
                FROM user_zone_points uz
                JOIN users u ON u.id = uz.user_id
                WHERE uz.zone_id = %s
                ORDER BY uz.points DESC, u.created_at ASC
                LIMIT 50
            """, (zone_id,))
            rows = cur.fetchall()
            return [LeaderboardEntry(name=r["name"], points=int(r["points"])) for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"leaderboard failed: {e}")
    finally:
        con.close()
