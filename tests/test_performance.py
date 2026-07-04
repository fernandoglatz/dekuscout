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
