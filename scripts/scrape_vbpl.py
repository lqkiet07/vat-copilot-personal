"""Scrape full text (.preview-content innerHTML) of 5 VAT legal docs from vbpl.vn."""
from pathlib import Path
from playwright.sync_api import sync_playwright

DOCS = [
    ("01_luat_48_2024.html",
     "https://vbpl.vn/van-ban/chi-tiet/luat-thue-gia-tri-gia-tang-so-48-2024-qh15--174917"),
    ("02_nd_181_2025.html",
     "https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-181-2025-nd-cp-quy-dinh-chi-tiet-thi-hanh-mot-so-dieu-cua-luat-thue-gia-tria-gia-tang--187347"),
    ("03_tt_69_2025.html",
     "https://vbpl.vn/van-ban/chi-tiet/thong-tu-so-69-2025-tt-btc-quy-dinh-chi-tiet-mot-so-dieu-cua-luat-thue-gia-tria-gia-tang-va-huong-dan-thuc-hien-nghi-dinh-so-181-2025-nd-cp-ngay-01-thang-7-nam-2025-cua-chinh-phu-quy-dinh-chi-tiet-thi-hanh-mot-so-dieu-cua-luat-thue-gia-tria-gia-tang--187346"),
    ("04_nd_359_2025.html",
     "https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-359-2025-nd-cp-sua-doi-bo-sung-mot-so-dieu-cua-nghi-dinh-so-181-2025-nd-cp-ngay-01-thang-7-nam-2025-cua-chinh-phu-quy-dinh-chi-tiet-thi-hanh-mot-so-dieu-cua-luat-thue-gia-tri-gia-tang--186979"),
    ("05_nd_144_2026.html",
     "https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-144-2026-nd-cp-sua-doi-bo-sung-mot-so-dieu-cua-nghi-dinh-so-181-2025-nd-cp-ngay-01-thang-7-nam-2025-cua-chinh-phu-quy-dinh-chi-tiet-thi-hanh-mot-so-dieu-cua-luat-thue-gia-tri-gia-tang-da-duoc-sua-doi-bo-sung-mot-so-dieu-boi-nghi-dinh-so-359-2025-nd-cp-ngay-31-thang-12-nam-2025-cua-chinh-phu--2f53d590-68a2-11f1-abca-2fa19d617f21"),
]

OUT = Path("data/raw")
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="chrome")
    for fname, url in DOCS:
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="vi-VN",
            viewport={"width": 1366, "height": 900},
        )
        print("GET", fname, flush=True)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector(".preview-content", timeout=45000)
            page.wait_for_function(
                "() => document.querySelector('.preview-content').innerText.length > 2000",
                timeout=45000,
            )
        except Exception as e:
            print("  WAIT-FAIL:", fname, type(e).__name__)
        html = page.evaluate("() => document.querySelector('.preview-content').innerHTML")
        text_len = page.evaluate("() => document.querySelector('.preview-content').innerText.length")
        (OUT / fname).write_text(html or "", encoding="utf-8")
        print(f"  saved {fname}: html={len(html or '')} chars, text={text_len} chars", flush=True)
        page.close()
    browser.close()
print("DONE")
