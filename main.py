import os
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr

from sqlalchemy import (
    create_engine,
    Column,
    String,
    Boolean,
    Integer,
    DateTime,
    UniqueConstraint,
    ForeignKey,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


# =========================
# Config
# =========================
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# Neon often requires SSL. If your DATABASE_URL already includes sslmode=require, it's fine.
# If not, you can append it. (Safe fallback)
if "sslmode=" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{sep}sslmode=require"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


# =========================
# DB Models (UUID everywhere)
# =========================
class User(Base):
    __tablename__ = "users"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String, unique=True, nullable=False)

    name = Column(String, nullable=False, default="Guest")
    email = Column(String, unique=True, nullable=True)

    is_guest = Column(Boolean, nullable=False, default=True)
    points = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    collections = relationship("Collection", back_populates="user", cascade="all, delete-orphan")


class Zone(Base):
    __tablename__ = "zones"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    join_code = Column(String, unique=True, nullable=False)

    lat = Column(String, nullable=True)
    lng = Column(String, nullable=True)
    radius_m = Column(Integer, nullable=False, default=50)

    name = Column(String, nullable=False, default="Zone")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    collections = relationship("Collection", back_populates="zone", cascade="all, delete-orphan")


class Collectible(Base):
    __tablename__ = "collectibles"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    word = Column(String, nullable=False)  # e.g. "UI" / "UX" / "GOLD"
    points = Column(Integer, nullable=False, default=10)

    created_at = Column(DateTime, nullable=False, server_default=func.now())

    collections = relationship("Collection", back_populates="collectible", cascade="all, delete-orphan")


