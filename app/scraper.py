import json
import logging
import os
import re
import time
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup

from app.config import COUNTRIES, DB_FILE, HEADERS, ICONS_DIR, LOCALE_URL, NO_DECIMAL_ISOS, WISHLIST_URL
from app.db import load_cookies, save_cookies, save_games_cache, save_performance_cache
from app.parsing import parse_release_date, parse_sale_end
from app.performance import fetch_performance_sheet

log = logging.getLogger(__name__)


def _make_headers(user_agent: str = None, **extra) -> dict:
    h = {**HEADERS}
    if user_agent:
        h["User-Agent"] = user_agent
    h.update(extra)
    return h


def build_session(db_path: str, locale: str = "br") -> requests.Session:
    session = requests.Session()
    cookies = load_cookies(db_path, locale)
    log.info("build_session: loaded %d cookies for locale=%s", len(cookies), locale)
    for name, value, domain, path in cookies:
        session.cookies.set(name, value, domain=domain, path=path)
    return session


def set_locale(session: requests.Session, country: str, wishlist_url: str = None, user_agent: str = None) -> None:
    url = wishlist_url or WISHLIST_URL
    headers = _make_headers(
        user_agent,
        **{
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.dekudeals.com",
            "Referer": url,
        },
    )
    log.info("set_locale: switching to country=%s", country)
    t0 = time.monotonic()
    resp = session.post(LOCALE_URL, data={"country": country}, headers=headers, timeout=30)
    resp.raise_for_status()
    log.info("set_locale: done (%.2fs, status=%d)", time.monotonic() - t0, resp.status_code)


def fetch_wishlist(session: requests.Session, wishlist_url: str = None, user_agent: str = None) -> str:
    url = wishlist_url or WISHLIST_URL
    log.info("fetch_wishlist: GET %s", url)
    t0 = time.monotonic()
    response = session.get(url + "?sort=release_date", headers=_make_headers(user_agent), timeout=30)
    response.raise_for_status()
    log.info("fetch_wishlist: done (%.2fs, %d bytes)", time.monotonic() - t0, len(response.content))
    return response.text


def extract_games(html: str) -> list[dict]:
    log.info("extract_games: parsing HTML (%d bytes)", len(html))
    soup = BeautifulSoup(html, "html.parser")

    view_list = soup.find(class_="view-list")
    if view_list is None:
        raise RuntimeError("Could not find .view-list on the page.")

    games = []
    for tag in view_list.find_all("a", class_="main-link", href=True):
        if not tag["href"].startswith("/items/"):
            continue
        name = tag.get_text(strip=True)
        if not name:
            continue

        # Walk up to the per-item container (div.col)
        container = tag.find_parent("div", class_="col")
        if container is None:
            continue

        original = container.find("s", class_="text-muted")
        current = container.find("strong")
        badge = container.find("span", class_="badge-danger")

        # Release date + sale end are in the no-class div inside the inner column
        inner = tag.find_parent("div", class_="d-flex flex-column flex-grow-1")
        date_div = None
        if inner:
            for child in inner.children:
                if hasattr(child, "get") and child.get("class") is None:
                    txt = child.get_text(" ", strip=True)
                    if txt:
                        date_div = txt
                        break

        release_date = ""
        sale_end = ""
        if date_div:
            if "Sale ends" in date_div:
                parts = date_div.split("Sale ends", 1)
                release_date = parse_release_date(parts[0].strip())
                sale_end = parse_sale_end(parts[1].strip())
            else:
                release_date = parse_release_date(date_div.strip())

        slug = tag["href"].removeprefix("/items/")

        img_tag = container.find("img")
        image_url = ""
        if img_tag:
            image_url = (
                img_tag.get("src")
                or img_tag.get("data-src")
                or img_tag.get("data-lazy-src")
                or ""
            )

        games.append(
            {
                "name": name,
                "slug": slug,
                "original": original.get_text(strip=True) if original else "",
                "current": current.get_text(strip=True) if current else "Unavailable",
                "discount": badge.get_text(strip=True) if badge else "",
                "release_date": release_date,
                "sale_end": sale_end,
                "image_url": image_url,
            }
        )

    log.info("extract_games: found %d games", len(games))
    return games


