# main.py
import os
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException
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
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.exc import IntegrityError


# -----------------------------
# Database
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# Neon غالباً يحتاج SSL
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "sslmode=" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{sep}sslmode=require"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


# -----------------------------
# Models (SQLAlchemy)
# -----------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Guest")
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # بدون تحقق
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    collections: Mapped[List["Collection"]] = relationship(back_populates="user")


class Collectible(Base):
    __tablename__ = "collectibles"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    word: Mapped[str] = mapped_column(String(64), nullable=False)  # UI / UX / GOLD
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    collections: Mapped[List["Collection"]] = relationship(back_populates="collectible")


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    collections: Mapped[List["Collection"]] = relationship(back_populates="zone")


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


# -----------------------------
# Schemas (Pydantic)
# -----------------------------
class UserUpsertRequest(BaseModel):
    device_id: str = Field(..., min_length=1)
    name: Optional[str] = "Guest"
    email: Optional[str] = None  # بدون EmailStr

class UserResponse(BaseModel):
    id: str
    device_id: str
    name: str
    email: Optional[str] = None

class CollectibleCreate(BaseModel):
    word: str
    points: int = 1

class ZoneCreate(BaseModel):
    name: str
    description: Optional[str] = None

class CollectRequest(BaseModel):
    device_id: str
    collectible_id: str
    zone_id: str


# -----------------------------
# App
# -----------------------------
app = FastAPI(title="VR API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # عدّلها إذا تبي أمان أكثر
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    # ينشئ الجداول إذا ما كانت موجودة
    Base.metadata.create_all(bind=engine)

    # Seed بسيط لو فاضي
    with SessionLocal() as db:
        any_collectible = db.query(Collectible).first()
        any_zone = db.query(Zone).first()

        if not any_collectible:
            db.add_all([
                Collectible(word="UI", points=1),
                Collectible(word="UX", points=1),
                Collectible(word="GOLD", points=5),
            ])
        if not any_zone:
            db.add_all([
                Zone(name="Clock Tower", description="Makkah vibes"),
                Zone(name="Elm HQ", description="Office zone"),
            ])
        db.commit()


# -----------------------------
# Helpers
# -----------------------------
def to_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a valid UUID")


# -----------------------------
# Routes
# -----------------------------
@app.get("/health")
def health() -> Dict[str, Any]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"ok": True, "time": datetime.utcnow().isoformat()}


@app.post("/users/upsert", response_model=UserResponse)
def upsert_user(payload: UserUpsertRequest):
    with SessionLocal() as db:
        user = db.query(User).filter(User.device_id == payload.device_id).first()
        if user:
            user.name = payload.name or user.name
            user.email = payload.email  # بدون تحقق
            user.updated_at = datetime.utcnow()
        else:
            user = User(
                device_id=payload.device_id,
                name=payload.name or "Guest",
                email=payload.email,
            )
            db.add(user)

        db.commit()
        db.refresh(user)
        return UserResponse(
            id=str(user.id),
            device_id=user.device_id,
            name=user.name,
            email=user.email,
        )


@app.get("/collectibles")
def list_collectibles():
    with SessionLocal() as db:
        items = db.query(Collectible).all()
        return [{"id": str(x.id), "word": x.word, "points": x.points} for x in items]


@app.post("/collectibles")
def create_collectible(payload: CollectibleCreate):
    with SessionLocal() as db:
        item = Collectible(word=payload.word, points=payload.points)
        db.add(item)
        db.commit()
        db.refresh(item)
        return {"id": str(item.id), "word": item.word, "points": item.points}


@app.get("/zones")
def list_zones():
    with SessionLocal() as db:
        zones = db.query(Zone).all()
        return [{"id": str(z.id), "name": z.name, "description": z.description} for z in zones]


@app.post("/zones")
def create_zone(payload: ZoneCreate):
    with SessionLocal() as db:
        z = Zone(name=payload.name, description=payload.description)
        db.add(z)
        db.commit()
        db.refresh(z)
        return {"id": str(z.id), "name": z.name, "description": z.description}


@app.post("/collect")
def collect(payload: CollectRequest):
    user_id = None
    collectible_uuid = to_uuid(payload.collectible_id, "collectible_id")
    zone_uuid = to_uuid(payload.zone_id, "zone_id")

    with SessionLocal() as db:
        user = db.query(User).filter(User.device_id == payload.device_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found (device_id)")

        collectible = db.query(Collectible).filter(Collectible.id == collectible_uuid).first()
        if not collectible:
            raise HTTPException(status_code=404, detail="Collectible not found")

        zone = db.query(Zone).filter(Zone.id == zone_uuid).first()
        if not zone:
            raise HTTPException(status_code=404, detail="Zone not found")

        row = Collection(
            user_id=user.id,
            collectible_id=collectible.id,
            zone_id=zone.id,
            awarded_points=collectible.points,
            collected_at=datetime.utcnow(),
        )
        db.add(row)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # هذا يعني نفس user جمع نفس collectible قبل (UniqueConstraint)
            raise HTTPException(status_code=409, detail="Already collected")

        return {
            "ok": True,
            "awarded_points": collectible.points,
            "collectible": {"id": str(collectible.id), "word": collectible.word},
            "zone": {"id": str(zone.id), "name": zone.name},
        }


@app.get("/users/{device_id}/score")
def get_score(device_id: str):
    with SessionLocal() as db:
        user = db.query(User).filter(User.device_id == device_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        total = db.execute(
            text(
                """
                SELECT COALESCE(SUM(awarded_points), 0) AS total_points
                FROM collections
                WHERE user_id = :uid
                """
            ),
            {"uid": str(user.id)},
        ).scalar_one()

        return {"device_id": device_id, "user_id": str(user.id), "points": int(total)}


@app.get("/leaderboard")
def leaderboard(limit: int = 10):
    limit = max(1, min(limit, 100))
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT u.name, u.device_id, COALESCE(SUM(c.awarded_points), 0) AS points
                FROM users u
                LEFT JOIN collections c ON c.user_id = u.id
                GROUP BY u.id
                ORDER BY points DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        ).mappings().all()

        return [{"name": r["name"], "device_id": r["device_id"], "points": int(r["points"])} for r in rows]
