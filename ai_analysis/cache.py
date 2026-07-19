import json
import sqlite3
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_cached(conn: sqlite3.Connection, analyzer: str, ticker: str, cache_key: str) -> dict | None:
    row = conn.execute(
        "SELECT result_json FROM ai_analysis_cache WHERE analyzer = ? AND ticker = ? AND cache_key = ?",
        (analyzer, ticker, cache_key),
    ).fetchone()
    return json.loads(row[0]) if row else None


def store_cache(
    conn: sqlite3.Connection,
    analyzer: str,
    ticker: str,
    cache_key: str,
    result: dict,
    model: str,
    prompt_version: str,
) -> None:
    conn.execute(
        """
        INSERT INTO ai_analysis_cache (analyzer, ticker, cache_key, result_json, model, prompt_version, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (analyzer, ticker, cache_key) DO UPDATE SET
            result_json = excluded.result_json,
            model = excluded.model,
            prompt_version = excluded.prompt_version,
            generated_at = excluded.generated_at
        """,
        (analyzer, ticker, cache_key, json.dumps(result), model, prompt_version, _now_iso()),
    )
