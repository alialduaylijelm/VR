# main.py
import os
import uuid
import base64
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sqlalchemy import (
    create_engine,
    String,
    Integer,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, BYTEA, JSONB

# -----------------------------
# Database
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# Render/Neon: postgres:// -> postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Neon غالباً يحتاج SSL
if "sslmode=" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{sep}sslmode=require"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


# -----------------------------
# Models
# -----------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Guest")
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_guest: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)  # 1/0
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    collections: Mapped[List["Collection"]] = relationship(back_populates="user")


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    collectibles: Mapped[List["Collectible"]] = relationship(back_populates="zone")
    worldmaps: Mapped[List["WorldMap"]] = relationship(back_populates="zone")
    collections: Mapped[List["Collection"]] = relationship(back_populates="zone")


class Collectible(Base):
    __tablename__ = "collectibles"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Swift يسميها type (UI/UX/GOLD)
    type: Mapped[str] = mapped_column(String(16), nullable=False)

    points: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # مصفوفة 4x4 = 16 float
    matrix: Mapped[list] = mapped_column(JSONB, nullable=False)

    # ربط بـ Zone
    zone_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("zones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # اختياري: ربط بالخريطة/الدور
    world_map_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    zone: Mapped[Zone] = relationship(back_populates="collectibles")
    collections: Mapped[List["Collection"]] = relationship(back_populates="collectible")


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("user_id", "collectible_id", name="uq_user_collectible_once"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    collectible_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("collectibles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    zone_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("zones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    awarded_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="collections")
    collectible: Mapped[Collectible] = relationship(back_populates="collections")
    zone: Mapped[Zone] = relationship(back_populates="collections")


class WorldMap(Base):
    __tablename__ = "worldmaps"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("zones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False, default="default")
    data: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    zone: Mapped[Zone] = relationship(back_populates="worldmaps")


# -----------------------------
# Schemas
# -----------------------------
class RegisterReq(BaseModel):
    deviceId: str = Field(..., min_length=1)

class RegisterRes(BaseModel):
    userId: str
    name: str
    isGuest: bool
    points: Optional[int] = 0

class ClaimReq(BaseModel):
    userId: str
    name: str
    email: str

class OkRes(BaseModel):
    ok: bool

class CollectibleCreate(BaseModel):
    type: str
    points: int = 1
    matrix: List[float]
    worldMapId: Optional[str] = None

class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]
    worldMapId: Optional[str] = None

class LeaderboardEntry(BaseModel):
    name: str
    points: int

class UploadWorldMapReq(BaseModel):
    mapBase64: str

class WorldMapDTO(BaseModel):
    id: str
    name: str
    data: str  # base64

# -----------------------------
# App
# -----------------------------
app = FastAPI(title="VR API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def to_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a valid UUID")

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    print("✅ startup: tables created")
    # ... seed loop ...

    with SessionLocal() as db:
        count = db.query(Zone).count()
        print("✅ startup: zones count =", count)


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"ok": True, "time": datetime.utcnow().isoformat()}

# -----------------------------
# Users
# -----------------------------
@app.post("/users/register", response_model=RegisterRes)
def register(payload: RegisterReq):
    with SessionLocal() as db:
        u = db.query(User).filter(User.device_id == payload.deviceId).first()
        if not u:
            u = User(device_id=payload.deviceId, name="Guest", email=None, is_guest=1)
            db.add(u)
            db.commit()
            db.refresh(u)
        return RegisterRes(userId=str(u.id), name=u.name, isGuest=bool(u.is_guest), points=0)

@app.post("/users/claim", response_model=OkRes)
def claim(payload: ClaimReq):
    uid = to_uuid(payload.userId, "userId")
    with SessionLocal() as db:
        u = db.query(User).filter(User.id == uid).first()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.name = payload.name
        u.email = payload.email
        u.is_guest = 0
        u.updated_at = datetime.utcnow()
        db.commit()
        return OkRes(ok=True)

@app.get("/users/{user_id}/points")
def user_points(user_id: str, zoneId: str = Query(...)):
    uid = to_uuid(user_id, "userId")
    zid = to_uuid(zoneId, "zoneId")

    with SessionLocal() as db:
        u = db.query(User).filter(User.id == uid).first()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")

        # مجموع النقاط في هذا الـ zone فقط
        total = db.execute(
            text("""
                SELECT COALESCE(SUM(awarded_points), 0) AS total_points
                FROM collections
                WHERE user_id = :uid AND zone_id = :zid
            """),
            {"uid": str(uid), "zid": str(zid)}
        ).scalar_one()

        return {"points": int(total)}

@app.get("/users/{device_id}/score")
def user_score_by_device(device_id: str, zoneId: Optional[str] = None):
    with SessionLocal() as db:
        u = db.query(User).filter(User.device_id == device_id).first()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")

        params = {"uid": str(u.id)}
        sql = """
            SELECT COALESCE(SUM(awarded_points), 0) AS total_points
            FROM collections
            WHERE user_id = :uid
        """

        if zoneId:
            zid = to_uuid(zoneId, "zoneId")
            sql += " AND zone_id = :zid"
            params["zid"] = str(zid)

        total = db.execute(text(sql), params).scalar_one()
        return {
            "deviceId": device_id,
            "userId": str(u.id),
            "points": int(total)
        }

# -----------------------------
# Zones Auto (اختياري - Swift يناديه أحياناً)
# -----------------------------
@app.post("/zones/auto")
def auto_zone(payload: Dict[str, float]):
    # حالياً رجّع dummy (أنت تستخدم fixedZoneId)
    return {"zoneId": "", "joinCode": "0000"}

# -----------------------------
# Collectibles per Zone
# -----------------------------
@app.get("/zones/{zone_id}/collectibles", response_model=List[CollectibleDTO])
def list_collectibles(zone_id: str, worldMapId: Optional[str] = None):
    zid = to_uuid(zone_id, "zoneId")
    with SessionLocal() as db:
        q = db.query(Collectible).filter(Collectible.zone_id == zid)
        if worldMapId:
            q = q.filter(Collectible.world_map_id == worldMapId)
        items = q.order_by(Collectible.created_at.desc()).all()

        return [
            CollectibleDTO(
                id=str(x.id),
                type=x.type,
                points=x.points,
                matrix=list(x.matrix),
                worldMapId=x.world_map_id,
            )
            for x in items
        ]

@app.post("/zones/{zone_id}/collectibles", response_model=CollectibleDTO)
def create_collectible(zone_id: str, payload: CollectibleCreate):
    zid = to_uuid(zone_id, "zoneId")
    if len(payload.matrix) != 16:
        raise HTTPException(status_code=400, detail="matrix must be 16 floats")

    with SessionLocal() as db:
        z = db.query(Zone).filter(Zone.id == zid).first()
        if not z:
            raise HTTPException(status_code=404, detail="Zone not found")

        item = Collectible(
            type=payload.type,
            points=payload.points,
            matrix=payload.matrix,
            zone_id=zid,
            world_map_id=payload.worldMapId,
        )
        db.add(item)
        db.commit()
        db.refresh(item)

        return CollectibleDTO(
            id=str(item.id),
            type=item.type,
            points=item.points,
            matrix=list(item.matrix),
            worldMapId=item.world_map_id,
        )

# -----------------------------
# Collect action
# -----------------------------
@app.post("/collectibles/{collectible_id}/collect")
def collect(collectible_id: str, userId: str = Query(...)):
    cid = to_uuid(collectible_id, "collectibleId")
    uid = to_uuid(userId, "userId")

    with SessionLocal() as db:
        u = db.query(User).filter(User.id == uid).first()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")

        c = db.query(Collectible).filter(Collectible.id == cid).first()
        if not c:
            raise HTTPException(status_code=404, detail="Collectible not found")

        row = Collection(
            user_id=uid,
            collectible_id=cid,
            zone_id=c.zone_id,
            awarded_points=c.points,
            collected_at=datetime.utcnow(),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Already collected")

        return {"points": c.points}

# -----------------------------
# Leaderboard per Zone
# -----------------------------
@app.get("/zones/{zone_id}/leaderboard", response_model=List[LeaderboardEntry])
def leaderboard(zone_id: str, limit: int = 10):
    zid = to_uuid(zone_id, "zoneId")
    limit = max(1, min(limit, 100))

    with SessionLocal() as db:
        rows = db.execute(
            text("""
                SELECT u.name, COALESCE(SUM(c.awarded_points), 0) AS points
                FROM users u
                LEFT JOIN collections c
                  ON c.user_id = u.id AND c.zone_id = :zid
                GROUP BY u.id
                ORDER BY points DESC
                LIMIT :lim
            """),
            {"zid": str(zid), "lim": limit},
        ).mappings().all()

        return [LeaderboardEntry(name=r["name"], points=int(r["points"])) for r in rows]

# -----------------------------
# WorldMap endpoints (Swift expects /worldmap and /worldmaps)
# -----------------------------
@app.post("/zones/{zone_id}/worldmap", response_model=OkRes)
def upload_worldmap(zone_id: str, payload: UploadWorldMapReq):
    zid = to_uuid(zone_id, "zoneId")
    try:
        data = base64.b64decode(payload.mapBase64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64")

    with SessionLocal() as db:
        z = db.query(Zone).filter(Zone.id == zid).first()
        if not z:
            raise HTTPException(status_code=404, detail="Zone not found")

        wm = WorldMap(zone_id=zid, name=f"WorldMap {datetime.utcnow().isoformat()}", data=data)
        db.add(wm)
        db.commit()
        return OkRes(ok=True)

@app.get("/zones/{zone_id}/worldmap")
def fetch_worldmap(zone_id: str):
    zid = to_uuid(zone_id, "zoneId")
    with SessionLocal() as db:
        wm = (
            db.query(WorldMap)
            .filter(WorldMap.zone_id == zid)
            .order_by(WorldMap.created_at.desc())
            .first()
        )
        if not wm:
            raise HTTPException(status_code=404, detail="No worldmap for this zone")
        return {"mapBase64": base64.b64encode(wm.data).decode("utf-8")}

@app.get("/zones/{zone_id}/worldmaps", response_model=List[WorldMapDTO])
def list_worldmaps(zone_id: str):
    zid = to_uuid(zone_id, "zoneId")
    with SessionLocal() as db:
        items = (
            db.query(WorldMap)
            .filter(WorldMap.zone_id == zid)
            .order_by(WorldMap.created_at.desc())
            .all()
        )
        return [
            WorldMapDTO(
                id=str(x.id),
                name=x.name,
                data=base64.b64encode(x.data).decode("utf-8"),
            )
            for x in items
        ]
