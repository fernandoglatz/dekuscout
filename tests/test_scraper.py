import os
import pytest


# ── _content_type_to_ext ──────────────────────────────────────────────────────

def test_content_type_png():
    from app.scraper import _content_type_to_ext
    assert _content_type_to_ext("image/png") == "png"


def test_content_type_webp():
    from app.scraper import _content_type_to_ext
    assert _content_type_to_ext("image/webp") == "webp"


def test_content_type_gif():
    from app.scraper import _content_type_to_ext
    assert _content_type_to_ext("image/gif") == "gif"


def test_content_type_jpeg_returns_jpg():
    from app.scraper import _content_type_to_ext
    assert _content_type_to_ext("image/jpeg") == "jpg"


def test_content_type_unknown_defaults_to_jpg():
    from app.scraper import _content_type_to_ext
    assert _content_type_to_ext("application/octet-stream") == "jpg"


def test_content_type_case_insensitive():
    from app.scraper import _content_type_to_ext
    assert _content_type_to_ext("IMAGE/PNG") == "png"


# ── _icon_path ────────────────────────────────────────────────────────────────

def test_icon_path_basic():
    from app.scraper import _icon_path
    from app.config import ICONS_DIR
    result = _icon_path("some-slug", "jpg")
    assert result == os.path.join(ICONS_DIR, "some-slug.jpg")


def test_icon_path_replaces_slash_with_underscore():
    from app.scraper import _icon_path
    from app.config import ICONS_DIR
    result = _icon_path("some/slug", "png")
    assert result == os.path.join(ICONS_DIR, "some_slug.png")


def test_icon_path_truncates_long_slug():
    from app.scraper import _icon_path
    long_slug = "a" * 100
    result = _icon_path(long_slug, "jpg")
    basename = os.path.basename(result)
    assert basename == "a" * 80 + ".jpg"


# ── _existing_icon_ext ────────────────────────────────────────────────────────

def test_existing_icon_ext_not_found(tmp_path, monkeypatch):
    from app.scraper import _existing_icon_ext
    import app.scraper as scraper_module
    monkeypatch.setattr(scraper_module, "ICONS_DIR", str(tmp_path))
    assert _existing_icon_ext("some-game") == ""


def test_existing_icon_ext_found_jpg(tmp_path, monkeypatch):
    from app.scraper import _existing_icon_ext
    import app.scraper as scraper_module
    monkeypatch.setattr(scraper_module, "ICONS_DIR", str(tmp_path))
    (tmp_path / "some-game.jpg").write_bytes(b"")
    assert _existing_icon_ext("some-game") == "jpg"


def test_existing_icon_ext_found_png(tmp_path, monkeypatch):
    from app.scraper import _existing_icon_ext
    import app.scraper as scraper_module
    monkeypatch.setattr(scraper_module, "ICONS_DIR", str(tmp_path))
    (tmp_path / "other-game.png").write_bytes(b"")
    assert _existing_icon_ext("other-game") == "png"


def test_existing_icon_ext_no_icons_dir(monkeypatch):
    from app.scraper import _existing_icon_ext
    import app.scraper as scraper_module
    monkeypatch.setattr(scraper_module, "ICONS_DIR", "/nonexistent/path/icons")
    assert _existing_icon_ext("game") == ""


# ── extract_games ──────────────────────────────────────────────────────────────

_BASE_HTML = """
<html><body>
<div class="view-list">
  <div class="col">
    <div class="d-flex flex-column flex-grow-1">
      <a class="main-link" href="/items/game-slug">Game Title</a>
      <div>October 1, 2026</div>
    </div>
    <img src="https://cdn.example.com/cover.jpg">
    <s class="text-muted">$9.99</s>
    <strong>$4.99</strong>
    <span class="badge-danger">-50%</span>
  </div>
</div>
</body></html>
"""


def test_extract_games_basic_fields():
    from app.scraper import extract_games
    games = extract_games(_BASE_HTML)
    assert len(games) == 1
    g = games[0]
    assert g["name"] == "Game Title"
    assert g["slug"] == "game-slug"
    assert g["original"] == "$9.99"
    assert g["current"] == "$4.99"
    assert g["discount"] == "-50%"
    assert g["image_url"] == "https://cdn.example.com/cover.jpg"
    assert g["release_date"] == "2026-10-01"


def test_extract_games_no_view_list_raises():
    from app.scraper import extract_games
    with pytest.raises(RuntimeError, match=r"\.view-list"):
        extract_games("<html><body><div>Nothing here</div></body></html>")


