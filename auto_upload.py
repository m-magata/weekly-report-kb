"""
月別フォルダ自動取り込みスクリプト

共有フォルダ上の年フォルダ（例: 2026年）配下にある「<n>月」フォルダを
1月〜現在月まで走査し、Excel 週報をパースして DB に保存する。
HTTP（/upload API）を経由せず backend.parser / backend.crud を直接呼び出す。

実行方法:
  python auto_upload.py           # 未登録のみ処理
  python auto_upload.py --force   # 登録済みも含めて全件再処理（上書き）

処理対象・除外:
  - 対象拡張子      : .xlsx / .xls
  - `~$` 一時ファイル : glob 段階で除外
  - `000` 始まり     : パーサーが SkipFileError（フォーマットファイル）
  - 売上進捗表なし   : パーサーが SkipFileError

スキップ判定（--force 未指定時）:
  weekly_reports の unique キー (store_id, report_year, report_month, submitter_role)
  と同じ組み合わせが既に DB に存在すればスキップする。
  store_code / 年月 / 店長・副店長 はいずれもファイル名から決まるため、
  Excel を開かずに判定できる。
"""
import argparse
import io
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# 起動ディレクトリに依存せず backend パッケージを import できるようにする
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from backend.crud import save_parsed_report
from backend.database import get_client
from backend.parser.excel_parser import (  # noqa: E402
    SkipFileError,
    parse_excel,
    # スキップ判定をパーサー本体と完全に一致させるため、判定関数を共用する
    _extract_report_ym_from_filename,
    _is_fuku_tencho,
)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

ROOT = Path(r"Q:\共有\110エリア／店長\01.店長週報\2026年")

EXCEL_SUFFIXES = {".xlsx", ".xls"}
MONTH_DIR_RE = re.compile(r"^(\d{1,2})月$")

MAX_RETRIES = 3      # 保存失敗時のリトライ回数（初回試行を除く）
RETRY_WAIT_SEC = 5   # リトライ間隔（秒）
PAGE_SIZE = 1000     # Supabase 取得のページサイズ

# 一時的な通信エラーとみなすキーワード（WinError 10035 = 非ブロッキングソケット待ち）
TRANSIENT_MARKERS = (
    "10035",
    "winerror",
    "connection",
    "timed out",
    "timeout",
    "server disconnected",
    "max retries",
    "temporarily unavailable",
)


# ---------------------------------------------------------------------------
# 対象フォルダ・ファイルの列挙
# ---------------------------------------------------------------------------

def _folder_year(root: Path) -> int | None:
    """'2026年' のようなフォルダ名から年を取り出す。"""
    m = re.search(r"(\d{4})年", root.name)
    return int(m.group(1)) if m else None


