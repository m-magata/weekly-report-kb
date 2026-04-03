# 販売部週報ナレッジDBシステム — プロジェクト仕様書

## 概要
販売部の週次Excelレポートをパースして構造化DBに格納し、過去データを検索・分析できるナレッジベースシステム。

---

## 技術スタック（実装済み）

| 領域 | 採用技術 |
|------|---------|
| バックエンド | Python 3.11+, FastAPI 0.115 |
| DB | **Supabase (PostgreSQL)** — Supabase Python SDK 2.28 経由の REST/HTTPS アクセス |
| Excel パース | openpyxl 3.1（pandas は未使用） |
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
│   │   └── excel_parser.py    # openpyxl による Excel パース
│   └── routers/
│       ├── upload.py          # POST /upload, GET /reports
│       ├── reports.py         # GET /reports/{id}/daily-sales, /texts
│       └── search.py          # GET /search?q=&month=
├── frontend/
│   └── viewer/
│       └── index.html         # 閲覧 UI（週タブ・KPI・テキスト3セクション）
├── data/                      # アップロード済み Excel ファイル置き場（約30件）
├── schema.sql                 # Supabase 上のテーブル定義（参照用）
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
  content           text          -- ＜営業報告＞以降の本文（全セクション含む）
)
```

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
- `052副店長...` / `069販売部週報26-03副.xlsx` → 副店長ファイル（`_is_fuku_tencho` 判定）
- `売上進捗表` シートが存在しないファイル → `SkipFileError`

---

## 実装済み機能（Phase 1 + Phase 2 進行中）

### バックエンド API
| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/upload` | Excel アップロード → パース → DB 保存 |
| GET  | `/reports?area_id=` | 週報一覧（store_name / submitter_role / area_id 付き） |
| GET  | `/reports/{id}/daily-sales` | 日別売上・客数・天候・予算比・昨対客数 |
| GET  | `/reports/{id}/texts` | 週報テキスト一覧（sheet_index 昇順） |
| GET  | `/search?q=&month=` | 週報テキスト全文検索（最大50件） |
| GET  | `/health` | ヘルスチェック |
| GET  | `/viewer/` | フロントエンド静的ファイル配信 |

### パーサー（excel_parser.py）
- `SkipFileError` 例外: store_code='0000' / 売上進捗表シートなし
- `_is_fuku_tencho`: 副店長ファイル判定（「副店長」含む or ファイル名末尾「副」）
- 副店長ファイル: `store_name` 末尾に「副」付加、`submitter_role='副店長'`
- 店舗名重複防止: 候補に「店」が付いていれば追加しない
- `SIGNATURE_WORDS`: 署名セル（社長・部長・エリア長等）を除去
- 週ブロック追加抽出: `sales_budget_ratio`（row+5）・`customer_count_prev_year`（row+7）

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
  - 年月セレクター（最新月デフォルト）
  - エリアセレクター（全エリア / 第1〜3エリア）
  - 店舗名テキスト検索
  - `×` フィルタークリアボタン
  - 店舗グループ表示（`[店長]` `[副]` バッジ、同一 store_name をグループ化）
- **メインエリア**:
  - サマリーバー（月間売上・客数・日商平均）
  - 日別売上グラフ（Chart.js bar+line、4系列: 売上・客数・売上予算比・客数昨対比）
  - **週タブ** `第N週(mm/dd)` 形式
  - 各タブ: 週計 KPI + テキスト4セクション
    - 営業報告（日付始まり行で段落整形、黒背景セル行は赤枠・太字強調）
    - 防災チェックリスト（`<details>` 折りたたみ、2列グリッド）
    - 人員管理報告
    - 来期コメント（署名除去済み）

### バッチツール
- `reprocess_all.py`: `data/` 内全ファイルを再パース・DB 上書き（SkipFileError は SKIP 表示）

---

## 起動方法

```bash
cd C:\Users\landrome\weekly-report-kb
uvicorn backend.main:app --reload
# → http://127.0.0.1:8000/viewer/
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

### 未着手
1. **未提出店舗グレーアウト** — m_store 全23店舗を取得し、週報未提出店舗をサイドバーにグレー表示
2. **月次サマリーダッシュボード** — 全店舗月次集計（売上・客数・日商平均・前月比）
3. **アップロード UI** — フロントエンドにドラッグ&ドロップ画面追加
4. **エクスポート** — CSV/Excel 出力エンドポイント

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
- `report_texts` には使われていない `has_highlight boolean` カラムが Supabase 上に残存（`ALTER TABLE report_texts ADD COLUMN has_highlight boolean NOT NULL DEFAULT false` で追加済み）。コードでは未参照。
