"""Записывает 30-сек продающий walkthrough Mini App.
Перед запуском: api.py поднят локально на :8000 без TELEGRAM_BOT_TOKEN.
Выход: docs/video/sales_demo_raw.webm
"""
import asyncio
import shutil
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "video"
OUT_DIR.mkdir(parents=True, exist_ok=True)
BASE = "http://localhost:8000"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            record_video_dir=str(OUT_DIR),
            record_video_size={"width": 390, "height": 844},
        )

        # Мокаем цены — чтобы во время записи показать большую разницу между поставщиками
        async def mock_nomenclature(route):
            await route.fulfill(json={
                "products": [
                    {"id": "p1", "name": "Помидор грунтовый",  "unit": "кг", "code": "T-001"},
                    {"id": "p2", "name": "Помидор био Эко-ферма", "unit": "кг", "code": "T-002"},
                    {"id": "p3", "name": "Помидоры черри 250г",   "unit": "уп", "code": "T-003"},
                ]
            })
        async def mock_prices(route):
            await route.fulfill(json={
                "product": {"id": "p1", "name": "Помидор грунтовый", "unit": "кг"},
                "best": {"supplier_name": "ТОО АгроПром", "price_per_unit": 720, "unit": "кг", "date": "01.06.2026"},
                "suppliers": [
                    {"supplier_name": "ТОО АгроПром",      "price_per_unit": 720, "unit": "кг", "date": "01.06.2026", "delta_pct": 0},
                    {"supplier_name": "ИП Фермер",          "price_per_unit": 780, "unit": "кг", "date": "28.05.2026", "delta_pct": 8.3},
                    {"supplier_name": "Овощебаза №3",       "price_per_unit": 850, "unit": "кг", "date": "24.05.2026", "delta_pct": 18.0},
                    {"supplier_name": "Магнум Cash&Carry",  "price_per_unit": 990, "unit": "кг", "date": "15.05.2026", "delta_pct": 37.5},
                ],
                "stats": {"purchases": 12, "days": 60},
                "iiko_ok": True,
            })
        await ctx.route("**/api/nomenclature/search/**", mock_nomenclature)
        await ctx.route("**/api/price-stats/**", mock_prices)

        page = await ctx.new_page()
        await page.add_init_script(
            "window.Telegram={WebApp:{initData:'',initDataUnsafe:{user:{id:0,first_name:'Demo'}},"
            "version:'7.0',platform:'ios',ready:()=>{},expand:()=>{},HapticFeedback:{impactOccurred:()=>{}}}};"
        )

        # === СЦЕНАРИЙ ===
        await page.goto(BASE, wait_until="domcontentloaded")
        # 0-2s: ждём сплеш
        await page.wait_for_function("document.getElementById('splash')?.style.display === 'none'", timeout=6000)
        await page.wait_for_timeout(800)

        # 2-7s: тыкаем "Попробовать демо" → главная дашборд анимируется
        await page.click("button:has-text('Попробовать демо')")
        await page.wait_for_selector("#navbar.visible", timeout=5000)
        await page.wait_for_timeout(2500)  # пусть числа доанимируются

        # 7-12s: Продажи
        await page.click(".nav-item:has-text('Продажи')")
        await page.wait_for_timeout(2500)

        # 12-13s: Расходы → Накладные → Цены
        await page.click(".nav-item:has-text('Расходы')")
        await page.wait_for_timeout(800)
        await page.click(".tab:has-text('Накладные')")
        await page.wait_for_timeout(800)
        await page.click("#inv-pill-prices")
        await page.wait_for_timeout(700)

        # 14-17s: имитируем печать "помидор"
        for ch in "помидор":
            await page.type("#ps-input", ch, delay=120)
        await page.wait_for_selector("#ps-results.active .ps-result", timeout=3000)
        await page.wait_for_timeout(1000)

        # 17-25s: выбираем товар → появляется карточка цен с дельтой
        await page.click("#ps-results .ps-result >> nth=0")
        await page.wait_for_selector(".ps-card .ps-best", timeout=3000)
        await page.wait_for_timeout(3500)

        # 25-30s: листаем поставщиков вниз
        await page.evaluate("window.scrollBy({top:200,behavior:'smooth'})")
        await page.wait_for_timeout(2500)

        # 30-33s: возвращаемся на главную → финал
        await page.click(".nav-item:has-text('Главная')")
        await page.wait_for_timeout(2200)

        # Закрываем — Playwright дозапишет .webm
        await ctx.close()
        await browser.close()

    # Найдём свежий webm и переименуем в стабильное имя
    webms = sorted(OUT_DIR.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not webms:
        print("⚠ Видео не сохранилось")
        return
    target = OUT_DIR / "sales_demo_raw.webm"
    if webms[0] != target:
        shutil.move(str(webms[0]), str(target))
    print(f"✓ raw video: {target}")
    print(f"  size: {target.stat().st_size // 1024} KB")


if __name__ == "__main__":
    asyncio.run(main())
