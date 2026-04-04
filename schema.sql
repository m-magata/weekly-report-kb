-- Supabase SQL Editor で一度だけ実行してください
-- 既存の m_store テーブルを参照して週報データを格納します

create table if not exists weekly_reports (
    id              bigserial primary key,
    store_id        bigint references m_store(store_id) on delete cascade,
    week_start      date not null,
    week_end        date not null,
    source_filename text,
    unique (store_id, week_start, week_end)
);

create table if not exists daily_sales (
    id                bigserial primary key,
    weekly_report_id  bigint references weekly_reports(id) on delete cascade,
    date              date not null,
    sales_amount      float,
    customer_count    integer,
    weather           text
);

create table if not exists report_texts (
    id                bigserial primary key,
    weekly_report_id  bigint references weekly_reports(id) on delete cascade,
    sheet_index       integer not null,
    sheet_name        text,
    content           text
);

create table if not exists digest_cache (
    id          bigserial primary key,
    cache_key   text not null unique,   -- "{year}-{month_from}-{month_to}"
    digest_text text not null,
    created_at  timestamptz not null default now()
);
