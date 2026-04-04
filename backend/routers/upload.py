"""
POST /upload        : Excelファイルをアップロードしてパース・DB保存する
POST /upload/batch  : 複数ファイルを一括処理し NDJSON ストリームで結果を返す
GET  /reports       : 保存済み週報一覧を返す
"""
import json as _json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from supabase import Client

from backend.database import get_client
from backend.crud import save_parsed_report
from backend.parser.excel_parser import parse_excel, SkipFileError

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
    store_code: str | None = None
    report_year: int | None = None
    report_month: int | None = None


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


@router.post("/upload/batch")
async def upload_batch(
    files: list[UploadFile] = File(...),
    client: Client = Depends(get_client),
):
    """
    複数ファイルを一括アップロード・DB 取込し、NDJSON ストリームで結果を返す。
    登録済みファイル（source_filename 一致）はスキップ。
    """
    # 登録済みファイル名セット（DUP チェック用）
    dup_res    = client.table("weekly_reports").select("source_filename").execute()
    registered = {r["source_filename"] for r in dup_res.data if r["source_filename"]}

    # 全ファイルをまず data/ に保存してからストリーム処理
    saved: list[dict] = []
    for f in files:
        fname  = f.filename or ""
        suffix = Path(fname).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            saved.append({"fname": fname, "path": None, "error": f"対応外の拡張子: {suffix}"})
            continue
        save_path = DATA_DIR / fname
        with save_path.open("wb") as fp:
            shutil.copyfileobj(f.file, fp)
        saved.append({"fname": fname, "path": save_path, "error": None})

    def _stream():
        for item in saved:
            fname = item["fname"] or "unknown"
            if item["error"]:
                yield _json.dumps(
                    {"filename": fname, "status": "err", "reason": item["error"]},
                    ensure_ascii=False,
                ) + "\n"
                continue
            if fname in registered:
                yield _json.dumps(
                    {"filename": fname, "status": "dup", "reason": "登録済みのためスキップ"},
                    ensure_ascii=False,
                ) + "\n"
                continue
            try:
                parsed = parse_excel(item["path"])
                save_parsed_report(client, parsed)
                role_label = "" if parsed.submitter_role == "店長" else f" [{parsed.submitter_role}]"
                yield _json.dumps(
                    {
                        "filename":   fname,
                        "status":     "ok",
                        "store_name": parsed.store_name + role_label,
                        "week_start": str(parsed.week_start),
                        "week_end":   str(parsed.week_end),
                    },
                    ensure_ascii=False,
                ) + "\n"
            except SkipFileError as e:
                yield _json.dumps(
                    {"filename": fname, "status": "skip", "reason": str(e)},
                    ensure_ascii=False,
                ) + "\n"
            except Exception as e:
                yield _json.dumps(
                    {"filename": fname, "status": "err", "reason": str(e)},
                    ensure_ascii=False,
                ) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@router.get("/reports", response_model=list[ReportSummary])
def list_reports(area_id: int | None = None, client: Client = Depends(get_client)):
    res = (
        client.table("weekly_reports")
        .select("id, week_start, week_end, source_filename, submitter_role, report_year, report_month, m_store(store_name, area_id, store_code)")
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
            store_code=row["m_store"].get("store_code"),
            report_year=row.get("report_year"),
            report_month=row.get("report_month"),
        )
        for row in res.data
    ]

    # area_id が指定された場合はサーバー側でフィルタリング
    if area_id is not None:
        reports = [r for r in reports if r.area_id == area_id]

    # store_code 昇順でソート（None は末尾）
    reports.sort(key=lambda r: r.store_code or "\uffff")

    return reports
