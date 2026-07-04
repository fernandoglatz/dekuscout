import sqlite3


def test_run_migrations_creates_all_tables(tmp_path):
    from app.migrations import run_migrations

    db_path = str(tmp_path / "test.db")
    run_migrations(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_yoyo%'"
            )
        }
    assert tables == {"cookies", "games_cache", "price_history_cache", "config", "performance_cache", "yoyo_lock", "sqlite_sequence"}


def test_run_migrations_idempotent(tmp_path):
    from app.migrations import run_migrations

    db_path = str(tmp_path / "test.db")
    run_migrations(db_path)
    run_migrations(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_yoyo%'"
            )
        }
    assert "cookies" in tables
    assert "games_cache" in tables
    assert "price_history_cache" in tables
    assert "config" in tables


def test_run_migrations_creates_data_directory(tmp_path):
    from app.migrations import run_migrations

    db_path = str(tmp_path / "nested" / "dir" / "test.db")
    run_migrations(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_yoyo%'"
        )}
    assert "config" in tables
