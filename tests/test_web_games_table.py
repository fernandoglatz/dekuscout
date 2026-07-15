import json

import app.config as config_module
from app.db import save_games_cache, set_config


def _setup_games(db_path):
    set_config("WISHLIST_URL", "https://www.dekudeals.com/wishlist/test", db_path)
    set_config("SELECTED_CURRENCIES", json.dumps(["br"]), db_path)
    games = [
        {"name": "S1 Only", "slug": "s1", "switch1": True, "switch2": False, "prices": {}},
        {"name": "S2 Only", "slug": "s2", "switch1": False, "switch2": True, "prices": {}},
        {"name": "Both", "slug": "both", "switch1": True, "switch2": True, "prices": {}},
        {"name": "Neither", "slug": "none", "switch1": False, "switch2": False, "prices": {}},
    ]
    save_games_cache(games, db_path)


def test_games_table_no_filter_returns_all(client):
    _setup_games(config_module.DB_FILE)
    html = client.get("/api/games-table").get_data(as_text=True)
    for slug in ("s1", "s2", "both", "none"):
        assert f"/items/{slug}" in html


def test_games_table_switch1_filter(client):
    _setup_games(config_module.DB_FILE)
    html = client.get("/api/games-table?platform=switch1").get_data(as_text=True)
    assert "/items/s1" in html
    assert "/items/both" in html
    assert "/items/s2" not in html
    assert "/items/none" not in html


def test_games_table_switch2_filter(client):
    _setup_games(config_module.DB_FILE)
    html = client.get("/api/games-table?platform=switch2").get_data(as_text=True)
    assert "/items/s2" in html
    assert "/items/both" in html
    assert "/items/s1" not in html
    assert "/items/none" not in html


def test_games_table_both_filters_intersection(client):
    _setup_games(config_module.DB_FILE)
    html = client.get("/api/games-table?platform=switch1,switch2").get_data(as_text=True)
    assert "/items/both" in html
    assert "/items/s1" not in html
    assert "/items/s2" not in html
    assert "/items/none" not in html


def test_games_table_ignores_unknown_platform(client):
    _setup_games(config_module.DB_FILE)
    html = client.get("/api/games-table?platform=bogus").get_data(as_text=True)
    for slug in ("s1", "s2", "both", "none"):
        assert f"/items/{slug}" in html


def test_games_table_no_cache_returns_empty(client):
    set_config("WISHLIST_URL", "https://www.dekudeals.com/wishlist/test", config_module.DB_FILE)
    set_config("SELECTED_CURRENCIES", json.dumps(["br"]), config_module.DB_FILE)
    resp = client.get("/api/games-table")
    assert resp.status_code == 200
    assert "/items/" not in resp.get_data(as_text=True)


def _setup_mixed(db_path):
    set_config("WISHLIST_URL", "https://www.dekudeals.com/wishlist/test", db_path)
    set_config("SELECTED_CURRENCIES", json.dumps(["br"]), db_path)
    games = [
        {"name": "Discounted", "slug": "disc",
         "prices": {"br": {"current": "R$ 10", "original": "R$ 20", "discount": "-50%"}}},
        {"name": "Full Price", "slug": "full",
         "prices": {"br": {"current": "R$ 30"}}},
        {"name": "Gone", "slug": "gone",
         "prices": {"br": {"current": "Unavailable"}}},
    ]
    save_games_cache(games, db_path)


def test_games_table_sale_filter(client):
    _setup_mixed(config_module.DB_FILE)
    html = client.get("/api/games-table?sale=1").get_data(as_text=True)
    assert "/items/disc" in html
    assert "/items/full" not in html
    assert "/items/gone" not in html


def test_games_table_available_filter(client):
    _setup_mixed(config_module.DB_FILE)
    html = client.get("/api/games-table?available=1").get_data(as_text=True)
    assert "/items/disc" in html
    assert "/items/full" in html
    assert "/items/gone" not in html


def test_games_table_search_filter(client):
    _setup_mixed(config_module.DB_FILE)
    html = client.get("/api/games-table?q=full").get_data(as_text=True)
    assert "/items/full" in html
    assert "/items/disc" not in html
    assert "/items/gone" not in html


def test_games_table_combined_filters_are_anded(client):
    _setup_mixed(config_module.DB_FILE)
    # available AND search "disc" -> only the discounted game
    html = client.get("/api/games-table?available=1&q=disc").get_data(as_text=True)
    assert "/items/disc" in html
    assert "/items/full" not in html
    assert "/items/gone" not in html


def test_games_table_bestbuy_filter(client, monkeypatch):
    import app.web as web_module

    monkeypatch.setattr(web_module, "fetch_rate", lambda locale, ref: 1.0)
    set_config("WISHLIST_URL", "https://www.dekudeals.com/wishlist/test", config_module.DB_FILE)
    set_config("SELECTED_CURRENCIES", json.dumps(["br", "us"]), config_module.DB_FILE)
    save_games_cache([
        {"name": "Cheaper US", "slug": "us-win",
         "prices": {"br": {"current": "R$ 50"}, "us": {"current": "$ 10"}}},
        {"name": "Cheaper BR", "slug": "br-win",
         "prices": {"br": {"current": "R$ 5"}, "us": {"current": "$ 40"}}},
    ], config_module.DB_FILE)

    html = client.get("/api/games-table?bestbuy=us").get_data(as_text=True)
    assert "/items/us-win" in html
    assert "/items/br-win" not in html


def test_annotate_performance_base_and_sw2(temp_db):
    from app.db import save_performance_cache
    from app.web import _annotate_performance

    save_performance_cache({
        "arms": {"fps": 60, "label": "60fps", "patch_type": "Free Update"},
        "plain game": {"fps": 30, "label": "30fps", "patch_type": "Unpatched"},
    }, temp_db)

    games = [
        {"name": "ARMS", "switch2": False},          # SW2 via patch_type
        {"name": "Plain Game", "switch2": False},    # base fps, no SW2
        {"name": "Sw2 Flagged", "switch2": True},    # not in sheet -> dash
    ]
    _annotate_performance(games, temp_db)

    assert games[0]["perf_label"] == "60fps" and games[0]["perf_sw2"] is True
    assert games[0]["perf_sort"] == 60
    assert games[1]["perf_label"] == "30fps" and games[1]["perf_sw2"] is False
    assert games[2]["perf_label"] == "—" and games[2]["perf_sort"] == 0
    assert games[2]["perf_sw2"] is False


def test_annotate_performance_sw2_flag_from_dekudeals(temp_db):
    from app.db import save_performance_cache
    from app.web import _annotate_performance

    save_performance_cache(
        {"g": {"fps": 30, "label": "30fps", "patch_type": "Unpatched"}}, temp_db)
    games = [{"name": "G", "switch2": True}]  # DekuDeals says SW2 version exists
    _annotate_performance(games, temp_db)
    assert games[0]["perf_sw2"] is True
