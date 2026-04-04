"""
週報詳細データ API
GET /reports/{id}/daily-sales  : 日別売上・客数・天候
GET /reports/{id}/texts        : 週報テキスト一覧
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from backend.database import get_client

router = APIRouter(prefix="/reports")


class DailySalesItem(BaseModel):
    date: str
    sales_amount: float | None
    customer_count: int | None
    weather: str | None
    sales_budget_ratio: float | None = None
    customer_count_prev_year: int | None = None


class ReportTextItem(BaseModel):
    sheet_index: int
    sheet_name: str
    content: str | None
    has_highlight: bool = False


@router.get("/{report_id}/daily-sales", response_model=list[DailySalesItem])
def get_daily_sales(report_id: int, client: Client = Depends(get_client)):
    res = (
        client.table("daily_sales")
        .select("date, sales_amount, customer_count, weather, sales_budget_ratio, customer_count_prev_year")
        .eq("weekly_report_id", report_id)
        .order("date")
        .execute()
    )
    if not res.data and report_id:
        # 存在確認
        chk = client.table("weekly_reports").select("id").eq("id", report_id).execute()
        if not chk.data:
            raise HTTPException(status_code=404, detail="週報が見つかりません")
    return res.data


@router.get("/{report_id}/texts", response_model=list[ReportTextItem])
def get_report_texts(report_id: int, client: Client = Depends(get_client)):
    res = (
        client.table("report_texts")
        .select("sheet_index, sheet_name, content, has_highlight")
        .eq("weekly_report_id", report_id)
        .order("sheet_index")
        .execute()
    )
    return res.data
