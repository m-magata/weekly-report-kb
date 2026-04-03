"""
POST /upload  : Excelファイルをアップロードしてパース・DB保存する
GET  /reports : 保存済み週報一覧を返す
"""
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from supabase import Client

from backend.database import get_client
from backend.crud import save_parsed_report
from backend.parser.excel_parser import parse_excel

router = APIRouter()

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xls"}


class UploadResult(BaseModel):
    store_name: str
    week_start: str
    week_end: str
    daily_sales_count: int
    report_texts_count: int
    weekly_report_id: int


class ReportSummary(BaseModel):
    id: int
    store_name: str
    week_start: str
    week_end: str
    source_filename: str | None
    submitter_role: str = "店長"
    area_id: int | None = None


@router.post("/upload", response_model=UploadResult)
async def upload_report(
    file: UploadFile = File(...),
    client: Client = Depends(get_client),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"対応拡張子は {ALLOWED_EXTENSIONS} のみです。",
        )

    save_path = DATA_DIR / file.filename
    with save_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        parsed = parse_excel(save_path)
    except Exception as e:
        save_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Excelパースに失敗しました: {e}")

    try:
        wr = save_parsed_report(client, parsed)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB保存に失敗しました: {e}")

    return UploadResult(
        store_name=parsed.store_name,
        week_start=str(parsed.week_start),
        week_end=str(parsed.week_end),
        daily_sales_count=len(parsed.daily_sales),
        report_texts_count=len(parsed.report_texts),
        weekly_report_id=wr["id"],
    )


@router.get("/reports", response_model=list[ReportSummary])
def list_reports(area_id: int | None = None, client: Client = Depends(get_client)):
    res = (
        client.table("weekly_reports")
        .select("id, week_start, week_end, source_filename, submitter_role, m_store(store_name, area_id)")
        .order("week_start", desc=True)
        .execute()
    )

    reports = [
        ReportSummary(
            id=row["id"],
            store_name=row["m_store"]["store_name"],
            week_start=row["week_start"],
            week_end=row["week_end"],
            source_filename=row.get("source_filename"),
            submitter_role=row.get("submitter_role", "店長"),
            area_id=row["m_store"].get("area_id"),
        )
        for row in res.data
    ]

    # area_id が指定された場合はサーバー側でフィルタリング
    if area_id is not None:
        reports = [r for r in reports if r.area_id == area_id]

    return reports
