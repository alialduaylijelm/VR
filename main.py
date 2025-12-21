from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Tuple
from uuid import uuid4
import time

app = FastAPI(title="UX GO Backend", version="1.0.0")

# ============================================================
# In-memory "DB" (بدّلها بـ Postgres لاحقًا)
# ============================================================

# users[userId] = { userId, name, isGuest, email, deviceId, createdAt }
users: Dict[str, dict] = {}

# zones[zoneId] = { zoneId, joinCode, createdAt }
zones: Dict[str, dict] = {}

# collectibles_by_zone[zoneId] = [ collectible, ... ]
# collectible = { id, type, points, matrix, worldMapId, createdAt }
collectibles_by_zone: Dict[str, List[dict]] = {}

# points[(userId, zoneId)] = int
points: Dict[Tuple[str, str], int] = {}

# ============================================================
# Helpers
# ============================================================

def now_ts() -> float:
    return time.time()

def ensure_zone(zone_id: str) -> dict:
    if zone_id not in zones:
        zones[zone_id] = {
            "zoneId": zone_id,
            "joinCode": zone_id[:6].upper(),
            "createdAt": now_ts(),
        }
    return zones[zone_id]

def safe_matrix(m: List[float]) -> None:
    if not isinstance(m, list) or len(m) != 16:
        raise HTTPException(status_code=400, detail="matrix must be 16 floats")

def display_name(u: dict) -> str:
    # إذا ما انعمل Claim، نطلع اسم لطيف
    if not u.get("isGuest", True) and u.get("name"):
        return u["name"]
    # Guest مع جزء من الـ id عشان يميّزه بالليدر بورد
    uid = u["userId"]
    return f"Guest-{uid[:4]}"

def find_collectible_any_zone(collectible_id: str) -> Tuple[str, dict]:
    for zone_id, items in collectibles_by_zone.items():
        for it in items:
            if it["id"] == collectible_id:
                return zone_id, it
    raise HTTPException(status_code=404, detail="collectible not found")

# ============================================================
# Models (نفس اللي Swift يتوقعه)
# ============================================================

class APIRegisterResponse(BaseModel):
    userId: str
    name: str
    isGuest: bool
    points: Optional[int] = None  # اختياري

class ClaimRequest(BaseModel):
    userId: str
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(min_length=3, max_length=120)  # بدون EmailStr لتفادي email-validator

class OkResponse(BaseModel):
    ok: bool

class AutoZoneBody(BaseModel):
    lat: float
    lng: float

class AutoZoneResponse(BaseModel):
    zoneId: str
    joinCode: str

class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]
    worldMapId: Optional[str] = None

class CreateCollectibleBody(BaseModel):
    type: str
    points: int
    matrix: List[float]
    worldMapId: Optional[str] = None  # ✅ NEW

class CollectResponse(BaseModel):
    points: int  # مجموع نقاط المستخدم داخل الزون (زي ما Swift يتعامل معه)

class LeaderboardEntry(BaseModel):
    name: str
    points: int

# ============================================================
# Users
# ============================================================

@app.post("/users/register", response_model=APIRegisterResponse)
def register_user(payload: dict):
    """
    Swift يرسل: { "deviceId": "..." }
    """
    device_id = payload.get("deviceId") if isinstance(payload, dict) else None
    if not device_id:
        raise HTTPException(status_code=400, detail="deviceId is required")

    # ✅ لو نفس الجهاز سجّلناه قبل، رجّع نفس المستخدم (مهم عشان ما يتغير userId كل مرة)
    for u in users.values():
        if u.get("deviceId") == device_id:
            return APIRegisterResponse(
                userId=u["userId"],
                name=u.get("name") or display_name(u),
                isGuest=bool(u.get("isGuest", True)),
                points=None,
            )

    user_id = str(uuid4())
    users[user_id] = {
        "userId": user_id,
        "deviceId": device_id,
        "isGuest": True,
        "name": "",
        "email": "",
        "createdAt": now_ts(),
    }

    return APIRegisterResponse(
        userId=user_id,
        name=display_name(users[user_id]),
        isGuest=True,
        points=None,
    )

