# main.py
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Optional
from uuid import uuid4

app = FastAPI()

# ----------------------------
# In-memory stores (استبدلها ب DB عندك)
# ----------------------------
collectibles_by_zone: Dict[str, List[dict]] = {}
user_points: Dict[tuple, int] = {}  # key = (userId, zoneId)

# ----------------------------
# Models
# ----------------------------
class CreateCollectibleBody(BaseModel):
    type: str
    points: int
    matrix: List[float]
    worldMapId: Optional[str] = None  # ✅ NEW

class CollectibleDTO(BaseModel):
    id: str
    type: str
    points: int
    matrix: List[float]
    worldMapId: Optional[str] = None  # ✅ NEW

class CollectResponse(BaseModel):
    points: int

# ----------------------------
# Endpoints
# ----------------------------
@app.get("/zones/{zoneId}/collectibles", response_model=List[CollectibleDTO])
def list_collectibles(zoneId: str, worldMapId: Optional[str] = Query(default=None)):
    items = collectibles_by_zone.get(zoneId, [])

    # ✅ فلترة حسب الخريطة إذا انرسلت
    if worldMapId:
        items = [x for x in items if x.get("worldMapId") == worldMapId]

    return items

@app.post("/zones/{zoneId}/collectibles", response_model=CollectibleDTO)
def create_collectible(zoneId: str, body: CreateCollectibleBody):
    if len(body.matrix) != 16:
        raise HTTPException(status_code=400, detail="matrix must be 16 floats")

    dto = {
        "id": str(uuid4()),
        "type": body.type,
        "points": body.points,
        "matrix": body.matrix,
        "worldMapId": body.worldMapId,  # ✅ NEW
    }
    collectibles_by_zone.setdefault(zoneId, []).append(dto)
    return dto

@app.post("/collectibles/{collectibleId}/collect", response_model=CollectResponse)
def collect(collectibleId: str, userId: str = Query(...), zoneId: Optional[str] = Query(default=None)):
    # ملاحظة: أنت عندك زون من قبل. لو تبي تأكد من الزون هنا، مرر zoneId وحقق.
    # (حالياً نحاول نلقاه في كل الزونات)
    found_zone = None
    found = None
    for z, items in collectibles_by_zone.items():
        for it in items:
            if it["id"] == collectibleId:
                found_zone = z
                found = it
                break
        if found:
            break
    if not found or not found_zone:
        raise HTTPException(status_code=404, detail="collectible not found")

    # احذف العنصر (انجمع)
    collectibles_by_zone[found_zone] = [x for x in collectibles_by_zone[found_zone] if x["id"] != collectibleId]

    gained = int(found["points"])
    key = (userId, found_zone)
    user_points[key] = user_points.get(key, 0) + gained
    return {"points": user_points[key]}
