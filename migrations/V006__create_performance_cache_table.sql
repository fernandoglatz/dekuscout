-- depends: V005__add_platform_flags_to_games_cache

CREATE TABLE performance_cache (
    norm_name  TEXT    PRIMARY KEY,
    fps        INTEGER NOT NULL DEFAULT 0,
    label      TEXT    NOT NULL DEFAULT '',
    patch_type TEXT    NOT NULL DEFAULT '',
    fetched_at REAL    NOT NULL
);
