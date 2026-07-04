import json
import sqlite3
import time
from typing import Optional

from requests.cookies import RequestsCookieJar

from app.config import CACHE_TTL, DB_FILE, HISTORY_CACHE_TTL
from app.parsing import parse_release_date, parse_sale_end
import re


def save_cookies(jar: RequestsCookieJar, db_path: str, locale: str = "br") -> None:
    with sqlite3.connect(db_path) as conn:
        rows = [(c.name, c.value, c.domain or "", c.path or "/", locale) for c in jar]
        conn.executemany(
            "INSERT OR REPLACE INTO cookies (name, value, domain, path, locale) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def load_cookies(db_path: str, locale: str = "br") -> list[tuple]:
    try:
        with sqlite3.connect(db_path) as conn:
            return conn.execute(
                "SELECT name, value, domain, path FROM cookies WHERE locale=?",
                (locale,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []


def clear_games_cache(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM games_cache")
        conn.commit()


def save_games_cache(games: list[dict], db_path: str) -> float:
    ts = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM games_cache")
        conn.executemany(
            "INSERT INTO games_cache"
            " (name, slug, prices, release_date, sale_end, image_url, icon_ext, fetched_at, sale_ends, switch1, switch2)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    g["name"],
                    g["slug"],
                    json.dumps(g.get("prices", {})),
                    g.get("release_date", ""),
                    g.get("sale_end", ""),
                    g.get("image_url", ""),
                    g.get("icon_ext", ""),
                    ts,
                    json.dumps(g.get("sale_ends", {})),
                    1 if g.get("switch1") else 0,
                    1 if g.get("switch2") else 0,
                )
                for g in games
            ],
        )
        conn.commit()
    return ts


def load_games_cache(db_path: str) -> tuple[Optional[list[dict]], Optional[float], bool]:
    """Return (games, fetched_at, is_stale). games is None only when there is no cached data at all."""
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT name, slug, prices, release_date, sale_end, image_url, icon_ext, fetched_at, sale_ends, switch1, switch2"
                " FROM games_cache ORDER BY id"
            ).fetchall()
        if not rows:
            return None, None, False
        fetched_at = rows[0][7]
        is_stale = time.time() - fetched_at > CACHE_TTL

        def _normalize_sale_end(val: str) -> str:
            if not val:
                return val
            if val.startswith("Sale ends "):
                return parse_sale_end(val[len("Sale ends "):].strip())
            return val

        def _normalize_release_date(val: str) -> str:
            if not val:
                return val
            if re.fullmatch(r"\d{4}(-\d{2}-\d{2})?", val):
                return val
            return parse_release_date(val)

        def _normalize_sale_ends(raw: str) -> dict:
            try:
                data = json.loads(raw) if raw else {}
                return {k: _normalize_sale_end(v) for k, v in data.items()}
            except (json.JSONDecodeError, AttributeError):
                return {}

        return (
            [
                {
                    "name": r[0],
                    "slug": r[1],
                    "prices": json.loads(r[2]) if r[2] else {},
                    "release_date": _normalize_release_date(r[3]),
                    "sale_end": _normalize_sale_end(r[4]),
                    "image_url": r[5],
                    "icon_ext": r[6],
                    "sale_ends": _normalize_sale_ends(r[8]),
                    "switch1": bool(r[9]),
                    "switch2": bool(r[10]),
                }
                for r in rows
            ],
            fetched_at,
            is_stale,
        )
    except sqlite3.OperationalError:
        return None, None, False


def get_cached_price_history(slug: str, currency: str, db_path: str) -> Optional[dict]:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT data, fetched_at FROM price_history_cache"
                " WHERE slug=? AND currency=?",
                (slug, currency),
            ).fetchone()
        if row and time.time() - row[1] < HISTORY_CACHE_TTL:
            return json.loads(row[0])
    except sqlite3.OperationalError:
        pass
    return None


def save_price_history_cache(slug: str, currency: str, data: dict, db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_history_cache"
            " (slug, currency, data, fetched_at) VALUES (?,?,?,?)",
            (slug, currency, json.dumps(data), time.time()),
        )
        conn.commit()


def save_performance_cache(rows: dict, db_path: str) -> None:
    """Replace the performance cache with the given {norm_name: {fps,label,patch_type}}."""
    ts = time.time()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM performance_cache")
        conn.executemany(
            "INSERT OR REPLACE INTO performance_cache"
            " (norm_name, fps, label, patch_type, fetched_at) VALUES (?,?,?,?,?)",
            [
                (k, v.get("fps", 0), v.get("label", ""), v.get("patch_type", ""), ts)
                for k, v in rows.items()
            ],
        )
        conn.commit()


def load_performance_cache(db_path: str) -> dict:
    """Return {norm_name: {'fps','label','patch_type'}}. Empty dict if table absent."""
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT norm_name, fps, label, patch_type FROM performance_cache"
            ).fetchall()
        return {r[0]: {"fps": r[1], "label": r[2], "patch_type": r[3]} for r in rows}
    except sqlite3.OperationalError:
        return {}


def get_config(key: str, db_path: Optional[str] = None) -> Optional[str]:
    """Retrieve a config value from the database."""
    with sqlite3.connect(db_path or DB_FILE) as conn:
        cursor = conn.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cursor.fetchone()
    return row[0] if row else None


def set_config(key: str, value: str, db_path: Optional[str] = None) -> None:
    """Save or update a config value."""
    with sqlite3.connect(db_path or DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO config (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=?, updated_at=CURRENT_TIMESTAMP
            """,
            (key, value, value),
        )
        conn.commit()
