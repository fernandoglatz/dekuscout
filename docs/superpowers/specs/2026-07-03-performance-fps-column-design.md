# Performance (FPS) column

**Date:** 2026-07-03

## Goal

Show, per game in the list, the framerate it runs at (30 / 60 / more). When a
Nintendo Switch 2 version of the game exists, show the Switch 2 performance
instead of the base framerate. A single "smart" column that picks the right value.

DekuDeals publishes **no** performance data on its pages (only price, platforms,
release date, download size — verified). The data must come from an external
community source.

## Data source

The only viable machine-readable source is the community
**[Switch 2 FPS/Resolution List](https://docs.google.com/spreadsheets/d/1sOYZRiOuD9Cnr-e_hlzhRuxuEq5X5Ptwq4yCfwxyfFk)**
Google Sheet, pulled as CSV through the public `gviz` endpoint (no auth, not
Cloudflare-gated):

```
https://docs.google.com/spreadsheets/d/<ID>/gviz/tq?tqx=out:csv&gid=<GID>
```

Rejected alternatives: `switchgamedb.com` (parked 16-byte placeholder), Famiboards
"Running in the 60s" thread (Cloudflare-protected forum HTML), GitHub 60fps *cheat*
databases (homebrew mods, not native performance).

### Sheet shape (observed)

Column-positional, one game per row (the main/first tab):

| Idx | Meaning |
|-----|---------|
| 0 | Game name |
| 1 | Publisher |
| 2 | Version |
| 3 | Patch type — `Unpatched` / `Free Update` / `Switch 2 Edition` |
| 4 | Framerate (docked) — prose, e.g. `Capped 60FPS, increased consistency` |
| 5 | Resolution (docked) |
| 7 | Framerate (handheld) |
| 8 | Resolution (handheld) |

The first row is a merged news/instructions blob, not clean headers — parsing is
column-positional and skips rows whose name cell is empty or clearly non-game.

### Honest caveats

- The sheet measures behavior **on Switch 2 hardware** (backward-compat or enhanced).
  For most 30/60-capped titles that equals native framerate; `Uncapped` flags a
  genuine Switch 2 boost. So "base framerate" here really means "framerate on Switch 2
  hardware" — accepted, because it is all the source provides.
- Framerate is prose. We regex a number out of it:
  - contains digits + `FPS` → that integer (`Capped 60FPS` → `60`)
  - contains `Uncapped` → sentinel `Uncapped`
  - `Unchanged / Not Noticeable`, `N/A`, empty → **no number** → blank
- Coverage is therefore lower than the ~1,100 raw rows, and matching to DekuDeals is
  by name only (no shared ID). Unmatched games show **blank**.

## Design

### 1. Parsing & matching (`app/performance.py`, new module)

- `normalize_name(name) -> str` — lowercase, strip `™`/`®`, drop punctuation,
  collapse whitespace. Used on both sides for a conservative **exact-normalized**
  match (favor missing data over wrong data).
- `extract_fps(text) -> tuple[int, str] | None` — returns `(sort_value, label)`:
  - `Capped 60FPS...` → `(60, "60fps")`
  - `Uncapped...` → `(999, "Uncapped")`
  - `Unchanged / Not Noticeable` / `N/A` / empty → `None`
- `parse_performance_csv(text) -> dict[norm_name, row]` where each `row` holds
  `{fps_docked, fps_handheld, patch_type}`. Prefer docked fps, fall back to handheld.
- `fetch_performance_sheet(...) -> dict` — GET the gviz CSV (short timeout), parse,
  return the dict. Raises on network/HTTP error so the caller can decide.

Source URL is built from env-overridable config constants (see §5).

### 2. Storage & refresh — new `performance_cache` table

Follows the `price_history_cache` pattern (standalone cache table, **no**
`games_cache` migration). Migration `V006__create_performance_cache_table.sql`:

```sql
CREATE TABLE performance_cache (
    norm_name  TEXT    PRIMARY KEY,
    fps        INTEGER NOT NULL DEFAULT 0,   -- sort value; 0 = unknown, 999 = uncapped
    label      TEXT    NOT NULL DEFAULT '',  -- display, e.g. "60fps" / "Uncapped"
    patch_type TEXT    NOT NULL DEFAULT '',  -- Unpatched / Free Update / Switch 2 Edition
    fetched_at REAL    NOT NULL
);
```

`app/db.py` gains `save_performance_cache(rows, db_path)` and
`load_performance_cache(db_path) -> dict[norm_name, row]`.

**Refresh** happens inside the existing background refresh `fetch_all_games`
([app/scraper.py](../../../app/scraper.py)), reported as a new progress step
`"performance"`. It is **best-effort and outage-safe**: fetch+parse the sheet inside
a `try/except`; only replace `performance_cache` on success. A failed fetch is logged
and the previous data is kept — the column never blanks on a transient sheet outage.
This does not add per-game HTTP requests (one CSV download per wishlist refresh).

### 3. Render-time join (`app/web.py`)

New `_annotate_performance(games, db_path)`:

1. Load `performance_cache` once into a dict.
2. For each game, look up by `normalize_name(g["name"])`.
3. Compute the smart value:
   - not found → `perf_label=""`, `perf_sort=0`, `perf_sw2=False`.
   - found → `perf_label`/`perf_sort` from the row's fps.
     `has_sw2 = g["switch2"] or patch_type in {"Switch 2 Edition", "Free Update"}`.
     Set `perf_sw2 = has_sw2` so the template can render the `S2` marker.

Called in both `index()` and `api_games_table()` (the toolbar-filter endpoint) so the
column is populated on first paint and after every filter/sort round-trip. This is a
cheap in-memory dict lookup over ~200 games per request.

### 4. Frontend (`app/templates/`)

- **Header** ([index.html](../../../app/templates/index.html)): new
  `<th data-type="num" data-i18n="th.performance">` before Release Date.
  Add colKey `'perf'` to `colKeyFor()` so the column participates in sort,
  drag-reorder, and localStorage persistence like every other column.
- **Numeric sort**: confirm the existing sort handles `data-type="num"`; if only
  `str/price/date` are handled, add a numeric branch (reuse the price number parser).
- **Row** ([games_rows.html](../../../app/templates/components/games_rows.html)):
  new `<td class="perf" data-sort="{{ g.perf_sort }}">{{ g.perf_label }}<span
  class="perf-s2">S2</span></td>`, the badge shown only when `g.perf_sw2`. Blank cell
  when `perf_label` is empty.
- **Mobile**: the layout has no separate card markup — `.table-wrap` scrolls
  horizontally (`overflow-x: auto`), so the new `<td>` participates automatically.
  No mobile-specific work beyond a visual check.
- **i18n**: add `th.performance` to all four locale files (en, es, pt, ja).

No FPS filter button — the request is only to *see* the value in the list. YAGNI.

### 5. Config (`app/config.py`)

Env-overridable constants so the source is easy to repoint if the community sheet
moves or restructures:

```python
PERFORMANCE_SHEET_ID  = os.environ.get("PERFORMANCE_SHEET_ID",  "1sOYZRiOuD9Cnr-e_hlzhRuxuEq5X5Ptwq4yCfwxyfFk")
PERFORMANCE_SHEET_GID = os.environ.get("PERFORMANCE_SHEET_GID", "0")   # confirmed against the live sheet
PERFORMANCE_CACHE_TTL = 24 * 60 * 60
```

## Testing

- `normalize_name`: trademark symbols, punctuation, casing, spacing.
- `extract_fps`: capped 30/60, uncapped, `N/A`, `Unchanged / Not Noticeable`, empty.
- `parse_performance_csv` against a small **captured CSV fixture** (a handful of real
  rows incl. the messy first row) — so a sheet restructure fails loudly.
- Smart-column decision: base vs `S2` for switch2-flagged / patched vs plain rows;
  unmatched → blank.
- DB round-trip for `performance_cache` (save → load).
- `_annotate_performance`: game matched, unmatched, and cache-empty cases.

## Out of scope

- FPS filter button.
- Backfilling performance for unavailable games (they may lack a `switch2` flag; the
  smart column still works from the sheet where matched).
- Reconciling multiple community sources / fuzzy matching beyond exact-normalized.
