# 販売部週報ナレッジDBシステム — プロジェクト仕様書

## 概要
販売部の週次Excelレポートをパースして構造化DBに格納し、過去データを検索・分析できるナレッジベースシステム。

---

## 技術スタック（実装済み）

| 領域 | 採用技術 |
|------|---------|
| バックエンド | Python 3.11+, FastAPI 0.115 |
| DB | **Supabase (PostgreSQL)** — Supabase Python SDK 2.28 経由の REST/HTTPS アクセス |
| Excel パース | openpyxl 3.1（.xlsx）+ xlrd 2.0.1（.xls） |
| フロントエンド | Vanilla JS + Chart.js 4.4（静的HTML、FastAPI で配信） |
| 環境変数 | python-dotenv (.env) |

> **注意**: SQLite / SQLAlchemy は初期設計のみ。現在は Supabase SDK に完全移行済み。  
> ポート 5432 はネットワーク制限で接続不可。Transaction Pooler も「Tenant not found」で失敗。  
> そのため `SUPABASE_URL` / `SUPABASE_KEY` による REST API 接続のみ使用する。

---

## ディレクトリ構成（現在）

```
weekly-report-kb/
├── .claude/
│   └── CLAUDE.md              # このファイル
├── .env                       # SUPABASE_URL, SUPABASE_KEY（gitignore 対象）
├── backend/
│   ├── main.py                # FastAPI アプリ・ルーター登録・静的ファイル配信
│   ├── database.py            # Supabase SDK シングルトンクライアント
│   ├── crud.py                # DB 書き込みロジック（キャッシュ・並列処理）
│   ├── models/                # SQLAlchemy モデル（現在未使用、参照のみ）
│   │   ├── store.py
│   │   ├── weekly_report.py
│   │   ├── daily_sales.py
│   │   └── report_text.py
│   ├── parser/
│   │   └── excel_parser.py    # openpyxl / xlrd による Excel パース
│   └── routers/
│       ├── upload.py          # POST /upload, GET /reports
│       ├── reports.py         # GET /reports/{id}/daily-sales, /texts
│       └── search.py          # GET /search?q=&month=
├── frontend/
│   ├── viewer/
│   │   └── index.html         # 閲覧 UI（週タブ・KPI・テキスト3セクション）
│   └── upload/
│       └── index.html         # アップロード UI（ファイル/フォルダ選択・結果一覧）
├── data/                      # アップロード済み Excel ファイル置き場
├── schema.sql                 # Supabase 上のテーブル定義（参照用）
├── reprocess_all.py           # data/ 全ファイル再パース・DB 上書きスクリプト
└── requirements.txt
```

---

## DBスキーマ（Supabase）

### 参照テーブル（変更不可）
```sql
-- 店舗マスタ（23店舗）
m_store (
  store_id   bigint primary key,
  corp_id    bigint,
  store_code text,      -- '0041' のようなゼロ埋め4桁
  store_name text,
  area_id    integer,   -- 追加済み: 1=第1エリア / 2=第2エリア / 3=第3エリア
  valid_from date,
  valid_to   date
)

-- 部門マスタ（商品部門 27部門）
m_dpt (dpt_id, group_id, dpt_code, dpt_name, ...)

-- 月次予算（store_id × fiscal_year/month × dpt_id）
m_budget (budget_id, store_id, fiscal_year, fiscal_month, budget_sales, budget_gross, dpt_id, ...)
```

### 作成済みテーブル
```sql
weekly_reports (
  id              bigserial primary key,
  store_id        bigint references m_store(store_id) on delete cascade,
  week_start      date not null,
  week_end        date not null,
  source_filename text,
  submitter_role  text not null default '店長',  -- '店長' or '副店長'
  report_year     integer,   -- ファイル名から抽出した年（例: 2026）
  report_month    integer,   -- ファイル名から抽出した月（例: 3）
  unique (store_id, week_start, week_end, submitter_role)
)

daily_sales (
  id                       bigserial primary key,
  weekly_report_id         bigint references weekly_reports(id) on delete cascade,
  date                     date not null,
  sales_amount             numeric,      -- 千円単位
  customer_count           integer,
  weather                  text,
  sales_budget_ratio       numeric,      -- 予算比（実売÷日割予算）
  customer_count_prev_year integer       -- 昨年同週客数
)

report_texts (
  id                bigserial primary key,
  weekly_report_id  bigint references weekly_reports(id) on delete cascade,
  sheet_index       integer,      -- 1〜6（週報①〜⑥）
  sheet_name        text,
  content           text,         -- ＜営業報告＞以降の本文（全セクション含む）
  has_highlight     boolean not null default false  -- 未使用・残存カラム
)
```

