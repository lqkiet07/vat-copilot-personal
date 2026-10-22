from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, channel="chrome")
    pg = b.new_page(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        locale="vi-VN",
        viewport={"width": 1366, "height": 900},
    )
    pg.goto(
        "https://vbpl.vn/van-ban/chi-tiet/luat-thue-gia-tri-gia-tang-so-48-2024-qh15--174917",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    pg.wait_for_timeout(15000)
    print("title:", pg.title())
    print("body len:", pg.evaluate("() => document.body.innerText.length"))
    print("has preview:", pg.evaluate("() => document.body.innerHTML.includes('preview-content')"))
    print(pg.evaluate("() => document.body.innerText.slice(0, 800)"))
    b.close()
