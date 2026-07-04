import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, jsonify, redirect, render_template, request, send_file, url_for

log = logging.getLogger(__name__)

from app.config import APP_VERSION, COUNTRIES, HEADERS, ICONS_DIR
from app.db import (
    clear_games_cache,
    get_cached_price_history,
    get_config,
    load_games_cache,
    load_performance_cache,
    save_price_history_cache,
    set_config,
)
from app.performance import normalize_name
from app.exchange import fetch_rate
from app.scraper import _content_type_to_ext, _icon_path, _make_headers, download_icons, fetch_all_games
from app.user import get_db_path, get_user_email

web_bp = Blueprint("web", __name__)


@web_bp.app_context_processor
def inject_app_version():
    """Expose the running build's version to every template."""
    return {"app_version": APP_VERSION}

_refresh_lock = threading.Lock()
_refreshing_dbs: set[str] = set()
_refresh_progress: dict[str, dict] = {}


def _background_refresh(db_path: str, wishlist_url: str, locales: list[str], reference_locale: str, user_agent: str = None) -> None:
    def _on_progress(step: str, locale, current, total) -> None:
        _refresh_progress[db_path] = {"step": step, "locale": locale, "current": current, "total": total}

    try:
        fetch_all_games(
            db_path,
            wishlist_url=wishlist_url,
            locales=locales,
            reference_locale=reference_locale,
            user_agent=user_agent,
            on_progress=_on_progress,
        )
    except Exception as exc:
        log.warning("background_refresh failed for %s: %s", db_path, exc)
    finally:
        with _refresh_lock:
            _refreshing_dbs.discard(db_path)
        _refresh_progress.pop(db_path, None)


def _trigger_background_refresh(db_path: str, wishlist_url: str, locales: list[str], reference_locale: str, user_agent: str = None) -> None:
    with _refresh_lock:
        if db_path in _refreshing_dbs:
            return
        _refreshing_dbs.add(db_path)
    threading.Thread(
        target=_background_refresh,
        args=(db_path, wishlist_url, locales, reference_locale, user_agent),
        daemon=True,
    ).start()


def _parse_price(s: str) -> float:
    """Parse a price string from any locale format to float."""
    clean = re.sub(r'[^\d,.]', '', s.strip())
    if not clean:
        return 0.0
    dot_count = clean.count('.')
    comma_count = clean.count(',')
    if dot_count == 0 and comma_count == 0:
        return float(clean) if clean else 0.0
    if dot_count > 1:
        clean = clean.replace('.', '')
        if comma_count == 1:
            clean = clean.replace(',', '.')
    elif comma_count > 1:
        clean = clean.replace(',', '')
    elif dot_count == 1 and comma_count == 1:
        if clean.rfind(',') > clean.rfind('.'):
            clean = clean.replace('.', '').replace(',', '.')
        else:
            clean = clean.replace(',', '')
    elif comma_count == 1:
        parts = clean.split(',')
        if len(parts[1]) <= 2:
            clean = clean.replace(',', '.')
        else:
            clean = clean.replace(',', '')
    try:
        return float(clean)
    except ValueError:
        return 0.0


def _format_price(value: float, locale: str) -> str:
    """Format a float as a price string in the given locale's currency style."""
    info = COUNTRIES.get(locale, {})
    symbol = info.get("symbol", "")
    iso = info.get("iso", "")
    if iso in ("JPY", "CLP", "COP"):
        amount = f"{int(round(value)):,}"
        return f"{symbol}{amount}" if len(symbol) == 1 else f"{symbol} {amount}"
    if locale == "br":
        formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{symbol} {formatted}"
    if len(symbol) > 1:
        return f"{symbol} {value:,.2f}"
    return f"{symbol}{value:,.2f}"