> **月分類の方針**: `week_end` は月またぎ誤検出が起きる場合があるため、月の分類には  
> `report_year` / `report_month`（ファイル名から抽出）を使用する。  
> 例: `065 販売部週報 26-03.xlsx` → `report_year=2026, report_month=3`

---

## Excelファイル構造（実調査済み）

### シート「売上進捗表」
- **行1**: `(None, None, ..., 2026, '年', 3, '月度...')`  → 年・月を抽出
- **行2**: `(None, ..., 'ＮＴ南', None, '店', ...)` → 店舗名を抽出（`店` の1〜2列前、末尾「店」重複を防ぐ）
- **週ブロック（10行単位）**:
  - `row+0`: `(週名, '日付', d1..d7, ...)`
  - `row+1`: `(None, '天候', w1..w7, ...)`
  - `row+2`: `(None, '売上', s1..s7, ...)`       → `sales_amount`
  - `row+5`: `(None, '予算比', r1..r7, ...)`     → `sales_budget_ratio`
  - `row+6`: `(None, '客数', c1..c7, ...)`       → `customer_count`
  - `row+7`: `(None, '昨客', p1..p7, ...)`       → `customer_count_prev_year`
- 月またぎ検出: 日の値が前日より小さくなったら翌月に進める

### シート「週報①〜⑥」
- 行17に `＜営業報告＞`（全角山括弧付き）マーカー
- 以降の行が本文。セクション区切り（4セクション）:
  1. `＜営業報告＞` — `content` の先頭から `＜防災` まで
  2. `＜防災、備災の日確認実施報告...＞` — チェックリスト形式（item/○ペア）
  3. `＜人員管理報告　人員、残業、配置、作業等＞`
  4. `＜来期へのコメント・成功、反省事例＞`
- 「社長」「部長」「エリア長」「次長」「課長」等の署名セルは除去（`SIGNATURE_WORDS`）

### ファイル命名規則
- 例: `041 販売部週報 26-03.xlsx`
- 先頭の数字（3〜4桁）→ ゼロ埋め4桁で `m_store.store_code` を検索
- `000 販売部週報...` → `store_code='0000'` → フォーマットファイルとして `SkipFileError`
- `~$` で始まるファイル → Excel 一時ファイルとして `SkipFileError`
- `052副店長...` / `069販売部週報26-03副.xlsx` → 副店長ファイル（`_is_fuku_tencho` 判定）
- `売上進捗表` シートが存在しないファイル → `SkipFileError`
- 年月抽出: ファイル名末尾の `YY-MM` パターン（全角ダッシュ・長音記号も対応）→ `report_year` / `report_month`

### .xls ファイル対応
- `.xls` は `xlrd.open_workbook()` で読み込み、`_XlrdWorkbookWrapper` で openpyxl ライクなインターフェースに変換
- `.xls` では黒背景セル検出（`_cell_has_dark_fill`）は常に False（書式情報なし）
- `.xlsx` は従来通り openpyxl を使用

---

## 実装済み機能

