"""
Supabase REST API を使った CRUD 操作。

最適化ポイント:
  1. store_id をプロセス内キャッシュ（m_store は静的データ）
  2. daily_sales / report_texts を ThreadPoolExecutor で並列実行
  3. 新規 weekly_report の場合は DELETE をスキップ（子レコードが存在しないため）
  4. 全インサートはリストを一括 POST（バルクインサート）
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from supabase import Client

from backend.parser.excel_parser import ParsedReport

# store_code → store_id のプロセス内キャッシュ
_store_cache: dict[str, int] = {}


def save_parsed_report(client: Client, parsed: ParsedReport) -> dict:
    """
    ParsedReport をDBに保存して weekly_report レコードを返す。

    リクエスト数:
      - 初回アップロード: 3リクエスト (lookup_store[キャッシュ後0] + upsert_wr + 並列2)
      - 再アップロード  : 4リクエスト (同上 + 並列DELETE×2 → INSERT×2)
    """
    store_id = _get_store_id(client, parsed)
    wr, is_new = _upsert_weekly_report(client, store_id, parsed)
    wr_id: int = wr["id"]

    # daily_sales と report_texts を並列実行
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_sales = pool.submit(_write_daily_sales, client, wr_id, parsed, is_new)
        f_texts = pool.submit(_write_report_texts, client, wr_id, parsed, is_new)
        # 例外を呼び出し元に伝播させる
        f_sales.result()
        f_texts.result()

    return wr


# ---------------------------------------------------------------------------
# 店舗ルックアップ（キャッシュ付き）
# ---------------------------------------------------------------------------

def _get_store_id(client: Client, parsed: ParsedReport) -> int:
    """store_id をキャッシュから返す。未キャッシュの場合は m_store を検索して保存。"""
    code = _extract_store_code(parsed.source_filename) or parsed.store_name

    if code in _store_cache:
        return _store_cache[code]

    store_id = _lookup_store_id(client, parsed)
    _store_cache[code] = store_id
    return store_id


def _lookup_store_id(client: Client, parsed: ParsedReport) -> int:
    """
    m_store から store_id を取得する。
    優先順:
      1. ファイル名先頭の数字3〜4桁 → store_code（0埋め4桁）で検索
      2. パーサーが抽出した store_name で前方一致検索
    """
    code = _extract_store_code(parsed.source_filename)
    if code:
        res = (
            client.table("m_store")
            .select("store_id")
            .eq("store_code", code)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["store_id"]

    name = parsed.store_name.rstrip("店")
    res = (
        client.table("m_store")
        .select("store_id")
        .ilike("store_name", f"{name}%")
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["store_id"]

    raise ValueError(
        f"m_store に該当する店舗が見つかりません: "
        f"store_code={code!r}, store_name={parsed.store_name!r}"
    )


def _extract_store_code(filename: str) -> str | None:
    m = re.match(r"^(\d+)", filename)
    return m.group(1).zfill(4) if m else None


# ---------------------------------------------------------------------------
# weekly_reports upsert
# ---------------------------------------------------------------------------

def _upsert_weekly_report(
    client: Client, store_id: int, parsed: ParsedReport
) -> tuple[dict, bool]:
    """
    weekly_reports を upsert し、(レコード, is_new) を返す。
    is_new=True なら子レコードの DELETE を省略できる。
    """
    # upsert 前に既存レコードの有無を確認（1リクエスト節約のため count 使用）
    existing = (
        client.table("weekly_reports")
        .select("id", count="exact")
        .eq("store_id", store_id)
        .eq("week_start", str(parsed.week_start))
        .eq("week_end", str(parsed.week_end))
        .eq("submitter_role", parsed.submitter_role)
        .execute()
    )
    is_new = existing.count == 0

    res = (
        client.table("weekly_reports")
        .upsert(
            {
                "store_id": store_id,
                "week_start": str(parsed.week_start),
                "week_end": str(parsed.week_end),
                "source_filename": parsed.source_filename,
                "submitter_role": parsed.submitter_role,
            },
            on_conflict="store_id,week_start,week_end,submitter_role",
        )
        .execute()
    )
    return res.data[0], is_new


# ---------------------------------------------------------------------------
# daily_sales / report_texts（並列実行される）
# ---------------------------------------------------------------------------

def _write_daily_sales(
    client: Client, wr_id: int, parsed: ParsedReport, is_new: bool
) -> None:
    """新規なら DELETE スキップ。バルクインサートで1リクエスト。"""
    if not is_new:
        client.table("daily_sales").delete().eq("weekly_report_id", wr_id).execute()

    if not parsed.daily_sales:
        return

    rows = [
        {
            "weekly_report_id": wr_id,
            "date": str(rec.date),
            "sales_amount": rec.sales_amount,
            "customer_count": rec.customer_count,
            "weather": rec.weather,
            "sales_budget_ratio": rec.sales_budget_ratio,
            "customer_count_prev_year": rec.customer_count_prev_year,
        }
        for rec in parsed.daily_sales
    ]
    client.table("daily_sales").insert(rows).execute()


def _replace_report_texts(client: Client, wr_id: int, parsed: ParsedReport) -> None:
    """後方互換のために残す（旧 crud 呼び出し用）。"""
    _write_report_texts(client, wr_id, parsed, is_new=False)


def _write_report_texts(
    client: Client, wr_id: int, parsed: ParsedReport, is_new: bool
) -> None:
    """新規なら DELETE スキップ。バルクインサートで1リクエスト。"""
    if not is_new:
        client.table("report_texts").delete().eq("weekly_report_id", wr_id).execute()

    if not parsed.report_texts:
        return

    rows = [
        {
            "weekly_report_id": wr_id,
            "sheet_index": rec.sheet_index,
            "sheet_name": rec.sheet_name,
            "content": rec.content,

        }
        for rec in parsed.report_texts
    ]
    client.table("report_texts").insert(rows).execute()