def _parse_wishlist_input(input_str: str) -> Tuple[Optional[str], Optional[str]]:
    input_str = input_str.strip()
    if not input_str:
        return (None, "Please enter a valid wishlist code or full URL")
    if input_str.startswith("http://") or input_str.startswith("https://"):
        parsed = urlparse(input_str)
        clean = urlunparse(parsed._replace(query="", fragment=""))
        return (clean, None)
    if re.fullmatch(r"[a-zA-Z0-9_-]+", input_str):
        return (f"https://www.dekudeals.com/wishlist/{input_str}", None)
    return (None, "Please enter a valid wishlist code or full URL")


def _validate_wishlist_url(url: str, user_agent: str = None) -> Optional[str]:
    try:
        response = requests.get(url, headers=_make_headers(user_agent), timeout=5)
        if response.status_code == 404:
            return "Wishlist not found (404). Please check the code or URL."
        elif response.status_code != 200:
            return f"Server error (HTTP {response.status_code}). Try again later."
        return None
    except requests.Timeout:
        return "Request timed out. Check your internet connection."
    except requests.ConnectionError:
        return "Connection error. Check your internet connection."
    except Exception as e:
        return f"Error validating URL: {str(e)}"


def _get_selected_locales() -> list[str]:
    raw = get_config("SELECTED_CURRENCIES", get_db_path())
    return json.loads(raw) if raw else ["br", "us"]


def _get_reference_locale(selected_locales: list[str]) -> str:
    ref = get_config("REFERENCE_CURRENCY", get_db_path())
    return ref if ref and ref in selected_locales else selected_locales[0]


def _compute_best_buy(games: list[dict], selected_locales: list[str], reference_locale: str) -> None:
    """Annotate each game dict with best_buy, best_buy_label, best_buy_save, has_discount, all_unavailable."""
    rates = {
        locale: fetch_rate(locale, reference_locale)
        for locale in selected_locales
        if locale != reference_locale
    }

    for g in games:
        prices = g.get("prices", {})
        selected_prices = {k: v for k, v in prices.items() if k in selected_locales}

        g["has_discount"] = any(p.get("discount") for p in selected_prices.values())
        g["all_unavailable"] = bool(selected_prices) and all(
            not p.get("current") or p.get("current") == "Unavailable"
            for p in selected_prices.values()
        )

        if len(selected_locales) < 2:
            g["best_buy"] = ""
            g["best_buy_label"] = ""
            g["best_buy_save"] = ""
            continue

        ref_str = prices.get(reference_locale, {}).get("current", "")
        ref_val = _parse_price(ref_str) if ref_str and ref_str != "Unavailable" else 0.0

        best_locale = None
        best_in_ref: Optional[float] = None

        for locale in selected_locales:
            if locale == reference_locale:
                continue
            cur_str = prices.get(locale, {}).get("current", "")
            if not cur_str or cur_str == "Unavailable":
                continue
            cur_val = _parse_price(cur_str)
            if cur_val <= 0:
                continue
            price_in_ref = cur_val * rates.get(locale, 1.0)
            if best_in_ref is None or price_in_ref < best_in_ref:
                best_in_ref = price_in_ref
                best_locale = locale

        if best_locale is None or best_in_ref is None:
            if ref_val > 0:
                g["best_buy"] = reference_locale
                g["best_buy_label"] = _format_price(ref_val, reference_locale)
                g["best_buy_save"] = ""
            else:
                g["best_buy"] = ""
                g["best_buy_label"] = ""
                g["best_buy_save"] = ""
            continue

        label = _format_price(best_in_ref, reference_locale)

        if ref_val <= 0:
            g["best_buy"] = best_locale
            g["best_buy_label"] = label
            g["best_buy_save"] = ""
            continue

        diff = ref_val - best_in_ref
        if diff > 0.005:
            g["best_buy"] = best_locale
            g["best_buy_label"] = label
            g["best_buy_save"] = _format_price(diff, reference_locale)
        elif diff < -0.005:
            g["best_buy"] = reference_locale
            g["best_buy_label"] = _format_price(ref_val, reference_locale)
            g["best_buy_save"] = _format_price(-diff, reference_locale)
        else:
            g["best_buy"] = "eq"
            g["best_buy_label"] = label
            g["best_buy_save"] = ""

    for g in games:
        sale_ends = g.get("sale_ends", {})
        if not sale_ends:
            continue
        effective = g.get("best_buy", "")
        if effective in ("", "eq"):
            effective = reference_locale
        g["sale_end"] = sale_ends.get(effective, g.get("sale_end", ""))