class Collection(Base):
    """
    A user collects a collectible in a zone.
    NOTE: user_id, collectible_id, zone_id are UUID => matches referenced tables.
    """
    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("user_id", "collectible_id", name="uq_user_collectible_once"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    collectible_id = Column(PG_UUID(as_uuid=True), ForeignKey("collectibles.id", ondelete="CASCADE"), nullable=False)
    zone_id = Column(PG_UUID(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False)

    awarded_points = Column(Integer, nullable=False, default=0)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="collections")
    collectible = relationship("Collectible", back_populates="collections")
    zone = relationship("Zone", back_populates="collections")


# =========================
# Pydantic Schemas
# =========================
class UserUpsertRequest(BaseModel):
    device_id: str
    name: Optional[str] = "Guest"
    email: Optional[EmailStr] = None


class UserResponse(BaseModel):
    id: str
    device_id: str
    name: str
    email: Optional[str] = None
    is_guest: bool
    points: int
    created_at: datetime

    class Config:
        from_attributes = True


class ZoneCreateRequest(BaseModel):
    join_code: str
    name: Optional[str] = "Zone"
    lat: Optional[str] = None
    lng: Optional[str] = None
    radius_m: int = 50


class ZoneResponse(BaseModel):
    id: str
    join_code: str
    name: str
    lat: Optional[str] = None
    lng: Optional[str] = None
    radius_m: int
    created_at: datetime

    class Config:
        from_attributes = True


class CollectibleCreateRequest(BaseModel):
    word: str
    points: int = 10


class CollectibleResponse(BaseModel):
    id: str
    word: str
    points: int
    created_at: datetime

    class Config:
        from_attributes = True


class CollectRequest(BaseModel):
    device_id: str
    collectible_id: str  # UUID string
    zone_id: str         # UUID string


class CollectResponse(BaseModel):
    ok: bool
    awarded_points: int
    user_points: int
    message: str


class LeaderboardItem(BaseModel):
    user_id: str
    name: str
    points: int


# =========================
# App
# =========================
app = FastAPI(title="VR API", version="1.0")


@app.on_event("startup")
def on_startup():
    # Creates tables if not exist
    Base.metadata.create_all(bind=engine)


def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# Routes
# =========================
@app.get("/")
def root():
    return {"status": "ok", "service": "VR API"}


# ---- Users ----
@app.post("/users/upsert", response_model=UserResponse)
def upsert_user(payload: UserUpsertRequest):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.device_id == payload.device_id).first()
        if user:
            if payload.name:
                user.name = payload.name
            if payload.email:
                user.email = str(payload.email)
                user.is_guest = False
            db.commit()
            db.refresh(user)
            return user

        user = User(
            device_id=payload.device_id,
            name=payload.name or "Guest",
            email=str(payload.email) if payload.email else None,
            is_guest=(payload.email is None),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


@app.get("/users/by-device/{device_id}", response_model=UserResponse)
def get_user_by_device(device_id: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.device_id == device_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user
    finally:
        db.close()


# ---- Zones ----
@app.post("/zones", response_model=ZoneResponse)
def create_zone(payload: ZoneCreateRequest):
    db = SessionLocal()
    try:
        exists = db.query(Zone).filter(Zone.join_code == payload.join_code).first()
        if exists:
            raise HTTPException(status_code=409, detail="join_code already exists")

        zone = Zone(
            join_code=payload.join_code,
            name=payload.name or "Zone",
            lat=payload.lat,
            lng=payload.lng,
            radius_m=payload.radius_m,
        )
        db.add(zone)
        db.commit()
        db.refresh(zone)
        return zone
    finally:
        db.close()


@app.get("/zones", response_model=List[ZoneResponse])
def list_zones():
    db = SessionLocal()
    try:
        return db.query(Zone).order_by(Zone.created_at.desc()).all()
    finally:
        db.close()


# ---- Collectibles ----
@app.post("/collectibles", response_model=CollectibleResponse)
def create_collectible(payload: CollectibleCreateRequest):
    db = SessionLocal()
    try:
        item = Collectible(word=payload.word, points=payload.points)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    finally:
        db.close()


@app.get("/collectibles", response_model=List[CollectibleResponse])
def list_collectibles():
    db = SessionLocal()
    try:
        return db.query(Collectible).order_by(Collectible.created_at.desc()).all()
    finally:
        db.close()


# ---- Collect action ----
@app.post("/collect", response_model=CollectResponse)
def collect(payload: CollectRequest):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.device_id == payload.device_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        try:
            collectible_uuid = uuid.UUID(payload.collectible_id)
            zone_uuid = uuid.UUID(payload.zone_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid UUID for collectible_id or zone_id")

        collectible = db.query(Collectible).filter(Collectible.id == collectible_uuid).first()
        if not collectible:
            raise HTTPException(status_code=404, detail="Collectible not found")

        zone = db.query(Zone).filter(Zone.id == zone_uuid).first()
        if not zone:
            raise HTTPException(status_code=404, detail="Zone not found")

        # Ensure user can only collect each collectible once
        existing = (
            db.query(Collection)
            .filter(Collection.user_id == user.id, Collection.collectible_id == collectible.id)
            .first()
        )
        if existing:
            return CollectResponse(
                ok=False,
                awarded_points=0,
                user_points=user.points,
                message="Already collected this item before",
            )

        awarded = collectible.points

        record = Collection(
            user_id=user.id,
            collectible_id=collectible.id,
            zone_id=zone.id,
            awarded_points=awarded,
            collected_at=datetime.utcnow(),
        )
        db.add(record)

        user.points += awarded

        db.commit()
        db.refresh(user)

        return CollectResponse(
            ok=True,
            awarded_points=awarded,
            user_points=user.points,
            message="Collected successfully",
        )
    finally:
        db.close()


# ---- Leaderboard ----
@app.get("/leaderboard", response_model=List[LeaderboardItem])
def leaderboard(limit: int = 50):
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.points.desc(), User.created_at.asc()).limit(limit).all()
        return [
            LeaderboardItem(user_id=str(u.id), name=u.name, points=u.points)
            for u in users
        ]
    finally:
        db.close()
