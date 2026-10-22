from pathlib import Path

EXPECTED_FILES = [
    "01_luat_48_2024.html",
    "02_nd_181_2025.html",
    "03_tt_69_2025.html",
    "04_nd_359_2025.html",
    "05_nd_144_2026.html",
]


def test_raw_files_exist_and_not_empty():
    raw_dir = Path("data/raw")
    assert raw_dir.exists(), "Thu muc data/raw khong ton tai"
    for fname in EXPECTED_FILES:
        fpath = raw_dir / fname
        assert fpath.exists(), f"Thieu file: {fname}"
        content = fpath.read_text(encoding="utf-8")
        assert len(content) > 1000, f"File {fname} qua ngan hoac rong"
        assert "Điều 1." in content or "Điều 1" in content, f"File {fname} khong chua cau truc dieu luat"
