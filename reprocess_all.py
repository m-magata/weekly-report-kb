"""
既存データ再処理スクリプト
data/ フォルダ内の全 Excel を再パースして DB を上書きする。

実行方法:
  python reprocess_all.py           # 新規ファイルのみ処理
  python reprocess_all.py --force   # 全件再処理（既存データも上書き）
"""
import argparse
import io
import os
import sys
import time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

from backend.parser.excel_parser import parse_excel, SkipFileError
from backend.crud import save_parsed_report

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
if not url or not key:
    print("ERROR: .env に SUPABASE_URL / SUPABASE_KEY が設定されていません。")
    sys.exit(1)

parser = argparse.ArgumentParser()
parser.add_argument("--force", action="store_true", help="既登録ファイルも含めて全件再処理する")
args = parser.parse_args()

client = create_client(url, key)
data_dir = Path("data")

# 登録済み source_filename を取得（--force でなければスキップ判定に使用）
registered = set()
if not args.force:
    res = client.table("weekly_reports").select("source_filename").execute()
    registered = {r["source_filename"] for r in res.data if r["source_filename"]}

files = sorted(
    f for f in data_dir.glob("*.xls*")
    if f.suffix.lower() in {".xlsx", ".xls"} and not f.name.startswith("~$")
)
mode_label = "全件再処理" if args.force else "新規のみ"
print(f"{len(files)} 件のファイルを確認します。（モード: {mode_label}）\n")

ok = skip = skip_dup = err = 0
for f in files:
    if not args.force and f.name in registered:
        print(f"  DUP  {f.name}  (登録済みのためスキップ)")
        skip_dup += 1
        continue
    try:
        parsed = parse_excel(f)
        last_exc = None
        for attempt in range(1, 4):
            try:
                save_parsed_report(client, parsed)
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                if attempt < 3:
                    print(f"  RETRY({attempt}) {f.name}: {e} — 5秒後に再試行")
                    time.sleep(5)
        if last_exc is not None:
            raise last_exc
        role = f"[{parsed.submitter_role}]" if parsed.submitter_role != "店長" else ""
        print(f"  OK   {f.name}  {parsed.store_name}{role}  {parsed.week_start}〜{parsed.week_end}")
        ok += 1
    except SkipFileError as e:
        print(f"  SKIP {f.name}: {e}")
        skip += 1
    except Exception as e:
        print(f"  ERR  {f.name}: {e}")
        err += 1

print(f"\n完了: {ok} 件処理 / {skip_dup} 件スキップ(登録済み) / {skip} 件スキップ(対象外) / {err} 件失敗")
