# main.py
import os
import base64
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sqlalchemy import (
    create_engine,
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Text,
    LargeBinary,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, relationship


# ============================================================
# CONFIG
# ============================================================
DATABASE_URL = os.environ.get("DATABASE_URL")  # Render يوفّرها عادة
if not DATABASE_URL:
    # مثال محلي:
    # export DATABASE_URL="postgresql+psycopg2://user:pass@localhost:5432/uxgo"
    raise RuntimeError("DATABASE_URL is not set")

# Render أحيانًا يرسل postgres:// لازم تتحول لـ postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ============================================================
# DB MODELS
# ============================================================
class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(120), default="Guest")
    email: Mapped[Optional[str]] = mapped_column(String(190), nullable=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    collections: Mapped[List["Collection"]] = relationship(back_populates="user")


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    join_code: Mapped[str] = mapped_column(String(12), index=True, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    collectibles: Mapped[List["Collectible"]] = relationship(back_populates="zone")


class Collectible(Base):
    __tablename__ = "collectibles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    zone_id: Mapped[str] = mapped_column(String(36), ForeignKey("zones.id"), index=True)

    type: Mapped[str] = mapped_column(String(10))  # UI / UX / GOLD
    points: Mapped[int] = mapped_column(Integer, default=0)

    # نخزن المصفوفة كسلسلة نصية "comma-separated" لتبسيط DB
    matrix_csv: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    zone: Mapped["Zone"] = relationship(back_populates="collectibles")
    collections: Mapped[List["Collection"]] = relationship(back_populates="collectible")


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    collectible_id: Mapped[str] = mapped_column(String(36), ForeignKey("collectibles.id"), index=True)
    zone_id: Mapped[str] = mapped_column(String(36), ForeignKey("zones.id"), index=True)

    awarded_points: Mapped[int] = mapped_column(Integer, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "collectible_id", name="uq_user_collectible_once"),
    )

    user: Mapped["User"] = relationship(back_populates="collections")
    collectible: Mapped["Collectible"] = relationship(back_populates="collections")


class WorldMap(Base):
    """
    نخزن ARWorldMap كـ bytes (base64 في API)
    ويمكن نخزن نسخ مختلفة حسب building/floor
    """
    __tablename__ = "worldmaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_id: Mapped[str] = mapped_column(String(36), ForeignKey("zones.id"), index=True)
    building: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    floor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    blob: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("zone_id", "building", "floor", name="uq_worldmap_key"),
    )


# ============================================================
# Pydantic Schemas
# ============================================================
class RegisterRequest(BaseModel):
    deviceId: str = Field(..., min_length=2)


class RegisterResponse(BaseModel):
    userId: str
    name: str
    isGuest: bool
    points: Optional[int] = None


class ClaimRequest(BaseModel):
    userId: str
    name: str = Field(..., min_length=2, max_length=120)
    email: str = Field(..., min_length=5, max_length=190)  # بدون EmailStr عشان ما تحتاج email-validator


class OkResponse(BaseModel):
    ok: bool


class CollectibleCreateRequest(BaseModel):
    type: str  # UI/UX/GOLD
    points: int
    matrix: List[float]


class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]


class CollectResponse(BaseModel):
    points: int  # للـ Swift (APICollectResponse) يدعم points/awardedPoints، هنا نخليها points


class LeaderboardEntry(BaseModel):
    name: str
    points: int


class WorldMapUpsertRequest(BaseModel):
    # base64 للـ ARWorldMap data
    worldMapB64: str
    building: Optional[str] = None
    floor: Optional[int] = None


class WorldMapResponse(BaseModel):
    zoneId: str
    building: Optional[str] = None
    floor: Optional[int] = None
    worldMapB64: str
    updatedAt: datetime


# ============================================================
# Helpers
# ============================================================
def new_uuid() -> str:
    return str(uuid.uuid4())


def matrix_to_csv(m: List[float]) -> str:
    if len(m) != 16:
        raise HTTPException(status_code=400, detail="matrix must have 16 floats")
    return ",".join(str(float(x)) for x in m)