def test_extract_games_empty_list():
    from app.scraper import extract_games
    html = "<html><body><div class='view-list'></div></body></html>"
    assert extract_games(html) == []


def test_extract_games_unavailable_price():
    from app.scraper import extract_games
    html = """
    <html><body>
    <div class="view-list">
      <div class="col">
        <div class="d-flex flex-column flex-grow-1">
          <a class="main-link" href="/items/no-price">No Price Game</a>
        </div>
      </div>
    </div>
    </body></html>
    """
    games = extract_games(html)
    assert len(games) == 1
    assert games[0]["current"] == "Unavailable"
    assert games[0]["discount"] == ""
    assert games[0]["original"] == ""


def test_extract_games_skips_non_items_href():
    from app.scraper import extract_games
    html = """
    <html><body>
    <div class="view-list">
      <div class="col">
        <div class="d-flex flex-column flex-grow-1">
          <a class="main-link" href="/other/link">Skip Me</a>
        </div>
      </div>
    </div>
    </body></html>
    """
    assert extract_games(html) == []


def test_extract_games_with_sale_end():
    from app.scraper import extract_games
    html = """
    <html><body>
    <div class="view-list">
      <div class="col">
        <div class="d-flex flex-column flex-grow-1">
          <a class="main-link" href="/items/sale-game">Sale Game</a>
          <div>October 1, 2026 Sale ends in 2 days</div>
        </div>
        <strong>$4.99</strong>
      </div>
    </div>
    </body></html>
    """
    games = extract_games(html)
    assert len(games) == 1
    assert games[0]["release_date"] == "2026-10-01"
    assert games[0]["sale_end"]  # non-empty since "in 2 days" is relative to now


def test_extract_games_multiple_items():
    from app.scraper import extract_games
    html = """
    <html><body>
    <div class="view-list">
      <div class="col">
        <div class="d-flex flex-column flex-grow-1">
          <a class="main-link" href="/items/game-a">Game A</a>
        </div>
        <strong>$1.99</strong>
      </div>
      <div class="col">
        <div class="d-flex flex-column flex-grow-1">
          <a class="main-link" href="/items/game-b">Game B</a>
        </div>
        <strong>$2.99</strong>
      </div>
    </div>
    </body></html>
    """
    games = extract_games(html)
    assert len(games) == 2
    assert games[0]["slug"] == "game-a"
    assert games[1]["slug"] == "game-b"


def test_extract_games_image_data_src_fallback():
    from app.scraper import extract_games
    html = """
    <html><body>
    <div class="view-list">
      <div class="col">
        <div class="d-flex flex-column flex-grow-1">
          <a class="main-link" href="/items/lazy-img">Lazy Game</a>
        </div>
        <img data-src="https://cdn.example.com/lazy.jpg">
        <strong>$4.99</strong>
      </div>
    </div>
    </body></html>
    """
    games = extract_games(html)
    assert games[0]["image_url"] == "https://cdn.example.com/lazy.jpg"


# ── merge_prices (extended) ───────────────────────────────────────────────────

def test_merge_prices_sale_ends_per_locale():
    from app.scraper import merge_prices
    br_games = [{"name": "Game A", "slug": "game-a", "original": "R$ 100", "current": "R$ 50",
                 "discount": "-50%", "release_date": "2024-01-01",
                 "sale_end": "2026-06-15T23:59:59Z", "image_url": ""}]
    us_games = [{"name": "Game A", "slug": "game-a", "original": "$9.99", "current": "$4.99",
                 "discount": "-50%", "release_date": "2024-01-01",
                 "sale_end": "2026-06-16T23:59:59Z", "image_url": ""}]
    result = merge_prices({"br": br_games, "us": us_games}, reference_locale="br")

    assert result[0]["sale_ends"]["br"] == "2026-06-15T23:59:59Z"
    assert result[0]["sale_ends"]["us"] == "2026-06-16T23:59:59Z"
    assert result[0]["sale_end"] == "2026-06-15T23:59:59Z"  # reference locale


def test_merge_prices_reference_locale_slug_wins():
    from app.scraper import merge_prices
    br_games = [{"name": "Game", "slug": "game-br", "original": "R$ 100", "current": "R$ 50",
                 "discount": "", "release_date": "2024-01-01", "sale_end": "", "image_url": "img-br"}]
    us_games = [{"name": "Game", "slug": "game-us", "original": "$9", "current": "$4",
                 "discount": "", "release_date": "2024-01-01", "sale_end": "", "image_url": "img-us"}]
    result = merge_prices({"br": br_games, "us": us_games}, reference_locale="br")

    assert result[0]["slug"] == "game-br"
    assert result[0]["image_url"] == "img-br"