def merge_prices(games_by_locale: dict[str, list[dict]], reference_locale: str) -> list[dict]:
    """Merge prices from N locales into a unified game list keyed by game name."""
    by_name: dict[str, dict[str, dict]] = {}
    meta_by_name: dict[str, dict] = {}

    for locale, games in games_by_locale.items():
        for g in games:
            name = g["name"]
            if name not in by_name:
                by_name[name] = {}
            by_name[name][locale] = {
                "original": g["original"],
                "current": g["current"],
                "discount": g["discount"],
            }
            if name not in meta_by_name:
                meta_by_name[name] = {
                    "slug": g["slug"],
                    "release_date": g["release_date"],
                    "sale_ends": {},
                    "image_url": g.get("image_url", ""),
                    "switch1": g.get("switch1", False),
                    "switch2": g.get("switch2", False),
                }
            # Platform flags come from item pages; OR across locales so a game
            # classified in any locale keeps its flags.
            meta_by_name[name]["switch1"] = meta_by_name[name].get("switch1", False) or g.get("switch1", False)
            meta_by_name[name]["switch2"] = meta_by_name[name].get("switch2", False) or g.get("switch2", False)
            if locale == reference_locale:
                meta_by_name[name]["slug"] = g["slug"]
                meta_by_name[name]["release_date"] = g["release_date"]
                meta_by_name[name]["image_url"] = g.get("image_url", "")
            if g.get("sale_end"):
                meta_by_name[name]["sale_ends"][locale] = g["sale_end"]

    ref_names = [g["name"] for g in games_by_locale.get(reference_locale, [])]
    other_names = [n for n in by_name if n not in ref_names]

    result = []
    for name in ref_names + other_names:
        meta = meta_by_name[name]
        sale_ends = meta.get("sale_ends", {})
        result.append({
            "name": name,
            "slug": meta["slug"],
            "prices": by_name[name],
            "release_date": meta["release_date"],
            "sale_end": sale_ends.get(reference_locale, ""),
            "sale_ends": sale_ends,
            "image_url": meta["image_url"],
            "switch1": meta.get("switch1", False),
            "switch2": meta.get("switch2", False),
        })
    return result


def _icon_path(slug: str, ext: str = "jpg") -> str:
    """Return the local filesystem path for a game icon."""
    safe = slug.replace("/", "_")[:80]
    return os.path.join(ICONS_DIR, f"{safe}.{ext}")


def _content_type_to_ext(content_type: str) -> str:
    ct = content_type.lower()
    if "png" in ct:
        return "png"
    if "webp" in ct:
        return "webp"
    if "gif" in ct:
        return "gif"
    return "jpg"


def _existing_icon_ext(slug: str) -> str:
    """Return the extension of an already-downloaded icon, or empty string."""
    safe = slug.replace("/", "_")[:80]
    if not os.path.exists(ICONS_DIR):
        return ""
    for fname in os.listdir(ICONS_DIR):
        if fname.startswith(safe + "."):
            return os.path.splitext(fname)[1].lstrip(".")
    return ""


def download_icons(games: list[dict], user_agent: str = None) -> dict[str, str]:
    """Download missing game icons into ICONS_DIR. Returns slug→ext mapping."""
    os.makedirs(ICONS_DIR, exist_ok=True)
    result: dict[str, str] = {}
    to_download = [g for g in games if not _existing_icon_ext(g["slug"]) and g.get("image_url")]
    cached = len(games) - len(to_download)
    log.info("download_icons: %d cached, %d to download", cached, len(to_download))
    for g in games:
        slug = g["slug"]
        existing = _existing_icon_ext(slug)
        if existing:
            result[slug] = existing
            continue
        url = g.get("image_url", "")
        if not url:
            continue
        try:
            log.debug("download_icons: fetching icon for %s", slug)
            resp = requests.get(url, headers=_make_headers(user_agent), timeout=15)
            resp.raise_for_status()
            ext = _content_type_to_ext(resp.headers.get("content-type", ""))
            path = _icon_path(slug, ext)
            with open(path, "wb") as fh:
                fh.write(resp.content)
            result[slug] = ext
        except Exception as exc:
            log.warning("download_icons: failed for %s: %s", slug, exc)
    log.info("download_icons: finished (%d icons saved)", len(result))
    return result