def _target_months(
    root: Path, today: date, only: list[int] | None = None
) -> list[tuple[int, Path]]:
    """root 直下の「<n>月」フォルダを (月, パス) の昇順で返す。

    only 未指定時は現在月まで（年フォルダが過年度なら12月まで、翌年度以降は対象なし）。
    only 指定時は明示指定を優先し、現在月の上限は適用しない。
    """
    if only:
        limit = 12
    else:
        year = _folder_year(root)
        if year is None or year == today.year:
            limit = today.month
        elif year < today.year:
            limit = 12
        else:
            limit = 0

    months: list[tuple[int, Path]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        m = MONTH_DIR_RE.match(d.name)
        if not m:
            continue
        n = int(m.group(1))
        if only and n not in only:
            continue
        if 1 <= n <= limit:
            months.append((n, d))
    return sorted(months)


def _excel_files(folder: Path) -> list[Path]:
    """月フォルダ内の .xlsx / .xls を返す（`~$` 一時ファイルは除外）。"""
    return sorted(
        f for f in folder.glob("*.xls*")
        if f.suffix.lower() in EXCEL_SUFFIXES and not f.name.startswith("~$")
    )


# ---------------------------------------------------------------------------
# 登録済み判定
# ---------------------------------------------------------------------------

def _load_registered_keys(client, year: int) -> set[tuple[str, int, int, str]]:
    """DB の既存レコードを (store_code, report_year, report_month, submitter_role) の集合で返す。"""
    stores = {
        s["store_id"]: s["store_code"]
        for s in client.table("m_store").select("store_id,store_code").execute().data
    }

    rows: list[dict] = []
    start = 0
    while True:
        res = (
            client.table("weekly_reports")
            .select("store_id,report_year,report_month,submitter_role")
            .eq("report_year", year)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        rows.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    keys = set()
    for r in rows:
        code = stores.get(r["store_id"])
        if code and r["report_month"] is not None:
            keys.add((code, r["report_year"], r["report_month"], r["submitter_role"]))
    return keys


def _file_key(filename: str) -> tuple[str, int, int, str] | None:
    """ファイル名から DB の unique キー相当の組を作る。判定不能なら None。"""
    m = re.match(r"^(\d+)", filename)
    if not m:
        return None
    ym = _extract_report_ym_from_filename(filename)
    if not ym:
        return None
    role = "副店長" if _is_fuku_tencho(filename) else "店長"
    return (m.group(1).zfill(4), ym[0], ym[1], role)


# ---------------------------------------------------------------------------
# 保存（リトライ付き）
# ---------------------------------------------------------------------------

def _is_transient(exc: Exception) -> bool:
    """一時的な通信エラー（WinError 10035 等）かどうかを判定する。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in TRANSIENT_MARKERS)


def _save_with_retry(client, parsed, filename: str) -> None:
    """save_parsed_report を実行する。通信エラーなら 5 秒待機して最大 3 回リトライ。"""
    for attempt in range(MAX_RETRIES + 1):
        try:
            save_parsed_report(client, parsed)
            return
        except Exception as e:
            # 通信起因でないエラー（店舗未登録など）は即座に失敗させる
            if attempt >= MAX_RETRIES or not _is_transient(e):
                raise
            print(
                f"       RETRY({attempt + 1}/{MAX_RETRIES}) {filename}: {e} "
                f"— {RETRY_WAIT_SEC}秒後に再試行"
            )
            time.sleep(RETRY_WAIT_SEC)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="月別フォルダの週報 Excel を一括で DB に取り込む"
    )
    ap.add_argument(
        "--force", action="store_true",
        help="登録済みレコードも含めて全件再処理する（上書き）",
    )
    ap.add_argument(
        "--month", type=int, nargs="+", metavar="N",
        help="対象月を指定する（例: --month 6 / --month 6 7）。"
             "指定時は現在月までの制限を適用しない。省略時は当月まで全て",
    )
    args = ap.parse_args()

    if args.month:
        invalid = [m for m in args.month if not 1 <= m <= 12]
        if invalid:
            print(f"ERROR: --month には 1〜12 を指定してください: {invalid}")
            return 1

    missing = [
        k for k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY")
        if not os.environ.get(k)
    ]
    if missing:
        print(f"ERROR: .env に {' / '.join(missing)} が設定されていません。")
        return 1

    if not ROOT.is_dir():
        print(f"ERROR: 対象フォルダが見つかりません: {ROOT}")
        print("       ネットワークドライブ（Q:）が接続されているか確認してください。")
        return 1

    today = date.today()
    months = _target_months(ROOT, today, only=args.month)
    if not months:
        if args.month:
            print(f"指定された月のフォルダが見つかりません: "
                  f"{'/'.join(str(m) + '月' for m in sorted(args.month))} ({ROOT})")
        else:
            print(f"対象となる月フォルダがありません: {ROOT}")
        return 0

    year = _folder_year(ROOT) or today.year
    client = get_client()

    registered: set[tuple[str, int, int, str]] = set()
    if not args.force:
        registered = _load_registered_keys(client, year)

    mode_label = "全件再処理（上書き）" if args.force else "未登録のみ"
    print(f"対象フォルダ: {ROOT}")
    if args.month:
        print(f"対象月      : {'/'.join(str(m) + '月' for m, _ in months)}（--month 指定）")
    else:
        print(f"対象月      : {months[0][0]}月 〜 {months[-1][0]}月（基準日 {today}）")
    print(f"モード      : {mode_label}")
    if not args.force:
        print(f"登録済み    : {len(registered)} 件（{year}年）")
    print()

    stats: dict[int, dict[str, int]] = {}
    errors: list[tuple[str, str]] = []

    for month, folder in months:
        files = _excel_files(folder)
        counts = {"total": len(files), "ok": 0, "dup": 0, "skip": 0, "err": 0}
        stats[month] = counts

        print(f"=== {month}月 ({len(files)} 件) ===")
        if not files:
            print("  （対象ファイルなし）\n")
            continue

        for f in files:
            key = _file_key(f.name)
            if not args.force and key is not None and key in registered:
                print(f"  DUP  {f.name}  (登録済みのためスキップ)")
                counts["dup"] += 1
                continue

            try:
                parsed = parse_excel(f)
                _save_with_retry(client, parsed, f.name)
            except SkipFileError as e:
                print(f"  SKIP {f.name}: {e}")
                counts["skip"] += 1
                continue
            except Exception as e:
                print(f"  ERR  {f.name}: {e}")
                counts["err"] += 1
                errors.append((f"{month}月/{f.name}", f"{type(e).__name__}: {e}"))
                continue

            role = f"[{parsed.submitter_role}]" if parsed.submitter_role != "店長" else ""
            print(
                f"  OK   {f.name}  {parsed.store_name}{role}  "
                f"{parsed.week_start}〜{parsed.week_end}"
            )
            counts["ok"] += 1
            # 同一実行内で同じキーが再登場した場合に二重処理しないよう記録
            if key is not None:
                registered.add(key)
        print()

    # -------------------------------------------------------------- サマリー
    print("=" * 52)
    print("サマリー")
    print("=" * 52)
    print(f"{'月':<6}{'対象':>6}{'OK':>6}{'登録済':>8}{'対象外':>8}{'エラー':>8}")
    print("-" * 52)
    totals = {"total": 0, "ok": 0, "dup": 0, "skip": 0, "err": 0}
    for month, c in sorted(stats.items()):
        print(
            f"{str(month) + '月':<6}{c['total']:>6}{c['ok']:>6}"
            f"{c['dup']:>8}{c['skip']:>8}{c['err']:>8}"
        )
        for k in totals:
            totals[k] += c[k]
    print("-" * 52)
    print(
        f"{'合計':<6}{totals['total']:>6}{totals['ok']:>6}"
        f"{totals['dup']:>8}{totals['skip']:>8}{totals['err']:>8}"
    )

    if errors:
        print(f"\nエラー詳細 ({len(errors)} 件):")
        for name, msg in errors:
            print(f"  - {name}: {msg}")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