def test_merge_prices_preserves_order_reference_first():
    from app.scraper import merge_prices
    br_games = [
        {"name": "B", "slug": "b", "original": "", "current": "", "discount": "",
         "release_date": "", "sale_end": "", "image_url": ""},
        {"name": "A", "slug": "a", "original": "", "current": "", "discount": "",
         "release_date": "", "sale_end": "", "image_url": ""},
    ]
    result = merge_prices({"br": br_games}, reference_locale="br")
    assert result[0]["name"] == "B"
    assert result[1]["name"] == "A"


def test_merge_prices_game_only_in_non_reference():
    from app.scraper import merge_prices
    br_games = []
    us_games = [{"name": "US Only", "slug": "us-only", "original": "$9", "current": "$4",
                 "discount": "", "release_date": "", "sale_end": "", "image_url": ""}]
    result = merge_prices({"br": br_games, "us": us_games}, reference_locale="br")

    assert len(result) == 1
    assert result[0]["name"] == "US Only"
    assert "us" in result[0]["prices"]
    assert "br" not in result[0]["prices"]


# ── _parse_eshop_analytics ────────────────────────────────────────────────────

def test_parse_eshop_analytics_us_no_discount():
    from app.scraper import _parse_eshop_analytics
    html = """<html><body>
    <script>outAnalytics['eshop:70010000018692'] = {"currency":"USD","value":999,"items":[{"item_id":"test","item_name":"Test","affiliation":"eshop","discount":0,"index":3,"item_category":"switch","item_variant":"digital","price":999,"quantity":1}]}</script>
    </body></html>"""
    assert _parse_eshop_analytics(html) == {"value": 999, "discount": 0}


def test_parse_eshop_analytics_br_locale_key():
    from app.scraper import _parse_eshop_analytics
    html = """<html><body>
    <script>outAnalytics['eshop_br:70010000018692'] = {"currency":"BRL","value":5490,"items":[{"item_id":"test","item_name":"Test","affiliation":"eshop_br","discount":0,"index":0,"item_category":"switch","item_variant":"digital","price":5490,"quantity":1}]}</script>
    </body></html>"""
    assert _parse_eshop_analytics(html) == {"value": 5490, "discount": 0}


def test_parse_eshop_analytics_with_discount():
    from app.scraper import _parse_eshop_analytics
    html = """<html><body>
    <script>outAnalytics['eshop:70010000099999'] = {"currency":"USD","value":999,"items":[{"item_id":"test","item_name":"Test","affiliation":"eshop","discount":1000,"index":0,"item_category":"switch","item_variant":"digital","price":999,"quantity":1}]}</script>
    </body></html>"""
    assert _parse_eshop_analytics(html) == {"value": 999, "discount": 1000}


def test_parse_eshop_analytics_no_eshop_entry_returns_empty():
    from app.scraper import _parse_eshop_analytics
    html = """<html><body>
    <script>outAnalytics['amazon:B07QZR4PFT'] = {"currency":"USD","value":949,"items":[{"discount":50}]}</script>
    </body></html>"""
    assert _parse_eshop_analytics(html) == {}


def test_parse_eshop_analytics_no_scripts_returns_empty():
    from app.scraper import _parse_eshop_analytics
    assert _parse_eshop_analytics("<html><body><p>Nothing</p></body></html>") == {}


# ── _format_eshop_value ───────────────────────────────────────────────────────

def test_format_eshop_value_usd():
    from app.scraper import _format_eshop_value
    assert _format_eshop_value(999, "us") == "$9.99"


def test_format_eshop_value_brl():
    from app.scraper import _format_eshop_value
    assert _format_eshop_value(5490, "br") == "R$ 54,90"


def test_format_eshop_value_jpy_no_decimal():
    from app.scraper import _format_eshop_value
    assert _format_eshop_value(500, "jp") == "¥500"


def test_format_eshop_value_eur():
    from app.scraper import _format_eshop_value
    assert _format_eshop_value(1499, "de") == "€14.99"


# ── _fetch_eshop_prices ───────────────────────────────────────────────────────