def csv_to_matrix(s: str) -> List[float]:
    parts = s.split(",")
    return [float(x) for x in parts]


def get_or_create_default_zone(db, zone_id: Optional[str] = None) -> Zone:
    """
    إذا عندك zone ثابت من Swift خلّه موجود.
    """
    if zone_id:
        z = db.get(Zone, zone_id)
        if z:
            return z
        z = Zone(id=zone_id, join_code="DEFAULT", name="Default Zone")
        db.add(z)
        db.commit()
        db.refresh(z)
        return z

    # fallback: أول zone
    z = db.query(Zone).first()
    if z:
        return z

    z = Zone(id=new_uuid(), join_code="DEFAULT", name="Default Zone")
    db.add(z)
    db.commit()
    db.refresh(z)
    return z


# ============================================================
# FastAPI App
# ============================================================
app = FastAPI(title="UX GO Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # عدلها لاحقًا
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"ok": True}


# ============================================================
# USERS
# ============================================================
@app.post("/users/register", response_model=RegisterResponse)
def register(req: RegisterRequest):
    db = SessionLocal()
    try:
        # إذا نفس الجهاز رجع نفس المستخدم (عشان ما تتكرر userId)
        existing = db.query(User).filter(User.device_id == req.deviceId).first()
        if existing:
            return RegisterResponse(
                userId=existing.id,
                name=existing.name,
                isGuest=existing.is_guest,
                points=None,
            )

        u = User(
            id=new_uuid(),
            device_id=req.deviceId,
            name="Guest",
            is_guest=True,
        )
        db.add(u)
        db.commit()
        return RegisterResponse(userId=u.id, name=u.name, isGuest=u.is_guest, points=None)
    finally:
        db.close()


@app.post("/users/claim", response_model=OkResponse)
def claim(req: ClaimRequest):
    db = SessionLocal()
    try:
        u = db.get(User, req.userId)
        if not u:
            raise HTTPException(status_code=404, detail="user not found")

        u.name = req.name.strip()
        u.email = req.email.strip().lower()
        u.is_guest = False

        db.commit()
        return OkResponse(ok=True)
    finally:
        db.close()


@app.get("/users/{user_id}/points")
def user_points(user_id: str, zoneId: str = Query(...)):
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        if not u:
            raise HTTPException(status_code=404, detail="user not found")

        # مجموع نقاط المستخدم في هذا الزون
        total = (
            db.query(func.coalesce(func.sum(Collection.awarded_points), 0))
            .filter(Collection.user_id == user_id, Collection.zone_id == zoneId)
            .scalar()
        )
        return {"points": int(total or 0)}
    finally:
        db.close()


# ============================================================
# ZONES + COLLECTIBLES
# ============================================================
@app.get("/zones/{zone_id}/collectibles", response_model=List[CollectibleDTO])
def list_collectibles(zone_id: str):
    db = SessionLocal()
    try:
        z = db.get(Zone, zone_id)
        if not z:
            raise HTTPException(status_code=404, detail="zone not found")

        items = db.query(Collectible).filter(Collectible.zone_id == zone_id).all()
        return [
            CollectibleDTO(
                id=it.id,
                type=it.type,
                points=it.points,
                matrix=csv_to_matrix(it.matrix_csv),
            )
            for it in items
        ]
    finally:
        db.close()


@app.post("/zones/{zone_id}/collectibles", response_model=CollectibleDTO)
def create_collectible(zone_id: str, req: CollectibleCreateRequest):
    db = SessionLocal()
    try:
        z = db.get(Zone, zone_id)
        if not z:
            # لو تبغى يسمح بإنشاء zone تلقائي:
            z = get_or_create_default_zone(db, zone_id)

        t = req.type.strip().upper()
        if t not in ("UI", "UX", "GOLD"):
            raise HTTPException(status_code=400, detail="type must be UI/UX/GOLD")

        it = Collectible(
            id=new_uuid(),
            zone_id=zone_id,
            type=t,
            points=int(req.points),
            matrix_csv=matrix_to_csv(req.matrix),
        )
        db.add(it)
        db.commit()
        db.refresh(it)

        return CollectibleDTO(
            id=it.id,
            type=it.type,
            points=it.points,
            matrix=csv_to_matrix(it.matrix_csv),
        )
    finally:
        db.close()


