import os
import base64
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sqlalchemy import (
    create_engine, String, Integer, DateTime, Boolean, ForeignKey, Text,
    select, func, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, relationship


# ============================================================
# DB
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    # fallback local
    DATABASE_URL = "sqlite:///./app.db"

# Render Postgres sometimes uses "postgres://", SQLAlchemy needs "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)          # userId (uuid string from your logic)
    device_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="Guest")
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    claims = relationship("Claim", back_populates="user")


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="claims")


class Collectible(Base):
    __tablename__ = "collectibles"
    id: Mapped[str] = mapped_column(String, primary_key=True)          # collectible uuid string
    zone_id: Mapped[str] = mapped_column(String, index=True)           # ✅ zoneId can be UUID OR "suhail-4"
    type: Mapped[str] = mapped_column(String)                          # UI / UX / GOLD
    points: Mapped[int] = mapped_column(Integer)
    matrix_json: Mapped[str] = mapped_column(Text)                     # store 16 floats as JSON string
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Collected(Base):
    """
    Prevent user collecting same collectible twice
    """
    __tablename__ = "collected"
    __table_args__ = (UniqueConstraint("user_id", "collectible_id", name="uq_user_collectible"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    collectible_id: Mapped[str] = mapped_column(String, index=True)
    zone_id: Mapped[str] = mapped_column(String, index=True)
    awarded_points: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ZoneWorldMap(Base):
    """
    One WorldMap per zone (building-floor)
    """
    __tablename__ = "zone_worldmaps"
    zone_id: Mapped[str] = mapped_column(String, primary_key=True)
    map_b64: Mapped[str] = mapped_column(Text)  # base64 for ARWorldMap data
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


# ============================================================
# API Models
# ============================================================
class RegisterRequest(BaseModel):
    deviceId: str


class RegisterResponse(BaseModel):
    userId: str
    name: str
    isGuest: bool
    points: Optional[int] = 0


class ClaimRequest(BaseModel):
    userId: str
    name: str = Field(min_length=2, max_length=50)
    email: str = Field(min_length=5, max_length=120)  # ✅ no EmailStr dependency


class OkResponse(BaseModel):
    ok: bool


class AutoZoneReq(BaseModel):
    lat: float
    lng: float


class AutoZoneResp(BaseModel):
    zoneId: str
    joinCode: str


class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]


class CreateCollectibleReq(BaseModel):
    type: str
    points: int
    matrix: List[float]


class CollectResponse(BaseModel):
    points: int


class PointsResp(BaseModel):
    points: int


class LeaderboardEntry(BaseModel):
    name: str
    points: int


class WorldMapUpsertReq(BaseModel):
    mapBase64: str  # ARWorldMap as base64


class WorldMapResp(BaseModel):
    zoneId: str
    mapBase64: str
    updatedAt: str


# ============================================================
# APP
# ============================================================
app = FastAPI(title="UX GO API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Helpers
# ============================================================
def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def ensure_user(session: Session, user_id: str) -> User:
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    return u


# ============================================================
# Users
# ============================================================
@app.post("/users/register", response_model=RegisterResponse)
def register(body: RegisterRequest):
    import uuid
    with Session(engine) as session:
        # reuse same user for same deviceId if exists
        existing = session.execute(select(User).where(User.device_id == body.deviceId)).scalars().first()
        if existing:
            return RegisterResponse(userId=existing.id, name=existing.name, isGuest=existing.is_guest, points=0)

        uid = str(uuid.uuid4())
        u = User(id=uid, device_id=body.deviceId, name="Guest", is_guest=True)
        session.add(u)
        session.commit()
        return RegisterResponse(userId=u.id, name=u.name, isGuest=u.is_guest, points=0)


@app.post("/users/claim", response_model=OkResponse)
def claim(body: ClaimRequest):
    with Session(engine) as session:
        u = ensure_user(session, body.userId)

        # basic email sanity
        if "@" not in body.email or "." not in body.email:
            raise HTTPException(status_code=400, detail="invalid email")

        u.name = body.name
        u.email = body.email
        u.is_guest = False

        session.add(Claim(user_id=u.id, name=body.name, email=body.email))
        session.commit()
        return OkResponse(ok=True)


@app.get("/users/{userId}/points", response_model=PointsResp)
def user_points(userId: str, zoneId: str = Query(...)):
    with Session(engine) as session:
        ensure_user(session, userId)
        pts = session.execute(
            select(func.coalesce(func.sum(Collected.awarded_points), 0))
            .where(Collected.user_id == userId, Collected.zone_id == zoneId)
        ).scalar_one()
        return PointsResp(points=int(pts))


# ============================================================
# Zones
# ============================================================
@app.post("/zones/auto", response_model=AutoZoneResp)
def auto_zone(body: AutoZoneReq):
    # Optional: keep your current logic. For now: return fixed.
    # You can later map GPS -> building/floor if you want.
    return AutoZoneResp(zoneId="default-zone", joinCode="0000")


# ============================================================
# Collectibles
# ============================================================
@app.get("/zones/{zoneId}/collectibles", response_model=list[CollectibleDTO])
def list_collectibles(zoneId: str):
    import json
    with Session(engine) as session:
        rows = session.execute(select(Collectible).where(Collectible.zone_id == zoneId)).scalars().all()
        out: list[CollectibleDTO] = []
        for c in rows:
            out.append(
                CollectibleDTO(
                    id=c.id,
                    type=c.type,
                    points=c.points,
                    matrix=json.loads(c.matrix_json),
                )
            )
        return out


@app.post("/zones/{zoneId}/collectibles", response_model=CollectibleDTO)
def create_collectible(zoneId: str, body: CreateCollectibleReq):
    import uuid, json
    if len(body.matrix) != 16:
        raise HTTPException(status_code=400, detail="matrix must have 16 floats")

    with Session(engine) as session:
        cid = str(uuid.uuid4())
        c = Collectible(
            id=cid,
            zone_id=zoneId,
            type=body.type,
            points=int(body.points),
            matrix_json=json.dumps(body.matrix),
        )
        session.add(c)
        session.commit()
        return CollectibleDTO(id=c.id, type=c.type, points=c.points, matrix=body.matrix)


@app.post("/collectibles/{collectibleId}/collect", response_model=CollectResponse)
def collect(collectibleId: str, userId: str = Query(...)):
    with Session(engine) as session:
        u = ensure_user(session, userId)

        c = session.get(Collectible, collectibleId)
        if not c:
            raise HTTPException(status_code=404, detail="collectible not found")

        # prevent duplicate collect
        already = session.execute(
            select(Collected).where(Collected.user_id == u.id, Collected.collectible_id == collectibleId)
        ).scalars().first()
        if already:
            return CollectResponse(points=0)

        got = Collected(
            user_id=u.id,
            collectible_id=c.id,
            zone_id=c.zone_id,
            awarded_points=c.points
        )
        session.add(got)

        # optional: remove collectible after collected
        session.delete(c)

        session.commit()
        return CollectResponse(points=int(got.awarded_points))


# ============================================================
# Leaderboard
# ============================================================
@app.get("/zones/{zoneId}/leaderboard", response_model=list[LeaderboardEntry])
def leaderboard(zoneId: str):
    with Session(engine) as session:
        # sum per user in this zone
        rows = session.execute(
            select(Collected.user_id, func.coalesce(func.sum(Collected.awarded_points), 0).label("pts"))
            .where(Collected.zone_id == zoneId)
            .group_by(Collected.user_id)
            .order_by(func.sum(Collected.awarded_points).desc())
            .limit(50)
        ).all()

        out: list[LeaderboardEntry] = []
        for user_id, pts in rows:
            u = session.get(User, user_id)
            name = u.name if u and u.name else "Guest"
            out.append(LeaderboardEntry(name=name, points=int(pts)))
        return out


# ============================================================
# ✅ WorldMap (NEW) — per zone
# ============================================================
@app.post("/zones/{zoneId}/worldmap", response_model=OkResponse)
def upsert_worldmap(zoneId: str, body: WorldMapUpsertReq):
    # validate base64 quickly
    try:
        _ = base64.b64decode(body.mapBase64.encode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid base64")

    with Session(engine) as session:
        existing = session.get(ZoneWorldMap, zoneId)
        if existing:
            existing.map_b64 = body.mapBase64
            existing.updated_at = datetime.utcnow()
            session.add(existing)
        else:
            session.add(ZoneWorldMap(zone_id=zoneId, map_b64=body.mapBase64, updated_at=datetime.utcnow()))
        session.commit()
        return OkResponse(ok=True)


@app.get("/zones/{zoneId}/worldmap", response_model=WorldMapResp)
def get_worldmap(zoneId: str):
    with Session(engine) as session:
        wm = session.get(ZoneWorldMap, zoneId)
        if not wm:
            raise HTTPException(status_code=404, detail="worldmap not found")
        return WorldMapResp(zoneId=zoneId, mapBase64=wm.map_b64, updatedAt=wm.updated_at.isoformat() + "Z")