def test_fetch_eshop_prices_replaces_amazon_price_with_eshop(monkeypatch):
    from app.scraper import _fetch_eshop_prices
    import requests

    _ESHOP_HTML = """<html><body>
    <script>outAnalytics['eshop:123'] = {"currency":"USD","value":999,"items":[{"item_id":"boxboy","item_name":"BOXBOY","affiliation":"eshop","discount":0,"index":3,"item_category":"switch","item_variant":"digital","price":999,"quantity":1}]}</script>
    </body></html>"""

    class _FakeResponse:
        text = _ESHOP_HTML
        def raise_for_status(self): pass

    class _FakeSession:
        cookies = []
        def get(self, url, **kwargs): return _FakeResponse()
        def cookies_set(self, *a): pass

    monkeypatch.setattr("app.scraper.requests.Session", lambda: _FakeSession())

    games = [{"slug": "boxboy-and-boxgirl", "current": "$9.49", "original": "$9.99",
              "discount": "-5%", "sale_end": "2026-07-01T00:00:00Z"}]
    _fetch_eshop_prices(games, "us", _FakeSession())

    assert games[0]["current"] == "$9.99"
    assert games[0]["original"] == ""
    assert games[0]["discount"] == ""
    assert games[0]["sale_end"] == ""


def test_fetch_eshop_prices_includes_unavailable_for_platforms(monkeypatch):
    from app.scraper import _fetch_eshop_prices
    import requests

    fetch_called = []
    class _FakeSession:
        cookies = []
        def get(self, url, **kwargs):
            fetch_called.append(url)
            class R:
                text = """<html><body>
                <ul class="details"><li class="list-group-item">
                <strong>Platforms:</strong>Nintendo Switch 2</li></ul>
                </body></html>"""
                def raise_for_status(self): pass
            return R()

    monkeypatch.setattr("app.scraper.requests.Session", lambda: _FakeSession())

    games = [{"slug": "upcoming-game", "current": "Unavailable", "original": "",
              "discount": "", "sale_end": ""}]
    _fetch_eshop_prices(games, "us", _FakeSession())

    assert fetch_called == ["https://www.dekudeals.com/items/upcoming-game"]
    assert games[0]["switch2"] is True
    assert games[0]["switch1"] is False


