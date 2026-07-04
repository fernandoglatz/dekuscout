# Performance (FPS) Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-game "FPS" column showing the framerate a game runs at (30 / 60 / more), preferring Switch 2 performance when a Switch 2 version exists.

**Architecture:** A new `app/performance.py` module fetches a public community Google Sheet (as CSV via the `gviz` endpoint) and parses it into `{normalized_name -> {fps, label, patch_type}}`. Parsed data is stored in a standalone `performance_cache` table (mirroring `price_history_cache`), refreshed best-effort inside the existing `fetch_all_games` background refresh. At render time `web.py` joins games to the cache by normalized name and picks a "smart" value (base fps, or Switch 2 fps with an `S2` badge). The frontend gets one new sortable/reorderable column.

**Tech Stack:** Python 3.10, Flask, SQLite, yoyo migrations, requests, BeautifulSoup (existing); Jinja templates + vanilla JS frontend; pytest.

## Global Constraints

- **Do NOT run `git commit` or `git push`.** The user commits manually (global rule). Every "Commit" step below means: stage the listed files with `git add`, then **stop and hand control to the user**. Do not create the commit yourself.
- Data source is env-overridable via `PERFORMANCE_SHEET_ID` (default `1sOYZRiOuD9Cnr-e_hlzhRuxuEq5X5Ptwq4yCfwxyfFk`) and `PERFORMANCE_SHEET_GID` (default `0`, confirmed against the live sheet).
- Matching is **exact-normalized name** only — favor blank over a wrong match.
- Unmatched games and games with no numeric fps render as a **blank** cell.
- i18n key added to **all four** locale files (en, pt, es, ja) or `tests/test_locales.py` fails.
- Run tests with `python -m pytest`.

---

### Task 1: Parsing module (`app/performance.py`) + config constants

**Files:**
- Create: `app/performance.py`
- Modify: `app/config.py` (add three constants)
- Test: `tests/test_performance.py`

**Interfaces:**
- Produces:
  - `normalize_name(name: str) -> str`
  - `extract_fps(text: str) -> tuple[int, str] | None` — `(sort_value, label)`
  - `parse_performance_csv(text: str) -> dict[str, dict]` — `norm_name -> {"fps": int, "label": str, "patch_type": str}`

- [ ] **Step 1: Add config constants**

In `app/config.py`, after the existing `HISTORY_CACHE_TTL` line (near line 53), add:

```python
PERFORMANCE_SHEET_ID = os.environ.get("PERFORMANCE_SHEET_ID", "1sOYZRiOuD9Cnr-e_hlzhRuxuEq5X5Ptwq4yCfwxyfFk")
PERFORMANCE_SHEET_GID = os.environ.get("PERFORMANCE_SHEET_GID", "0")
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_performance.py`:

```python
from app.performance import normalize_name, extract_fps, parse_performance_csv


def test_normalize_strips_trademark_and_punctuation():
    assert normalize_name("The Legend of Zelda™: Tears of the Kingdom") == \
        "the legend of zelda tears of the kingdom"


def test_normalize_collapses_whitespace_and_case():
    assert normalize_name("  ARMS   ") == "arms"


def test_extract_fps_capped():
    assert extract_fps("Capped 60FPS, increased consistency") == (60, "60fps")
    assert extract_fps("Capped 30FPS, increased consistency") == (30, "30fps")


def test_extract_fps_uncapped():
    assert extract_fps("Uncapped, increased framerate") == (999, "Uncapped")


def test_extract_fps_none_cases():
    assert extract_fps("Unchanged / Not Noticeable") is None
    assert extract_fps("N/A") is None
    assert extract_fps("") is None


def test_parse_csv_extracts_games_and_prefers_docked():
    csv_text = (
        '"[NEWS] big merged\nheader blob","x","y","z","stuff"\n'
        '"ARMS","Nintendo","5.5.1","Free Update","Capped 60FPS, increased consistency",'
        '"Unchanged","Improved","Capped 60FPS","Unchanged"\n'
        '"Among Us","Innersloth","2025","Unpatched","N/A","N/A","N/A","N/A","N/A"\n'
        '"Handheld Only Game","Pub","1.0","Unpatched","N/A","N/A","N/A","Capped 30FPS","x"\n'
    )
    result = parse_performance_csv(csv_text)
    assert result["arms"] == {"fps": 60, "label": "60fps", "patch_type": "Free Update"}
    # Among Us has no numeric fps in docked or handheld -> excluded
    assert "among us" not in result
    # Falls back to handheld when docked is N/A
    assert result["handheld only game"]["fps"] == 30
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_performance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.performance'`

- [ ] **Step 4: Implement `app/performance.py`**

