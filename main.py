from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime
import sqlite3
import uuid
import random
import string

app = FastAPI(title="Usability World Day AR Backend", version="1.0")

import os
DB = os.getenv("DB_PATH", "app.db")

def db():
    con = sqlite3.connect(DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS zones (
        id TEXT PRIMARY KEY,
        seed TEXT UNIQUE,
        join_code TEXT UNIQUE,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS collectibles (
        id TEXT PRIMARY KEY,
        zone_id TEXT,
        type TEXT,                 -- UI/UX/GOLD
        matrix TEXT,               -- JSON string of 16 floats
        created_at TEXT,
        collected_by TEXT NULL,    -- user_id
        collected_at TEXT NULL,
        FOREIGN KEY(zone_id) REFERENCES zones(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        name TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS scores (
        user_id TEXT,
        zone_id TEXT,
        points INTEGER,
        updated_at TEXT,
        PRIMARY KEY(user_id, zone_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(zone_id) REFERENCES zones(id)
    )
    """)

    con.commit()
    con.close()

init_db()

def now_iso():
    return datetime.utcnow().isoformat()

def gen_join_code(n=5):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))

# ---------- Schemas ----------

CollectibleType = Literal["UI", "UX", "GOLD"]

class AutoZoneRequest(BaseModel):
    seed: str = Field(..., description="Unique stable seed for the distributor device (store in Keychain)")

class AutoZoneResponse(BaseModel):
    zoneId: str
    joinCode: str

class JoinZoneRequest(BaseModel):
    joinCode: str

class JoinZoneResponse(BaseModel):
    zoneId: str

class RegisterUserRequest(BaseModel):
    name: str

class RegisterUserResponse(BaseModel):
    userId: str
    name: str

class CollectibleCreateRequest(BaseModel):
    type: CollectibleType
    matrix: List[float] = Field(..., min_length=16, max_length=16)

class CollectibleOut(BaseModel):
    id: str
    type: CollectibleType
    matrix: List[float]

class CollectRequest(BaseModel):
    userId: str
    # GOLD question validation (optional الآن)
    goldAnswerCorrect: Optional[bool] = None

class CollectResponse(BaseModel):
    ok: bool
    pointsAwarded: int = 0
    totalPoints: int = 0

class LeaderboardEntry(BaseModel):
    name: str
    points: int

# ---------- Helpers ----------

def get_or_create_user(name: str) -> str:
    con = db()
    cur = con.cursor()
    uid = str(uuid.uuid4())
    cur.execute("INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)", (uid, name, now_iso()))
    con.commit()
    con.close()
    return uid

def ensure_zone_exists(zone_id: str):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id FROM zones WHERE id = ?", (zone_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        raise HTTPException(status_code=404, detail="Zone not found")

def get_score(user_id: str, zone_id: str) -> int:
    con = db()
    cur = con.cursor()
    cur.execute("SELECT points FROM scores WHERE user_id=? AND zone_id=?", (user_id, zone_id))
    row = cur.fetchone()
    con.close()
    return int(row["points"]) if row else 0

def add_points(user_id: str, zone_id: str, delta: int) -> int:
    con = db()
    cur = con.cursor()
    cur.execute("SELECT points FROM scores WHERE user_id=? AND zone_id=?", (user_id, zone_id))
    row = cur.fetchone()
    if row:
        new_points = int(row["points"]) + delta
        cur.execute("UPDATE scores SET points=?, updated_at=? WHERE user_id=? AND zone_id=?",
                    (new_points, now_iso(), user_id, zone_id))
    else:
        new_points = delta
        cur.execute("INSERT INTO scores (user_id, zone_id, points, updated_at) VALUES (?, ?, ?, ?)",
                    (user_id, zone_id, new_points, now_iso()))
    con.commit()
    con.close()
    return new_points

# ---------- API ----------

@app.post("/users/register", response_model=RegisterUserResponse)
def register_user(req: RegisterUserRequest):
    uid = get_or_create_user(req.name)
    return RegisterUserResponse(userId=uid, name=req.name)

@app.post("/zones/auto", response_model=AutoZoneResponse)
def auto_zone(req: AutoZoneRequest):
    """
    Auto zone by first distributor placement:
    - If seed already exists, return same zone
    - Else create new zone + joinCode
    """
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, join_code FROM zones WHERE seed=?", (req.seed,))
    row = cur.fetchone()
    if row:
        con.close()
        return AutoZoneResponse(zoneId=row["id"], joinCode=row["join_code"])

    zid = str(uuid.uuid4())
    # ensure unique join_code
    join_code = gen_join_code()
    while True:
        cur.execute("SELECT 1 FROM zones WHERE join_code=?", (join_code,))
        if not cur.fetchone():
            break
        join_code = gen_join_code()

    cur.execute("INSERT INTO zones (id, seed, join_code, created_at) VALUES (?, ?, ?, ?)",
                (zid, req.seed, join_code, now_iso()))
    con.commit()
    con.close()
    return AutoZoneResponse(zoneId=zid, joinCode=join_code)

@app.post("/zones/join", response_model=JoinZoneResponse)
def join_zone(req: JoinZoneRequest):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id FROM zones WHERE join_code=?", (req.joinCode,))
    row = cur.fetchone()
    con.close()
    if not row:
        raise HTTPException(status_code=404, detail="Invalid join code")
    return JoinZoneResponse(zoneId=row["id"])

@app.get("/zones/{zone_id}/collectibles", response_model=List[CollectibleOut])
def list_collectibles(zone_id: str):
    ensure_zone_exists(zone_id)
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT id, type, matrix FROM collectibles
        WHERE zone_id=? AND collected_at IS NULL
        ORDER BY created_at DESC
    """, (zone_id,))
    rows = cur.fetchall()
    con.close()
    out = []
    for r in rows:
        # matrix stored as comma-separated floats (simple)
        m = [float(x) for x in r["matrix"].split(",")]
        out.append(CollectibleOut(id=r["id"], type=r["type"], matrix=m))
    return out

@app.post("/zones/{zone_id}/collectibles", response_model=CollectibleOut)
def create_collectible(zone_id: str, req: CollectibleCreateRequest):
    """
    Distributor only (Placement Mode).
    """
    ensure_zone_exists(zone_id)
    cid = str(uuid.uuid4())
    matrix_str = ",".join(str(x) for x in req.matrix)
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO collectibles (id, zone_id, type, matrix, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (cid, zone_id, req.type, matrix_str, now_iso()))
    con.commit()
    con.close()
    return CollectibleOut(id=cid, type=req.type, matrix=req.matrix)

@app.post("/collectibles/{collectible_id}/collect", response_model=CollectResponse)
def collect_item(collectible_id: str, req: CollectRequest):
    """
    Atomic collect:
    - If already collected => ok:false
    - If GOLD => must pass goldAnswerCorrect=True to get points (temporary approach)
    """
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, zone_id, type, collected_at FROM collectibles WHERE id=?", (collectible_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        raise HTTPException(status_code=404, detail="Collectible not found")

    if row["collected_at"] is not None:
        con.close()
        return CollectResponse(ok=False, pointsAwarded=0, totalPoints=get_score(req.userId, row["zone_id"]))

    ctype = row["type"]
    zone_id = row["zone_id"]

    # GOLD validation (phase 1: client sends correctness)
    if ctype == "GOLD" and req.goldAnswerCorrect is not True:
        con.close()
        return CollectResponse(ok=False, pointsAwarded=0, totalPoints=get_score(req.userId, zone_id))

    # mark as collected
    cur.execute("""
        UPDATE collectibles
        SET collected_by=?, collected_at=?
        WHERE id=? AND collected_at IS NULL
    """, (req.userId, now_iso(), collectible_id))
    if cur.rowcount == 0:
        con.close()
        return CollectResponse(ok=False, pointsAwarded=0, totalPoints=get_score(req.userId, zone_id))

    # points mapping
    awarded = 10 if ctype == "UI" else 15 if ctype == "UX" else 100

    con.commit()
    con.close()

    total = add_points(req.userId, zone_id, awarded)
    return CollectResponse(ok=True, pointsAwarded=awarded, totalPoints=total)

@app.get("/zones/{zone_id}/leaderboard", response_model=List[LeaderboardEntry])
def leaderboard(zone_id: str, limit: int = 50):
    ensure_zone_exists(zone_id)
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT u.name as name, s.points as points
        FROM scores s
        JOIN users u ON u.id = s.user_id
        WHERE s.zone_id=?
        ORDER BY s.points DESC
        LIMIT ?
    """, (zone_id, limit))
    rows = cur.fetchall()
    con.close()
    return [LeaderboardEntry(name=r["name"], points=int(r["points"])) for r in rows]