def _format_eshop_value(value: int, locale: str) -> str:
    """Format an eShop price (in minor currency units from outAnalytics) as a locale string."""
    info = COUNTRIES.get(locale, {})
    symbol = info.get("symbol", "")
    iso = info.get("iso", "")
    if iso in NO_DECIMAL_ISOS:
        amount = f"{int(round(value)):,}"
        return f"{symbol}{amount}" if len(symbol) == 1 else f"{symbol} {amount}"
    val = value / 100.0
    if locale == "br":
        formatted = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{symbol} {formatted}"
    if len(symbol) > 1:
        return f"{symbol} {val:,.2f}"
    return f"{symbol}{val:,.2f}"


def _parse_eshop_analytics(html: str) -> dict:
    """Extract eShop price data from outAnalytics scripts on a DekuDeals item page.

    Matches both 'eshop:' (US) and 'eshop_br:', 'eshop_jp:' etc. (other locales).
    Returns {"value": int, "discount": int} in minor currency units, or {}.
    """
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        content = script.string or ""
        if "outAnalytics['eshop" not in content:
            continue
        m = re.search(r"outAnalytics\['eshop[^']*'\]\s*=\s*(\{[^\n]+)", content)
        if not m:
            continue
        try:
            data = json.loads(m.group(1).rstrip(";"))
            value = data.get("value", 0)
            items_list = data.get("items", [{}])
            item = items_list[0] if items_list else {}
            discount = item.get("discount", 0)
            return {"value": value, "discount": discount}
        except (json.JSONDecodeError, IndexError):
            pass
    return {}


def _parse_platforms(html: str) -> dict:
    """Extract Nintendo console availability from a DekuDeals item page.

    The item page lists supported platforms in `ul.details` under a `Platforms:`
    line, e.g. "Nintendo Switch, PlayStation 5, Steam". Returns
    {"switch1": bool, "switch2": bool}. A game may support both.

    Matches exact comma-separated tokens (not a substring search over the whole
    details text) so the release-date line — which also contains "Switch" — does
    not produce a false positive.
    """
    result = {"switch1": False, "switch2": False}
    soup = BeautifulSoup(html, "html.parser")
    details = soup.find("ul", class_="details")
    if details is None:
        return result
    for li in details.find_all("li", class_="list-group-item", recursive=False):
        strong = li.find("strong")
        if not strong or not strong.get_text(strip=True).startswith("Platforms"):
            continue
        text = li.get_text(" ", strip=True)
        text = text.split(":", 1)[1] if ":" in text else text
        tokens = [t.strip() for t in text.split(",")]
        result["switch1"] = "Nintendo Switch" in tokens
        result["switch2"] = "Nintendo Switch 2" in tokens
        break
    return result


