import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_client: Client | None = None
_write_client: Client | None = None


def get_client() -> Client:
    """読み取り用 Supabase クライアント（anon キー）のシングルトンを返す。"""
    global _client
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
    return _client


def get_write_client() -> Client:
    """
    書き込み用 Supabase クライアント（service_role キー）のシングルトンを返す。

    service_role キーは RLS をバイパスするため、INSERT/UPDATE/DELETE に使用する。
    このキーは秘匿情報であり、フロントエンドに渡してはならない。
    """
    global _write_client
    if _write_client is None:
        _write_client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
    return _write_client