@app.post("/collectibles/{collectible_id}/collect", response_model=CollectResponse)
def collect(collectible_id: str, userId: str = Query(...)):
    db = SessionLocal()
    try:
        u = db.get(User, userId)
        if not u:
            raise HTTPException(status_code=404, detail="user not found")

        c = db.get(Collectible, collectible_id)
        if not c:
            raise HTTPException(status_code=404, detail="collectible not found")

        # منع جمع نفس العنصر مرتين من نفس المستخدم
        existing = (
            db.query(Collection)
            .filter(Collection.user_id == userId, Collection.collectible_id == collectible_id)
            .first()
        )
        if existing:
            # ترجع 0 (بدون كراش)
            return CollectResponse(points=0)

        col = Collection(
            user_id=userId,
            collectible_id=collectible_id,
            zone_id=c.zone_id,
            awarded_points=c.points,
        )
        db.add(col)

        # (اختياري) بعد ما ينحفظ التجميع نحذف العنصر من الأرض عشان يختفي للجميع
        # لو تبغى يخفي بعد أول من يجمعه:
        db.delete(c)

        db.commit()
        return CollectResponse(points=int(col.awarded_points))
    finally:
        db.close()


@app.get("/zones/{zone_id}/leaderboard", response_model=List[LeaderboardEntry])
def leaderboard(zone_id: str):
    db = SessionLocal()
    try:
        z = db.get(Zone, zone_id)
        if not z:
            raise HTTPException(status_code=404, detail="zone not found")

        # نجمع نقاط كل مستخدم في الزون
        rows = (
            db.query(
                User.name.label("name"),
                func.coalesce(func.sum(Collection.awarded_points), 0).label("points"),
            )
            .join(Collection, Collection.user_id == User.id)
            .filter(Collection.zone_id == zone_id)
            .group_by(User.id, User.name)
            .order_by(func.sum(Collection.awarded_points).desc())
            .limit(50)
            .all()
        )

        return [LeaderboardEntry(name=r.name, points=int(r.points)) for r in rows]
    finally:
        db.close()


# ============================================================
# WORLDMAP (ARWorldMap)
# ============================================================
@app.post("/zones/{zone_id}/worldmap", response_model=OkResponse)
def upsert_worldmap(zone_id: str, req: WorldMapUpsertRequest):
    db = SessionLocal()
    try:
        z = db.get(Zone, zone_id)
        if not z:
            z = get_or_create_default_zone(db, zone_id)

        try:
            blob = base64.b64decode(req.worldMapB64.encode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="invalid base64")

        wm = (
            db.query(WorldMap)
            .filter(
                WorldMap.zone_id == zone_id,
                WorldMap.building == req.building,
                WorldMap.floor == req.floor,
            )
            .first()
        )

        if wm:
            wm.blob = blob
            wm.updated_at = datetime.utcnow()
        else:
            wm = WorldMap(
                zone_id=zone_id,
                building=req.building,
                floor=req.floor,
                blob=blob,
                updated_at=datetime.utcnow(),
            )
            db.add(wm)

        db.commit()
        return OkResponse(ok=True)
    finally:
        db.close()


@app.get("/zones/{zone_id}/worldmap", response_model=WorldMapResponse)
def get_worldmap(
    zone_id: str,
    building: Optional[str] = Query(None),
    floor: Optional[int] = Query(None),
):
    db = SessionLocal()
    try:
        wm = (
            db.query(WorldMap)
            .filter(
                WorldMap.zone_id == zone_id,
                WorldMap.building == building,
                WorldMap.floor == floor,
            )
            .first()
        )
        if not wm:
            raise HTTPException(status_code=404, detail="worldmap not found")

        b64 = base64.b64encode(wm.blob).decode("utf-8")
        return WorldMapResponse(
            zoneId=zone_id,
            building=wm.building,
            floor=wm.floor,
            worldMapB64=b64,
            updatedAt=wm.updated_at,
        )
    finally:
        db.close()
