# db.py
import os
from supabase import create_client, Client


_supabase = None


def get_supabase() -> Client:
    global _supabase

    if _supabase is None:
        supabase_url = os.getenv("SUPABASE_URL", "").strip()
        supabase_key = os.getenv("SUPABASE_KEY", "").strip()

        if not supabase_url or not supabase_key:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in .env")

        _supabase = create_client(supabase_url, supabase_key)

    return _supabase