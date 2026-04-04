from fastapi import APIRouter, Depends
from pydantic import BaseModel
from supabase import Client
from backend.database import get_client

router = APIRouter(prefix="/api/stores")

class StoreItem(BaseModel):
    store_id: int
    store_name: str
    store_code: str | None = None

@router.get("/all", response_model=list[StoreItem])
def get_all_stores(client: Client = Depends(get_client)):
    res = (
        client.table("m_store")
        .select("store_id, store_name, store_code")
        .order("store_code")
        .execute()
    )
    return res.data
