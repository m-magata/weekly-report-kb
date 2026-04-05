"""
GET  /api/highlights         : has_highlight=true の週報テキスト一覧
POST /api/highlights/summary : Anthropic API で昨年同月の注意事項をサマリー化
"""
import os
import re

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from backend.database import get_client

router = APIRouter(prefix="/api/highlights")

MODEL = "claude-sonnet-4-20250514"


class HighlightItem(BaseModel):
    store_name: str
    report_year: int | None
    report_month: int | None
    sheet_name: str | None
    content: str | None


class SummaryRequest(BaseModel):
    year: int
    month_from: int
    month_to: int
    force: bool = False  # True の場合キャッシュを無視して再生成


class SummaryResponse(BaseModel):
    summary: str


class DigestResponse(BaseModel):
    digest: str


# ★行のみ抽出（連続する★行を1ブロックに連結）
def _extract_hl_blocks(content: str | None) -> list[str]:
    if not content:
        return []
    blocks, buf = [], []
    for line in content.split("\n"):
        if line.startswith("★"):
            buf.append(line[1:].strip())
        else:
            if buf:
                blocks.append(" ".join(buf))
                buf = []
    if buf:
        blocks.append(" ".join(buf))
    return blocks


def _fetch_highlights(
    client: Client,
    year: int | None,
    month_from: int | None,
    month_to: int | None,
) -> list[HighlightItem]:
    query = (
        client.table("report_texts")
        .select(
            "sheet_name, content,"
            " weekly_reports!inner(report_year, report_month, m_store(store_name))"
        )
        .eq("has_highlight", True)
    )
    if year is not None:
        query = query.eq("weekly_reports.report_year", year)
    if month_from is not None:
        query = query.gte("weekly_reports.report_month", month_from)
    if month_to is not None:
        query = query.lte("weekly_reports.report_month", month_to)

    res = query.execute()
    results = []
    for row in res.data:
        wr = row.get("weekly_reports") or {}
        store = (wr.get("m_store") or {}).get("store_name", "")
        results.append(
            HighlightItem(
                store_name=store,
                report_year=wr.get("report_year"),
                report_month=wr.get("report_month"),
                sheet_name=row.get("sheet_name"),
                content=row.get("content"),
            )
        )
    return results


@router.get("", response_model=list[HighlightItem])
def get_highlights(
    year: int | None = None,
    month_from: int | None = None,
    month_to: int | None = None,
    client: Client = Depends(get_client),
):
    return _fetch_highlights(client, year, month_from, month_to)


def _build_prompt(source_text: str) -> str:
    return f"""\
以下の<重要コメント>タグ内に昨年同時期の全店舗の重要コメントを示します。

<重要コメント>
{source_text}
</重要コメント>

今年の同時期に注意すべきポイントを、以下の<フォーマット>の通りに出力してください。

<フォーマット>
今年の同時期に注意すべきポイント

1. （タイトル）
（原文そのままの引用）
出典：（店舗名） （年月） （シート名）

2. （タイトル）
（原文そのままの引用）
出典：（店舗名） （年月） （シート名）
</フォーマット>

<厳守ルール>
- 出力は<フォーマット>の内容のみ。タグ自体は出力しない
- #・##・###・**・*・__・【】・■・---・===などの記号は絶対に使わない
- 見出しは「数字.（半角スペース）タイトル」の形式のみ（例：1. 売上低下への注意）
- 内容は原文をそのまま引用し、要約・解釈・編集しない
- 出典は必ず「出典：店舗名 年月 シート名」の形式で記載
- データがない場合は「該当データがありません」とのみ出力し、他は何も書かない
</厳守ルール>"""


def _build_source_text(items: list[HighlightItem]) -> str:
    """HighlightItem リストから ★行を抽出してプロンプト用テキストを組み立てる。"""
    lines = []
    for item in items:
        blocks = _extract_hl_blocks(item.content)
        if not blocks:
            continue
        month_label = f"{item.report_year}年{item.report_month}月" if item.report_year else ""
        for block in blocks:
            lines.append(f"【{item.store_name} {month_label} {item.sheet_name or ''}】{block}")
    return "\n".join(lines)


def _call_anthropic(source_text: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY が設定されていません")
    ai_client = anthropic.Anthropic(api_key=api_key)
    message = ai_client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=(
            "あなたは小売業の週報データを分析するアシスタントです。"
            "指定されたフォーマットと厳守ルールを必ず守って出力してください。"
            "Markdownの記号（#・**・*・__・---など）は絶対に使いません。"
        ),
        messages=[{"role": "user", "content": _build_prompt(source_text)}],
    )
    return message.content[0].text if message.content else ""


def _cache_key(year: int, month_from: int, month_to: int) -> str:
    return f"{year}-{month_from}-{month_to}"


def _get_cache(client: Client, key: str) -> str | None:
    res = client.table("digest_cache").select("digest_text").eq("cache_key", key).limit(1).execute()
    return res.data[0]["digest_text"] if res.data else None


def _set_cache(client: Client, key: str, text: str) -> None:
    client.table("digest_cache").upsert(
        {"cache_key": key, "digest_text": text},
        on_conflict="cache_key",
    ).execute()


@router.post("/summary", response_model=SummaryResponse)
def summarize_highlights(req: SummaryRequest, client: Client = Depends(get_client)):
    key = _cache_key(req.year, req.month_from, req.month_to)

    if not req.force:
        cached = _get_cache(client, key)
        if cached:
            return SummaryResponse(summary=cached)

    items = _fetch_highlights(client, req.year, req.month_from, req.month_to)
    source_text = _build_source_text(items)
    if not source_text:
        return SummaryResponse(summary="該当データがありません")

    summary = _call_anthropic(source_text)
    _set_cache(client, key, summary)
    return SummaryResponse(summary=summary)


@router.post("/digest", response_model=DigestResponse)
def digest_highlights(req: SummaryRequest, client: Client = Depends(get_client)):
    items = _fetch_highlights(client, req.year, req.month_from, req.month_to)
    source_text = _build_source_text(items)
    if not source_text:
        return DigestResponse(digest="該当データがありません")
    return DigestResponse(digest=_call_anthropic(source_text))
