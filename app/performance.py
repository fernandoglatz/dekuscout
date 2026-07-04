import csv
import io
import re

import requests

from app.config import HEADERS, PERFORMANCE_SHEET_GID, PERFORMANCE_SHEET_ID

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