```python
import csv
import io
import re

from app.config import PERFORMANCE_SHEET_GID, PERFORMANCE_SHEET_ID

_TRADEMARK = str.maketrans("", "", "™®©")


def normalize_name(name: str) -> str:
    """Lowercase, drop trademark symbols and punctuation, collapse whitespace."""
    s = (name or "").lower().translate(_TRADEMARK)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_fps(text: str):
    """Return (sort_value, label) parsed from prose, or None if no number.

    'Capped 60FPS...' -> (60, '60fps'); 'Uncapped...' -> (999, 'Uncapped');
    'Unchanged / Not Noticeable', 'N/A', '' -> None.
    """
    if not text:
        return None
    low = text.strip().lower()
    if "uncapped" in low:
        return (999, "Uncapped")
    m = re.search(r"(\d+)\s*fps", low)
    if m:
        n = int(m.group(1))
        return (n, f"{n}fps")
    return None


def parse_performance_csv(text: str) -> dict:
    """Parse the gviz CSV into {norm_name: {'fps', 'label', 'patch_type'}}.

    Column layout (positional): 0=name, 3=patch type, 4=framerate (docked),
    7=framerate (handheld). Prefer docked fps, fall back to handheld. Rows with
    no name, an obvious header/news blob, or no numeric fps are skipped.
    """
    result: dict = {}
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 5:
            continue
        name = row[0].strip()
        if not name or "\n" in name or len(name) > 100:
            continue
        norm = normalize_name(name)
        if not norm:
            continue
        patch_type = row[3].strip() if len(row) > 3 else ""
        fps = extract_fps(row[4])
        if fps is None and len(row) > 7:
            fps = extract_fps(row[7])
        if fps is None:
            continue
        result[norm] = {"fps": fps[0], "label": fps[1], "patch_type": patch_type}
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_performance.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit (stage + hand off)**

```bash
git add app/performance.py app/config.py tests/test_performance.py
# STOP — user commits manually
```

---

### Task 2: Sheet fetch (`fetch_performance_sheet`)

**Files:**
- Modify: `app/performance.py`
- Test: `tests/test_performance.py`

**Interfaces:**
- Consumes: `parse_performance_csv`, config constants (Task 1).
- Produces: `fetch_performance_sheet(sheet_id=None, gid=None, user_agent=None, timeout=20) -> dict`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_performance.py`:

```python
def test_fetch_performance_sheet_parses_response(monkeypatch):
    import app.performance as perf

    class FakeResp:
        text = ('"name","pub","ver","patch","fps"\n'
                '"ARMS","Nintendo","5.5.1","Free Update","Capped 60FPS"\n')
        def raise_for_status(self):
            pass

    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return FakeResp()

    monkeypatch.setattr(perf.requests, "get", fake_get)
    result = perf.fetch_performance_sheet(sheet_id="SID", gid="7")
    assert result["arms"]["fps"] == 60
    assert "SID" in captured["url"] and "gid=7" in captured["url"]
    assert "out:csv" in captured["url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_performance.py::test_fetch_performance_sheet_parses_response -v`
Expected: FAIL with `AttributeError: module 'app.performance' has no attribute 'requests'`

- [ ] **Step 3: Implement the fetch function**

At the top of `app/performance.py` add imports and add the config to the existing import line:

```python
import requests

from app.config import HEADERS, PERFORMANCE_SHEET_GID, PERFORMANCE_SHEET_ID
```

Add at the end of the module:

```python
def _headers(user_agent: str = None) -> dict:
    h = dict(HEADERS)
    if user_agent:
        h["User-Agent"] = user_agent
    return h


def fetch_performance_sheet(sheet_id: str = None, gid: str = None,
                            user_agent: str = None, timeout: int = 20) -> dict:
    """Fetch the community sheet as CSV and parse it. Raises on network/HTTP error."""
    sheet_id = sheet_id or PERFORMANCE_SHEET_ID
    gid = gid if gid is not None else PERFORMANCE_SHEET_GID
    url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
           f"/gviz/tq?tqx=out:csv&gid={gid}")
    resp = requests.get(url, headers=_headers(user_agent), timeout=timeout)
    resp.raise_for_status()
    return parse_performance_csv(resp.text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_performance.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit (stage + hand off)**

```bash
git add app/performance.py tests/test_performance.py
# STOP — user commits manually
```

---

### Task 3: DB layer + migration (`performance_cache`)

**Files:**
- Create: `migrations/V006__create_performance_cache_table.sql`
- Modify: `app/db.py`
- Test: `tests/test_db_performance.py`

**Interfaces:**
- Produces:
  - `save_performance_cache(rows: dict, db_path: str) -> None`
  - `load_performance_cache(db_path: str) -> dict` — `norm_name -> {"fps", "label", "patch_type"}`

- [ ] **Step 1: Create the migration**

Create `migrations/V006__create_performance_cache_table.sql`:

```sql
-- depends: V005__add_platform_flags_to_games_cache

