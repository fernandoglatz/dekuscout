from app.db import save_performance_cache, load_performance_cache


def test_performance_cache_round_trip(temp_db):
    rows = {
        "arms": {"fps": 60, "label": "60fps", "patch_type": "Free Update"},
        "some game": {"fps": 30, "label": "30fps", "patch_type": "Unpatched"},
    }
    save_performance_cache(rows, temp_db)
    loaded = load_performance_cache(temp_db)
    assert loaded["arms"] == {"fps": 60, "label": "60fps", "patch_type": "Free Update"}
    assert loaded["some game"]["fps"] == 30


def test_save_replaces_previous_rows(temp_db):
    save_performance_cache({"a": {"fps": 30, "label": "30fps", "patch_type": ""}}, temp_db)
    save_performance_cache({"b": {"fps": 60, "label": "60fps", "patch_type": ""}}, temp_db)
    loaded = load_performance_cache(temp_db)
    assert "a" not in loaded and loaded["b"]["fps"] == 60


def test_load_missing_table_returns_empty(tmp_path):
    # A fresh db with no migrations run has no performance_cache table.
    assert load_performance_cache(str(tmp_path / "empty.db")) == {}