_SW2_PATCH_TYPES = {"Switch 2 Edition", "Free Update"}


def _annotate_performance(games: list[dict], db_path: str) -> None:
    """Set perf_label/perf_sort/perf_sw2 on each game from performance_cache.

    Smart column: show the sheet's fps; mark perf_sw2 when a genuine Switch 2
    version exists (DekuDeals switch2 flag OR a Switch 2 patch type).
    """
    perf = load_performance_cache(db_path)
    for g in games:
        row = perf.get(normalize_name(g.get("name", "")))
        if not row:
            g["perf_label"] = ""
            g["perf_sort"] = 0
            g["perf_sw2"] = False
            continue
        g["perf_label"] = row["label"]
        g["perf_sort"] = row["fps"]
        g["perf_sw2"] = bool(g.get("switch2")) or row.get("patch_type") in _SW2_PATCH_TYPES


@web_bp.route("/")
def index():
    db_path = get_db_path()
    user_email = get_user_email()
    wishlist_url = get_config("WISHLIST_URL", db_path)
    wishlist_configured = wishlist_url is not None
    selected_locales = _get_selected_locales()
    reference_locale = _get_reference_locale(selected_locales)
    exchange_rates: dict = {}

    user_agent = request.headers.get("User-Agent")

    if not wishlist_configured:
        return render_template(
            "index.html",
            wishlist_configured=False,
            wishlist_error=False,
            wishlist_error_message=None,
            games=None,
            on_sale=0,
            unavailable=0,
            last_updated="Never",
            total=0,
            selected_locales=selected_locales,
            reference_locale=reference_locale,
            countries=COUNTRIES,
            exchange_rates=exchange_rates,
            user_email=user_email,
        )

    games, fetched_at, is_stale = load_games_cache(db_path)
    if games is None:
        log.info("index: cache miss — starting background fetch (locales=%s)", selected_locales)
        _trigger_background_refresh(db_path, wishlist_url, selected_locales, reference_locale, user_agent)
        return render_template(
            "index.html",
            wishlist_configured=True,
            wishlist_error=False,
            wishlist_error_message=None,
            games=None,
            loading_in_progress=True,
            on_sale=0,
            unavailable=0,
            last_updated="Never",
            total=0,
            selected_locales=selected_locales,
            reference_locale=reference_locale,
            countries=COUNTRIES,
            exchange_rates={},
            user_email=user_email,
        )
    else:
        if is_stale:
            log.info("index: stale cache — serving %d games, refreshing in background", len(games))
            _trigger_background_refresh(db_path, wishlist_url, selected_locales, reference_locale, user_agent)
        else:
            log.info("index: cache hit — %d games from cache", len(games))
        threading.Thread(target=download_icons, args=(games, user_agent), daemon=True).start()

    last_updated = (
        datetime.fromtimestamp(fetched_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if fetched_at
        else "Never"
    )

    for locale in selected_locales:
        if locale != reference_locale:
            exchange_rates[locale] = fetch_rate(locale, reference_locale)

    _compute_best_buy(games, selected_locales, reference_locale)
    _annotate_performance(games, db_path)

    on_sale = sum(1 for g in games if g.get("has_discount"))
    unavailable = sum(1 for g in games if g.get("all_unavailable"))

    return render_template(
        "index.html",
        wishlist_configured=True,
        wishlist_error=False,
        wishlist_error_message=None,
        games=games,
        loading_in_progress=False,
        last_updated=last_updated,
        fetched_at_ts=int(fetched_at) if fetched_at else 0,
        total=len(games),
        on_sale=on_sale,
        unavailable=unavailable,
        selected_locales=selected_locales,
        reference_locale=reference_locale,
        countries=COUNTRIES,
        exchange_rates=exchange_rates,
        user_email=user_email,
    )


def _filter_games(games: list[dict], args) -> list[dict]:
    """Apply every toolbar filter server-side. All criteria are AND-combined.

    Query params:
      platform  comma-separated switch1/switch2 (a game must match every flag)
      sale=1    only games currently discounted
      available=1  hide games that are unavailable in all selected currencies
      bestbuy   only games whose cheapest currency equals this locale code
      q         case-insensitive substring match on the game name
    """
    platforms = [p for p in args.get("platform", "").split(",") if p in ("switch1", "switch2")]
    if platforms:
        games = [g for g in games if all(g.get(p) for p in platforms)]
    if args.get("sale") == "1":
        games = [g for g in games if g.get("has_discount")]
    if args.get("available") == "1":
        games = [g for g in games if not g.get("all_unavailable")]
    bestbuy = args.get("bestbuy", "")
    if bestbuy:
        games = [g for g in games if g.get("best_buy") == bestbuy]
    q = args.get("q", "").strip().lower()
    if q:
        games = [g for g in games if q in g.get("name", "").lower()]
    return games


@web_bp.route("/api/games-table")
def api_games_table():
    """Render the games table rows server-side, filtered by the toolbar criteria."""
    db_path = get_db_path()
    selected_locales = _get_selected_locales()
    reference_locale = _get_reference_locale(selected_locales)

    games, _fetched_at, _is_stale = load_games_cache(db_path)
    if games is None:
        games = []
    else:
        _compute_best_buy(games, selected_locales, reference_locale)
        games = _filter_games(games, request.args)
        _annotate_performance(games, db_path)

    return render_template(
        "components/games_rows.html",
        games=games,
        selected_locales=selected_locales,
        reference_locale=reference_locale,
        countries=COUNTRIES,
    )


@web_bp.route("/api/status")
def api_status():
    db_path = get_db_path()
    games, fetched_at, is_stale = load_games_cache(db_path)
    with _refresh_lock:
        refreshing = db_path in _refreshing_dbs
    return jsonify({
        "ready": games is not None,
        "refreshing": refreshing,
        "progress": _refresh_progress.get(db_path),
    })


@web_bp.route("/api/config", methods=["GET"])
def get_config_api():
    db_path = get_db_path()
    url = get_config("WISHLIST_URL", db_path)
    selected_locales = _get_selected_locales()
    reference_locale = _get_reference_locale(selected_locales)
    return jsonify({
        "wishlist_url": url,
        "configured": url is not None,
        "selected_currencies": selected_locales,
        "reference_currency": reference_locale,
    })


@web_bp.route("/api/config", methods=["POST"])
def set_config_api():
    db_path = get_db_path()
    data = request.get_json()
    has_currencies = data and ("selected_currencies" in data or "reference_currency" in data)
    has_input = data and "input" in data

    if not has_currencies and not has_input:
        return jsonify({"success": False, "error": "Missing 'input' field"}), 400

    if has_currencies:
        selected = data.get("selected_currencies")
        if selected is not None:
            if not isinstance(selected, list) or len(selected) == 0:
                return jsonify({"success": False, "error": "At least one currency must be selected"}), 400
            invalid = [c for c in selected if c not in COUNTRIES]
            if invalid:
                return jsonify({"success": False, "error": f"Invalid locale codes: {invalid}"}), 400
            set_config("SELECTED_CURRENCIES", json.dumps(selected), db_path)
            clear_games_cache(db_path)

        reference = data.get("reference_currency")
        if reference is not None:
            current_raw = get_config("SELECTED_CURRENCIES", db_path)
            current_selected = json.loads(current_raw) if current_raw else []
            if reference not in current_selected:
                return jsonify({"success": False, "error": "Reference currency must be one of the selected currencies"}), 400
            set_config("REFERENCE_CURRENCY", reference, db_path)

    if not has_input:
        return jsonify({"success": True}), 200

    url, parse_error = _parse_wishlist_input(data["input"])
    if parse_error:
        return jsonify({"success": False, "error": parse_error}), 400

    user_agent = request.headers.get("User-Agent")
    validation_error = _validate_wishlist_url(url, user_agent)
    if validation_error:
        return jsonify({"success": False, "error": validation_error}), 400

    set_config("WISHLIST_URL", url, db_path)
    return jsonify({"success": True, "wishlist_url": url}), 200


DEMO_WISHLIST_URL = "https://www.dekudeals.com/wishlist/x8kxhn96yf"


@web_bp.route("/demo")
def demo():
    db_path = get_db_path()
    if get_config("WISHLIST_URL", db_path) is None:
        set_config("WISHLIST_URL", DEMO_WISHLIST_URL, db_path)
    return redirect(url_for("web.index"))


@web_bp.route("/refresh", methods=["POST"])
def refresh():
    db_path = get_db_path()
    wishlist_url = get_config("WISHLIST_URL", db_path)
    selected_locales = _get_selected_locales()
    reference_locale = _get_reference_locale(selected_locales)
    user_agent = request.headers.get("User-Agent")
    clear_games_cache(db_path)
    _trigger_background_refresh(db_path, wishlist_url, selected_locales, reference_locale, user_agent)
    return redirect(url_for("web.index"))


@web_bp.route("/icons/<path:slug>")
def serve_icon(slug: str):
    db_path = get_db_path()
    safe_slug = slug.replace("/", "_")[:80]
    # Strip image extension from slug so URLs like /icons/game.jpg work correctly
    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    base, ext = os.path.splitext(safe_slug)
    search_base = base if ext.lower() in _IMAGE_EXTS else safe_slug
    icon_found = None
    for fname in os.listdir(ICONS_DIR) if os.path.exists(ICONS_DIR) else []:
        if fname.startswith(search_base + "."):
            icon_found = os.path.join(ICONS_DIR, fname)
            break

    if not icon_found:
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT image_url FROM games_cache WHERE slug = ?", (slug,)
                ).fetchone()
            if row and row[0]:
                resp = requests.get(row[0], headers=_make_headers(request.headers.get("User-Agent")), timeout=15)
                resp.raise_for_status()
                os.makedirs(ICONS_DIR, exist_ok=True)
                ext = _content_type_to_ext(resp.headers.get("content-type", ""))
                icon_found = _icon_path(slug, ext)
                with open(icon_found, "wb") as fh:
                    fh.write(resp.content)
        except Exception:
            pass

    if not icon_found or not os.path.exists(icon_found):
        return "", 404

    try:
        ext = os.path.splitext(icon_found)[1].lower()
        mimetype_map = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
        mimetype = mimetype_map.get(ext, "image/jpeg")
        return send_file(icon_found, mimetype=mimetype)
    except (FileNotFoundError, OSError):
        return "", 404


@web_bp.route("/price-history/<path:slug>")
def price_history_api(slug: str):
    db_path = get_db_path()
    currency = request.args.get("currency", "br").lower()
    locale = currency
    cached = get_cached_price_history(slug, currency, db_path)
    if cached is not None:
        return jsonify(cached)
    try:
        from app.scraper import build_session, set_locale

        user_agent = request.headers.get("User-Agent")
        sess = build_session(db_path, locale)
        set_locale(sess, locale, user_agent=user_agent)
        resp = sess.get(
            f"https://www.dekudeals.com/items/{slug}",
            headers=_make_headers(user_agent),
            timeout=30,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        script_tag = soup.find("script", id="price_history_data")
        if not script_tag or not script_tag.string:
            return jsonify({"headers": [], "data": []})
        data = json.loads(script_tag.string)
        save_price_history_cache(slug, currency, data, db_path)
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