CREATE TABLE performance_cache (
    norm_name  TEXT    PRIMARY KEY,
    fps        INTEGER NOT NULL DEFAULT 0,
    label      TEXT    NOT NULL DEFAULT '',
    patch_type TEXT    NOT NULL DEFAULT '',
    fetched_at REAL    NOT NULL
);
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_db_performance.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_db_performance.py -v`
Expected: FAIL with `ImportError: cannot import name 'save_performance_cache'`

- [ ] **Step 4: Implement the DB functions**

In `app/db.py`, add after `save_price_history_cache` (near line 149):

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_db_performance.py tests/test_migrations.py -v`
Expected: PASS

- [ ] **Step 6: Commit (stage + hand off)**

```bash
git add migrations/V006__create_performance_cache_table.sql app/db.py tests/test_db_performance.py
# STOP — user commits manually
```

---

### Task 4: Wire sheet fetch into `fetch_all_games` (outage-safe)

**Files:**
- Modify: `app/scraper.py`
- Test: `tests/test_scraper.py`

**Interfaces:**
- Consumes: `fetch_performance_sheet` (Task 2), `save_performance_cache` (Task 3).
- Produces: side effect — `fetch_all_games` populates `performance_cache`; emits `on_progress("performance", None, None, None)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scraper.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scraper.py::test_refresh_performance_saves_on_success -v`
Expected: FAIL with `AttributeError: module 'app.scraper' has no attribute '_refresh_performance'`

- [ ] **Step 3: Implement**

In `app/scraper.py`, extend the db import (near line 12) to include `save_performance_cache`:

```python
from app.db import load_cookies, save_cookies, save_games_cache, save_performance_cache
from app.performance import fetch_performance_sheet
```

Add this helper above `fetch_all_games` (near line 409):

```python
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
```

In `fetch_all_games`, just before `ts = save_games_cache(games, db_path)` (near line 472), add:

```python
    if on_progress:
        on_progress("performance", None, None, None)
    _refresh_performance(db_path, user_agent=user_agent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scraper.py -v`
Expected: PASS

- [ ] **Step 5: Commit (stage + hand off)**

```bash
git add app/scraper.py tests/test_scraper.py
# STOP — user commits manually
```

---

### Task 5: Render-time join (`_annotate_performance`) + endpoints

**Files:**
- Modify: `app/web.py`
- Test: `tests/test_web_games_table.py`

**Interfaces:**
- Consumes: `load_performance_cache` (Task 3), `normalize_name` (Task 1).
- Produces: `_annotate_performance(games: list[dict], db_path: str) -> None` — sets `perf_label` (str), `perf_sort` (int), `perf_sw2` (bool) on each game. Called in `index()` and `api_games_table()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_games_table.py`:

```python
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
        {"name": "Sw2 Flagged", "switch2": True},    # not in sheet -> blank
    ]
    _annotate_performance(games, temp_db)

    assert games[0]["perf_label"] == "60fps" and games[0]["perf_sw2"] is True
    assert games[0]["perf_sort"] == 60
    assert games[1]["perf_label"] == "30fps" and games[1]["perf_sw2"] is False
    assert games[2]["perf_label"] == "" and games[2]["perf_sort"] == 0
    assert games[2]["perf_sw2"] is False


def test_annotate_performance_sw2_flag_from_dekudeals(temp_db):
    from app.db import save_performance_cache
    from app.web import _annotate_performance

    save_performance_cache(
        {"g": {"fps": 30, "label": "30fps", "patch_type": "Unpatched"}}, temp_db)
    games = [{"name": "G", "switch2": True}]  # DekuDeals says SW2 version exists
    _annotate_performance(games, temp_db)
    assert games[0]["perf_sw2"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_games_table.py::test_annotate_performance_base_and_sw2 -v`
Expected: FAIL with `ImportError: cannot import name '_annotate_performance'`

- [ ] **Step 3: Implement**

In `app/web.py`, extend the `app.db` import block (near line 18) to add `load_performance_cache`, and add an import for `normalize_name`:

```python
from app.performance import normalize_name
```

Add the function after `_compute_best_buy` (near line 248):

```python
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
```

In `index()`, after `_compute_best_buy(games, selected_locales, reference_locale)` (near line 319), add:

```python
    _annotate_performance(games, db_path)
```

In `api_games_table()`, after `games = _filter_games(games, request.args)` (near line 382), add:

```python
        _annotate_performance(games, db_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_games_table.py -v`
Expected: PASS

