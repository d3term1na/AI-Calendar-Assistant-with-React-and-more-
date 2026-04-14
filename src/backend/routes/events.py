from fastapi import APIRouter, Depends
from routes.users import get_current_user
import database

router = APIRouter()

@router.get("/events")
async def get_all_events(username: str = Depends(get_current_user)):
    return {"events": database.get_user_events(username)}