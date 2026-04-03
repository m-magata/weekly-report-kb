"""
GET /search?q=キーワード&month=2026-03  週報テキスト全文検索
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from supabase import Client

from backend.database import get_client

router = APIRouter()

SNIPPET_BEFORE = 40
SNIPPET_AFTER  = 80
MAX_RESULTS    = 50


class SearchResult(BaseModel):
    report_id: int
    store_name: str
    week_start: str
    week_end: str
    sheet_index: int
    sheet_name: str
    snippet: str


def _make_snippet(content: str, keyword: str) -> str:
    idx = content.lower().find(keyword.lower())
    if idx == -1:
        return content[:120]
    start = max(0, idx - SNIPPET_BEFORE)
    end   = min(len(content), idx + len(keyword) + SNIPPET_AFTER)
    snip  = content[start:end]
    if start > 0:
        snip = "…" + snip
    if end < len(content):
        snip = snip + "…"
    return snip


@router.get("/search", response_model=list[SearchResult])
def search_texts(
    q: str = Query(..., min_length=1),
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    client: Client = Depends(get_client),
):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="検索キーワードが空です")

    res = (
        client.table("report_texts")
        .select(
            "weekly_report_id, sheet_index, sheet_name, content,"
            "weekly_reports(week_start, week_end, m_store(store_name))"
        )
        .ilike("content", f"%{q}%")
        .limit(MAX_RESULTS)
        .execute()
    )

    results: list[SearchResult] = []
    for row in res.data:
        wr    = row.get("weekly_reports") or {}
        store = (wr.get("m_store") or {}).get("store_name", "不明")
        week_start = wr.get("week_start", "")
        week_end   = wr.get("week_end",   "")

        # month フィルタリング（YYYY-MM 形式）
        if month:
            if (week_end[:7] if week_end else "") != month:
                continue

        content = row.get("content") or ""
        snippet = _make_snippet(content, q)
        snippet = snippet.replace("★", "")  # ★マーカーは表示しない

        results.append(SearchResult(
            report_id  =row["weekly_report_id"],
            store_name =store,
            week_start =week_start,
            week_end   =week_end,
            sheet_index=row["sheet_index"],
            sheet_name =row["sheet_name"],
            snippet    =snippet,
        ))

    return results