def test_fetch_eshop_prices_retries_on_429(monkeypatch):
    from app.scraper import _fetch_eshop_prices
    import requests as req

    _ESHOP_HTML = """<html><body>
    <script>outAnalytics['eshop:1'] = {"currency":"USD","value":999,"items":[{"discount":0}]}</script>
    </body></html>"""

    call_count = [0]
    sleeps = []

    class _FakeResponse429:
        status_code = 429
        def raise_for_status(self):
            err = req.exceptions.HTTPError()
            err.response = self
            raise err

    class _FakeResponseOk:
        text = _ESHOP_HTML
        def raise_for_status(self): pass

    class _FakeSession:
        cookies = []
        def get(self, url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _FakeResponse429()
            return _FakeResponseOk()

    monkeypatch.setattr("app.scraper.requests.Session", lambda: _FakeSession())
    monkeypatch.setattr("app.scraper.time.sleep", lambda d: sleeps.append(d))

    games = [{"slug": "some-game", "current": "$9.99", "original": "", "discount": "", "sale_end": ""}]
    _fetch_eshop_prices(games, "us", _FakeSession())

    assert call_count[0] == 2
    assert games[0]["current"] == "$9.99"
    assert 1.0 in sleeps


def test_fetch_eshop_prices_delay_increases_on_429(monkeypatch):
    from app.scraper import _fetch_eshop_prices
    import requests as req

    _ESHOP_HTML = """<html><body>
    <script>outAnalytics['eshop:1'] = {"currency":"USD","value":499,"items":[{"discount":0}]}</script>
    </body></html>"""

    responses = iter([429, 429, 200])
    sleeps = []

    class _FakeSession:
        cookies = []
        def get(self, url, **kwargs):
            code = next(responses)
            if code == 429:
                class R429:
                    status_code = 429
                    def raise_for_status(self):
                        err = req.exceptions.HTTPError()
                        err.response = self
                        raise err
                return R429()
            class ROk:
                text = _ESHOP_HTML
                def raise_for_status(self): pass
            return ROk()

    monkeypatch.setattr("app.scraper.requests.Session", lambda: _FakeSession())
    monkeypatch.setattr("app.scraper.time.sleep", lambda d: sleeps.append(d))

    games = [{"slug": "rate-limited", "current": "$4.99", "original": "", "discount": "", "sale_end": ""}]
    _fetch_eshop_prices(games, "us", _FakeSession())

    # sleeps: [delay_attempt1, 1.0_backoff, delay_attempt2, 1.0_backoff, delay_attempt3]
    one_sec_sleeps = [s for s in sleeps if s == 1.0]
    delay_sleeps = [s for s in sleeps if s != 1.0]
    assert len(one_sec_sleeps) == 2
    assert len(delay_sleeps) == 3
    assert delay_sleeps[0] < delay_sleeps[1] < delay_sleeps[2]
    assert round(delay_sleeps[1] - delay_sleeps[0], 3) == 0.01
    assert round(delay_sleeps[2] - delay_sleeps[1], 3) == 0.01


# ---- _parse_platforms ----

def _details_html(platforms_inner: str) -> str:
    return (
        '<ul class="details list-group list-group-flush">'
        '<li class="list-group-item"><strong>MSRP:</strong> R$59,99</li>'
        '<li class="list-group-item"><strong>Release date:</strong>'
        '<ul><li><strong>PS4, PS5, Switch</strong><br/>October 30, 2025</li></ul></li>'
        f'{platforms_inner}'
        '</ul>'
    )


def test_parse_platforms_switch1_only():
    from app.scraper import _parse_platforms
    html = _details_html(
        '<li class="list-group-item"><strong>Platforms:</strong> '
        '<a href="?platform=all">Nintendo Switch, PlayStation 5, Steam</a></li>'
    )
    assert _parse_platforms(html) == {"switch1": True, "switch2": False}


def test_parse_platforms_switch2_only():
    from app.scraper import _parse_platforms
    html = _details_html(
        '<li class="list-group-item"><strong>Platforms:</strong> '
        '<a href="?platform=all">Nintendo Switch 2, PlayStation 5, Steam</a></li>'
    )
    assert _parse_platforms(html) == {"switch1": False, "switch2": True}


def test_parse_platforms_both():
    from app.scraper import _parse_platforms
    html = _details_html(
        '<li class="list-group-item"><strong>Platforms:</strong> '
        '<a href="?platform=all">Nintendo Switch, Nintendo Switch 2, Steam</a></li>'
    )
    assert _parse_platforms(html) == {"switch1": True, "switch2": True}


def test_parse_platforms_neither():
    from app.scraper import _parse_platforms
    html = _details_html(
        '<li class="list-group-item"><strong>Platforms:</strong> '
        '<a href="?platform=all">PlayStation 5, Xbox Series X|S, Steam</a></li>'
    )
    assert _parse_platforms(html) == {"switch1": False, "switch2": False}


def test_parse_platforms_missing_line():
    from app.scraper import _parse_platforms
    html = _details_html("")
    assert _parse_platforms(html) == {"switch1": False, "switch2": False}


def test_parse_platforms_no_details():
    from app.scraper import _parse_platforms
    assert _parse_platforms("<html><body>nope</body></html>") == {"switch1": False, "switch2": False}


def test_refresh_performance_saves_on_success(temp_db, monkeypatch):
    import app.scraper as scraper

    monkeypatch.setattr(scraper, "fetch_performance_sheet",
                        lambda **kw: {"arms": {"fps": 60, "label": "60fps", "patch_type": ""}})
    saved = {}
    monkeypatch.setattr(scraper, "save_performance_cache",
                        lambda rows, db: saved.update(rows))
    scraper._refresh_performance(temp_db, user_agent="x")
    assert saved["arms"]["fps"] == 60


def test_refresh_performance_swallows_errors(temp_db, monkeypatch):
    import app.scraper as scraper

    def boom(**kw):
        raise RuntimeError("network down")

    called = {"saved": False}
    monkeypatch.setattr(scraper, "fetch_performance_sheet", boom)
    monkeypatch.setattr(scraper, "save_performance_cache",
                        lambda rows, db: called.update(saved=True))
    # Must not raise, and must not save (so previous cache is preserved).
    scraper._refresh_performance(temp_db, user_agent="x")
    assert called["saved"] is False


def test_refresh_performance_skips_empty(temp_db, monkeypatch):
    import app.scraper as scraper

    monkeypatch.setattr(scraper, "fetch_performance_sheet", lambda **kw: {})
    called = {"saved": False}
    monkeypatch.setattr(scraper, "save_performance_cache",
                        lambda rows, db: called.update(saved=True))
    scraper._refresh_performance(temp_db, user_agent="x")
    assert called["saved"] is False