@app.post("/users/claim", response_model=OkResponse)
def claim_user(req: ClaimRequest):
    """
    Swift يرسل: { userId, name, email }
    """
    u = users.get(req.userId)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")

    u["isGuest"] = False
    u["name"] = req.name.strip()
    u["email"] = req.email.strip().lower()

    return OkResponse(ok=True)

@app.get("/users/{userId}/points")
def get_user_points(userId: str, zoneId: str = Query(...)):
    """
    Swift ينادي: /users/{userId}/points?zoneId=...
    ويرجع JSON: { "points": رقم }
    """
    ensure_zone(zoneId)
    key = (userId, zoneId)
    return {"points": int(points.get(key, 0))}

# ============================================================
# Zones
# ============================================================

@app.post("/zones/auto", response_model=AutoZoneResponse)
def auto_zone(body: AutoZoneBody):
    """
    أنت حاليًا تستخدم Zone ثابت عندك في Swift.
    هذا endpoint موجود لو تبغى مستقبلًا تربطها بالموقع.
    """
    # مثال بسيط: نثبّت زون واحدة
    zone_id = "449a5601-cada-489f-8fcb-ed67a1a417ba"
    z = ensure_zone(zone_id)
    return AutoZoneResponse(zoneId=z["zoneId"], joinCode=z["joinCode"])

# ============================================================
# Collectibles
# ============================================================

@app.get("/zones/{zoneId}/collectibles", response_model=List[CollectibleDTO])
def list_collectibles(
    zoneId: str,
    worldMapId: Optional[str] = Query(default=None)  # ✅ NEW
):
    ensure_zone(zoneId)
    items = collectibles_by_zone.get(zoneId, [])

    # ✅ أهم شيء لحل مشكلة “الدور الرابع يظهر بالدور الأرضي”
    # لازم Swift يرسل worldMapId (المحفوظ/المسترجع) هنا
    if worldMapId:
        items = [x for x in items if x.get("worldMapId") == worldMapId]

    return items

@app.post("/zones/{zoneId}/collectibles", response_model=CollectibleDTO)
def create_collectible(zoneId: str, body: CreateCollectibleBody):
    ensure_zone(zoneId)
    safe_matrix(body.matrix)

    dto = {
        "id": str(uuid4()),
        "type": body.type,
        "points": int(body.points),
        "matrix": body.matrix,
        "worldMapId": body.worldMapId,  # ✅ NEW
        "createdAt": now_ts(),
    }
    collectibles_by_zone.setdefault(zoneId, []).append(dto)
    return dto

@app.post("/collectibles/{collectibleId}/collect", response_model=CollectResponse)
def collect_collectible(
    collectibleId: str,
    userId: str = Query(...),
):
    # ✅ FIX: إذا userId مو موجود (بسبب restart / in-memory) سجله تلقائيًا
    if userId not in users:
        users[userId] = {
            "userId": userId,
            "deviceId": "",       # ما عندنا هنا
            "isGuest": True,
            "name": "",
            "email": "",
            "createdAt": now_ts(),
        }

    zone_id, it = find_collectible_any_zone(collectibleId)

    collectibles_by_zone[zone_id] = [
        x for x in collectibles_by_zone[zone_id] if x["id"] != collectibleId
    ]

    gained = int(it.get("points", 0))
    key = (userId, zone_id)
    points[key] = int(points.get(key, 0)) + gained

    return CollectResponse(points=points[key])

# ============================================================
# Leaderboard
# ============================================================

@app.get("/zones/{zoneId}/leaderboard", response_model=List[LeaderboardEntry])
def leaderboard(zoneId: str):
    ensure_zone(zoneId)

    # اجمع نقاط كل المستخدمين في هذا الزون
    entries: List[LeaderboardEntry] = []
    for (uid, zid), pts in points.items():
        if zid != zoneId:
            continue
        u = users.get(uid)
        if not u:
            continue
        entries.append(LeaderboardEntry(name=display_name(u), points=int(pts)))

    # ترتيب تنازلي
    entries.sort(key=lambda x: x.points, reverse=True)

    # خذ أفضل 50
    return entries[:50]