def _fetch_eshop_prices(
    games: list[dict],
    locale: str,
    session: requests.Session,
    user_agent: str = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Fetch eShop price from each game's item page and overwrite wishlist prices in place.

    The wishlist shows the best price across all stores (eShop, Amazon, etc.). This
    function replaces those prices with eShop-only prices so retail discounts are ignored.
    """
    if not games:
        return

    cookies = [(c.name, c.value, c.domain, c.path) for c in session.cookies]
    headers = _make_headers(user_agent)
    total = len(games)
    log.info("_fetch_eshop_prices: fetching %d item pages (locale=%s)", total, locale)

    delay = 0.01
    results = []
    for i, game in enumerate(games):
        slug = game["slug"]
        while True:
            try:
                s = requests.Session()
                for name, val, domain, path in cookies:
                    s.cookies.set(name, val, domain=domain, path=path)
                time.sleep(delay)
                resp = s.get(
                    f"https://www.dekudeals.com/items/{slug}",
                    headers=headers,
                    timeout=30,
                )
                resp.raise_for_status()
                results.append((slug, _parse_eshop_analytics(resp.text), _parse_platforms(resp.text)))
                break
            except requests.exceptions.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    delay += 0.01
                    log.warning("_fetch_eshop_prices: 429 for %s, backing off 1s then retrying with delay=%.3fs", slug, delay)
                    time.sleep(1.0)
                    continue
                log.warning("_fetch_eshop_prices: %s failed: %s", slug, exc)
                results.append((slug, {}, {}))
                break
            except Exception as exc:
                log.warning("_fetch_eshop_prices: %s failed: %s", slug, exc)
                results.append((slug, {}, {}))
                break
        if on_progress:
            on_progress(i + 1, total)

    slug_to_game = {g["slug"]: g for g in games}
    for slug, eshop, platforms in results:
        game = slug_to_game.get(slug)
        if not game:
            continue

        game["switch1"] = platforms.get("switch1", False)
        game["switch2"] = platforms.get("switch2", False)

        if not eshop:
            continue

        value = eshop["value"]
        discount = eshop["discount"]

        if value == 0:
            game["current"] = "Unavailable"
            game["original"] = ""
            game["discount"] = ""
            game["sale_end"] = ""
            continue

        game["current"] = _format_eshop_value(value, locale)
        game["original"] = _format_eshop_value(value + discount, locale) if discount > 0 else ""
        game["discount"] = f"-{round(discount / (value + discount) * 100)}%" if discount > 0 else ""
        if not game["discount"]:
            game["sale_end"] = ""

    log.info("_fetch_eshop_prices: done (locale=%s)", locale)


def _refresh_performance(db_path: str, user_agent: str = None) -> None:
    """Best-effort: fetch+parse the community FPS sheet and save it. Outage-safe.

    Only overwrites performance_cache on a successful, non-empty fetch, so a
    transient sheet outage keeps the previous data instead of blanking it.
    """
    try:
        rows = fetch_performance_sheet(user_agent=user_agent)
        if rows:
            save_performance_cache(rows, db_path)
            log.info("_refresh_performance: saved %d entries", len(rows))
        else:
            log.warning("_refresh_performance: sheet parsed to 0 rows, keeping previous cache")
    except Exception as exc:
        log.warning("_refresh_performance: failed, keeping previous cache: %s", exc)


def fetch_all_games(
    db_path: str = DB_FILE,
    wishlist_url: str = None,
    locales: list[str] = None,
    reference_locale: str = None,
    user_agent: str = None,
    on_progress: Optional[Callable[[str, str, Optional[int], Optional[int]], None]] = None,
) -> tuple[list[dict], float]:
    url = wishlist_url or WISHLIST_URL
    if locales is None:
        locales = ["br", "us"]
    if reference_locale is None:
        reference_locale = locales[0]

    log.info("fetch_all_games: starting (locales=%s, reference=%s)", locales, reference_locale)
    t_start = time.monotonic()

    sessions: dict[str, requests.Session] = {}
    games_by_locale: dict[str, list[dict]] = {}

    # Phase 1: fetch all wishlists first to know exact game counts per locale
    for locale in locales:
        log.info("fetch_all_games: fetching wishlist locale=%s", locale)
        session = build_session(db_path, locale)
        sessions[locale] = session
        if not list(session.cookies):
            log.info("fetch_all_games: no cookies for locale=%s, calling set_locale", locale)
            if on_progress:
                on_progress("set_locale", locale, None, None)
            set_locale(session, locale, wishlist_url=url, user_agent=user_agent)
        if on_progress:
            on_progress("fetch_wishlist", locale, None, None)
        games_by_locale[locale] = extract_games(fetch_wishlist(session, wishlist_url=url, user_agent=user_agent))

    # Phase 2: fetch prices using cumulative offsets across all locales
    global_total = sum(len(g) for g in games_by_locale.values())
    global_offset = 0

    for locale in locales:
        locale_games = games_by_locale[locale]
        locale_offset = global_offset

        def _make_price_progress(loc: str, offset: int) -> Callable[[int, int], None]:
            def _cb(current: int, total: int) -> None:
                if on_progress:
                    on_progress("eshop_prices", loc, offset + current, global_total)
            return _cb

        _fetch_eshop_prices(
            locale_games, locale, sessions[locale], user_agent=user_agent,
            on_progress=_make_price_progress(locale, locale_offset) if on_progress else None,
        )
        save_cookies(sessions[locale].cookies, db_path, locale=locale)
        global_offset += len(locale_games)

    games = merge_prices(games_by_locale, reference_locale=reference_locale)
    log.info("fetch_all_games: merged %d games, downloading icons", len(games))
    if on_progress:
        on_progress("icons", None, None, None)
    icon_exts = download_icons(games, user_agent=user_agent)
    for g in games:
        g["icon_ext"] = icon_exts.get(g["slug"], "")
    if on_progress:
        on_progress("performance", None, None, None)
    _refresh_performance(db_path, user_agent=user_agent)
    ts = save_games_cache(games, db_path)
    log.info("fetch_all_games: done in %.2fs", time.monotonic() - t_start)
    return games, ts