- [ ] **Step 5: Commit (stage + hand off)**

```bash
git add app/web.py tests/test_web_games_table.py
# STOP — user commits manually
```

---

### Task 6: Frontend column + i18n

**Files:**
- Modify: `app/templates/index.html` (header `<th>`, `colKeyFor`, numeric sort branch, `.perf-s2` CSS)
- Modify: `app/templates/components/games_rows.html` (new `<td>`)
- Modify: `app/static/locales/en.json`, `pt.json`, `es.json`, `ja.json`
- Test: `tests/test_locales.py` (existing — must stay green), plus a manual render check

**Interfaces:**
- Consumes: `g.perf_label`, `g.perf_sort`, `g.perf_sw2` (Task 5).

- [ ] **Step 1: Add the header cell**

In `app/templates/index.html`, before the Release Date `<th>` (line 406), add:

```html
          <th data-type="num" data-i18n="th.performance">FPS</th>
```

- [ ] **Step 2: Register the column key**

In `colKeyFor(th, i)` (near line 580), before the `th.releaseDate` line, add:

```javascript
    if (th.dataset.i18n === 'th.performance') return 'perf';
```

- [ ] **Step 3: Add the numeric sort branch**

In `doSort`, in the type dispatch (near line 649), after the `price` branch and before `date`, add:

```javascript
      if (type === 'num') { cmp = (parseFloat(av) - parseFloat(bv)) * dir; }
      else if (type === 'price') { cmp = (parsePrice(av) - parsePrice(bv)) * dir; }
```

(Replace the existing `if (type === 'price')` line with the two lines above.)

- [ ] **Step 4: Add the `S2` badge CSS**

In `app/templates/index.html`, near the `.sale-end` / `.rel-date` cell styles (around line 201), add:

```css
    .perf { white-space: nowrap; }
    .perf-s2 { font-size: .62rem; font-weight: 700; color: var(--accent);
               border: 1px solid var(--accent); border-radius: 3px;
               padding: 0 .2rem; vertical-align: middle; }
```

- [ ] **Step 5: Add the body cell**

In `app/templates/components/games_rows.html`, before the `<td class="rel-date" ...>` line (line 44), add:

```html
  <td class="perf" data-sort="{{ g.perf_sort }}">
    {%- if g.perf_label -%}{{ g.perf_label }}{% if g.perf_sw2 %} <span class="perf-s2">S2</span>{% endif %}{%- endif -%}
  </td>
```

- [ ] **Step 6: Add the i18n key to all four locales**

In each of `en.json`, `pt.json`, `es.json`, `ja.json`, add alongside the other `th.*` keys:

```json
  "th.performance": "FPS",
```

- [ ] **Step 7: Run the locale test**

Run: `python -m pytest tests/test_locales.py -v`
Expected: PASS (keys consistent across all four locales)

- [ ] **Step 8: Manual render check**

Run the app (see project run instructions), load a wishlist with known games (e.g. the demo wishlist), trigger a refresh, and confirm:
- The FPS column appears with values like `60fps` / `30fps` and blanks for unmatched games.
- A game with a Switch 2 version shows the `S2` badge.
- Clicking the column header sorts numerically; blanks (`perf_sort=0`) sort to the bottom ascending.
- Dragging the column reorders it and the order persists on reload.

- [ ] **Step 9: Run the full suite**

Run: `python -m pytest`
Expected: PASS (all tests)

- [ ] **Step 10: Commit (stage + hand off)**

```bash
git add app/templates/index.html app/templates/components/games_rows.html app/static/locales/
# STOP — user commits manually
```

---

## Self-Review

**Spec coverage:**
- Data source / gviz CSV → Task 2. ✅
- Parsing (normalize, extract_fps prose cases, column layout) → Task 1. ✅
- `performance_cache` table + save/load → Task 3. ✅
- Outage-safe refresh inside `fetch_all_games` + progress step → Task 4. ✅
- Render-time smart join (base vs S2, unmatched blank) → Task 5. ✅
- Frontend th/td/colKey/numeric sort/badge/i18n → Task 6. ✅
- Config constants (env-overridable) → Task 1. ✅
- Mobile (no separate markup, scrolls) → covered by Task 6 manual check. ✅
- Testing (normalize, extract_fps, csv fixture, smart decision, round-trip, annotate) → Tasks 1,3,5. ✅
- Out of scope (no filter button) → honored. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅

**Type consistency:** `parse_performance_csv`/`load_performance_cache` both return `{norm_name: {"fps": int, "label": str, "patch_type": str}}`; `_annotate_performance` reads `row["label"]`, `row["fps"]`, `row.get("patch_type")` — consistent. `perf_sort` (int) drives `data-sort` + numeric sort (`parseFloat`). ✅