### バックエンド API
| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/upload` | Excel アップロード → パース → DB 保存 |
| POST | `/upload/batch` | 複数ファイル一括処理（NDJSON ストリーム返却） |
| GET  | `/reports?area_id=` | 週報一覧（store_code 昇順、report_year/month 付き） |
| GET  | `/reports/{id}/daily-sales` | 日別売上・客数・天候・予算比・昨対客数 |
| GET  | `/reports/{id}/texts` | 週報テキスト一覧（sheet_index 昇順） |
| GET  | `/search?q=&month=` | 週報テキスト全文検索（最大50件） |
| GET  | `/health` | ヘルスチェック |
| GET  | `/viewer/` | 閲覧 UI 配信 |
| GET  | `/upload/` | アップロード UI 配信 |

### パーサー（excel_parser.py）
- `SkipFileError` 例外: store_code='0000' / 売上進捗表シートなし / `~$` 一時ファイル
- `_is_fuku_tencho`: 副店長ファイル判定（「副店長」含む or ファイル名末尾「副」）
- 副店長ファイル: `store_name` 末尾に「副」付加、`submitter_role='副店長'`
- 店舗名重複防止: 候補に「店」が付いていれば追加しない
- `SIGNATURE_WORDS`: 署名セル（社長・部長・エリア長等）を除去
- 週ブロック追加抽出: `sales_budget_ratio`（row+5）・`customer_count_prev_year`（row+7）
- `_extract_report_ym_from_filename()`: ファイル名から `report_year`/`report_month` を抽出
- `_XlrdWorkbookWrapper` / `_XlrdSheetWrapper` / `_XlrdCellWrapper`: xlrd→openpyxl 変換ラッパー

### パフォーマンス最適化（crud.py）
- `store_id` のプロセス内キャッシュ（`_store_cache` dict）
- `daily_sales` / `report_texts` の並列書き込み（`ThreadPoolExecutor(max_workers=2)`）
- 新規アップロード時は DELETE をスキップ（`is_new` フラグ）
- バルクインサート（リスト一括 POST）

### フロントエンド（frontend/viewer/index.html）
- **ヘッダー**:
  - 全文検索ボックス（Enter / 検索ボタンで実行）
  - 結果モーダル表示（店舗名・期間・シート名・スニペット、キーワード黄色ハイライト）
  - 結果クリックで対象週報に遷移、ESC / 背景クリックで閉じる
  - 月フィルター選択中は検索も同月で絞り込み
- **サイドバー**:
  - 年月セレクター（`report_year`/`report_month` ベース、最新月デフォルト）
  - エリアセレクター（全エリア / 第1〜3エリア）
  - 店舗名テキスト検索
  - `×` フィルタークリアボタン
  - 店舗グループ表示（`[店長]` `[副]` バッジ、同一 store_name をグループ化、store_code 昇順）
- **メインエリア**:
  - サマリーバー（月間売上・客数・日商平均）
  - 日別売上グラフ（Chart.js bar+line、4系列: 売上・客数・売上予算比・客数昨対比）
  - **週タブ** `第N週(mm/dd)` 形式
  - 各タブ: 週計 KPI + テキスト4セクション
    - 営業報告（日付始まり行で段落整形、黒背景セル行は赤枠・太字強調）
    - 防災チェックリスト（`<details>` 折りたたみ、2列グリッド）
    - 人員管理報告
    - 来期コメント（署名除去済み）

### フロントエンド（frontend/upload/index.html）
- **ファイル選択**: 「ファイルを選択」ボタン（複数選択可）と「フォルダを選択」ボタン（`webkitdirectory`、サブフォルダ再帰）
- **.xlsx / .xls** のみ自動フィルタリング、その他拡張子は除外
- ドラッグ&ドロップ対応
- `/upload/batch` に送信し、NDJSON ストリームをリアルタイム表示
- 結果テーブル: フォルダパス（グレー）＋ファイル名 / 店舗名 / 期間 / ステータスバッジ（OK・SKIP・登録済・ERR）
- 完了後サマリー + 「閲覧画面へ →」リンク

### バッチツール
- `reprocess_all.py`: `data/` 内全ファイルを再パース・DB 上書き
  - `~$` 一時ファイルを glob 段階で除外
  - `.xlsx` / `.xls` 両対応
  - stdout を UTF-8 強制（Windows cp932 文字化け対策）
  - `--force` オプションで登録済みも含めて全件再処理

---

## 起動方法

```bash
cd C:\Users\landrome\weekly-report-kb
uvicorn backend.main:app --reload
# → http://127.0.0.1:8000/viewer/
# → http://127.0.0.1:8000/upload/
```

`.env` に以下が必要:
```
SUPABASE_URL=https://evjauefckxjicyunfsoq.supabase.co
SUPABASE_KEY=<anon key>
```

---

## 次のタスク（Phase 2 残タスク）

### 完了済み ✅
- 来期コメントのパース修正（`parseSections` をマーカー名ベースに変更）
- グラフに予算比・客数昨対比を追加（破線折れ線、右軸 70〜130%）
- 防災チェックリストのコンパクト表示（`<details>` 折りたたみ + 2列グリッド）
- 署名欄テキスト除去（社長・部長・エリア長等）
- 営業報告の日付行前に空行挿入
- 店舗名「店」重複修正
- 副店長ファイル対応（submitter_role / store_name 末尾「副」）
- store_code='0000' スキップ
- サイドバー: 年月・エリア・テキスト検索フィルター
- サイドバー: 店長/副店長グループ表示
- 黒背景コメントの強調表示（`★` プレフィックス行を赤枠・太字、`★` 文字は非表示）
- 週報テキスト全文検索（`GET /search?q=&month=` + モーダル結果表示、キーワードハイライト）
- .xls ファイル対応（xlrd 2.0.1 + openpyxl ラッパー）
- `~$` Excel 一時ファイルの自動スキップ
- アップロード UI 改善（フォルダ選択・サブフォルダパス表示・NDJSON ストリーム結果表示）
- サイドバー店舗一覧を store_code 昇順でソート（GET /reports でソート）
- `report_year` / `report_month` カラム追加（ファイル名から年月を抽出・月分類に使用）
- 月セレクターと月フィルターを `report_year`/`report_month` ベースに変更（week_end 依存を廃止）
- reprocess_all.py の UTF-8 出力対応・.xls 対応・一時ファイル除外

### 未着手
1. **未提出店舗グレーアウト** — m_store 全23店舗を取得し、週報未提出店舗をサイドバーにグレー表示
2. **月次サマリーダッシュボード** — 全店舗月次集計（売上・客数・日商平均・前月比）
3. **エクスポート** — CSV/Excel 出力エンドポイント

---

## 命名規則
- Python ファイル: snake_case
- クラス名: PascalCase
- DB テーブル名: snake_case（複数形）
- API エンドポイント: kebab-case

## 注意事項
- 売上データは **千円単位**で格納（表示時に変換不要）
- 店舗名は全角文字を許容
- 週報テキストは原文をそのまま保持（HTML エスケープはフロントで実施）
- 静的ファイルパスは `Path(__file__).resolve()` で絶対パス解決（uvicorn 起動ディレクトリ非依存）
- `report_texts.content` の `★` プレフィックスは黒背景セルのマーカー（DB に格納、フロント表示時は `★` 文字を除去して赤枠スタイルを適用）
- `report_texts` には未使用の `has_highlight boolean` カラムが Supabase 上に残存。コードでは未参照。
- `week_end` は月またぎ誤検出で実際の月と異なる場合がある（例: 26-03.xlsx なのに week_end=2026-04-29）。月の分類・フィルタリングには必ず `report_year`/`report_month` を使用すること。
- Supabase への大量リクエスト時（reprocess --force など）はレート制限（WinError 10035）が発生することがある。失敗したレコードは再実行するか直接 UPDATE で補完する。

---

## 2026-04-05 引き継ぎ

### 完了済み機能
- バッジ重複排除・店舗名同行配置
- 未提出店舗グレーアウト（/api/stores/all エンドポイント）
- 黒背景セル強調表示（行単位連結・フロント★処理）
- xls形式黒背景検出対応（formatting_info=True・_XlrdCellWrapper改修）
- 強制上書きUI追加（/upload/batch?force=true）
- /api/highlights エンドポイント（has_highlight=trueの横断取得）
- AIダイジェスト機能（/api/highlights/summary・キャッシュ・再生成）
- digest_cacheテーブル作成済み（Supabase）
- 年月フィルター現在月+3ヶ月先まで対応
- AIダイジェストのスタイル統一（プロンプトをXMLタグ構造化・systemパラメータ追加・/summaryと/digestを同一プロンプトに統一）
- AIダイジェスト期間を当月のみに変更（昨年同月1ヶ月分）
- AIダイジェスト表示の見出し太字化・出典グレー表示
- コピーボタンをPDF出力ボタンに変更（BIZ UDPGothic・A4縦1枚強制縮小・上下10mm左右5mm余白）
- Railwayへの本番デプロイ完了
  URL: https://weekly-report-kb-production.up.railway.app/viewer/
  - nixpacks.toml / railway.toml / Procfile 追加
  - pydantic>=2.11.7 に変更（supabase依存関係解消）
  - 環境変数（SUPABASE_URL / SUPABASE_KEY / ANTHROPIC_API_KEY）設定済み
  - GitHubへのpush → 自動デプロイ設定済み

## 2026-04-06 引き継ぎ

### 完了済み機能
- デフォルト表示月を最新月→当月（yyyy-MM）に変更
- アップロードボタンをlocalhost/127.0.0.1のみ表示に変更（外部アクセスでは非表示）
- m_storeにこてはし三角町店（store_id=24, store_code=0074, area_id=3）追加
- reprocess_all.pyにリトライロジック追加（WinError 10035対策、5秒待機×最大3回）
- requirements.txtからpandasを削除（未使用、Railwayビルド高速化）
- 売上進捗表の月_日形式日付文字列パース対応（'3_30'/'5_1'形式、都賀店26-04.xlsケース）

### 現在の問題
- なし

### 次のタスク
1. エクスポート機能（CSV/Excel出力）
2. 他部署週報への対応

### 将来構想
- カレンダーデータのアップロード・活用
- 日割予算データのアップロード・活用
- 実績ファイルのアップロード・活用
- Web入力型週報への移行（Excelファイル提出からの脱却）
  - テキスト系（営業報告・防災チェック・人員管理・来期コメント）はWeb入力
  - 数値系（日別売上・客数・天候・予算比）は売上進捗表Excelの一括アップロードを継続
  - 店長がスマホ・PCから入力できるレスポンシブ対応が必要

### 技術メモ
- .envにANTHROPIC_API_KEY設定済み
- 年月フィルターは現在月+3ヶ月先まで対応済み
- ダイジェスト参照期間：選択月の昨年同月1ヶ月分（month_from=month_to=選択月）
- プロンプトは _build_prompt() 関数で生成（XMLタグ構造）、system パラメーターでMarkdown記号禁止を明示
