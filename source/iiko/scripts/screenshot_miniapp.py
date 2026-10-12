"""Снимает скриншоты Mini App в демо-режиме для инструкции/FAQ.
Перед запуском: api.py должен быть поднят локально на :8000 БЕЗ TELEGRAM_BOT_TOKEN.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://localhost:8000"


async def shot(page, name: str, full_page: bool = True):
    path = OUT / f"{name}.png"
    await page.screenshot(path=str(path), full_page=full_page)
    print(f"  ✓ {name}.png")


async def main():
    async with async_playwright() as pw:
        # iPhone 14 viewport, retina
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        )
        page = await ctx.new_page()

        # Подделываем Telegram.WebApp — иначе фронт не понимает, что он в Telegram.
        await page.add_init_script(
            """
            window.Telegram = {
              WebApp: {
                initData: '',
                initDataUnsafe: { user: { id: 0, first_name: 'Demo' } },
                version: '7.0',
                platform: 'ios',
                colorScheme: 'light',
                themeParams: {},
                ready: () => {},
                expand: () => {},
                close: () => {},
                MainButton: { show: () => {}, hide: () => {}, onClick: () => {}, setText: () => {} },
                BackButton: { show: () => {}, hide: () => {}, onClick: () => {} },
                HapticFeedback: { impactOccurred: () => {}, notificationOccurred: () => {} },
                sendData: (d) => console.log('sendData', d),
              }
            };
            """
        )

        # Мок /api/comparison — бэк отдаёт пусто для demo-org-1, поэтому подставляем
        # синтетические данные год-к-году чтобы скриншот не был пустым.
        async def mock_comparison(route):
            await route.fulfill(json={
                "cur_year": 2026, "prev_year": 2025,
                "channels": ["Обычный заказ", "Доставка", "Самовывоз"],
                "weeks": {
                    "current": [
                        {"key": f"2026-W{w:02d}", "label": f"Нед {w}", "year": 2026,
                         "total_rev": 9_000_000 + w*180_000,
                         "total_orders": 800 + w*15,
                         "channels": {
                             "Обычный заказ": {"rev": (9_000_000 + w*180_000)*0.7, "orders": (800 + w*15)*0.7},
                             "Доставка":      {"rev": (9_000_000 + w*180_000)*0.2, "orders": (800 + w*15)*0.2},
                             "Самовывоз":     {"rev": (9_000_000 + w*180_000)*0.1, "orders": (800 + w*15)*0.1},
                         }} for w in range(18, 23)
                    ],
                    "previous": [
                        {"key": f"2025-W{w:02d}", "label": f"Нед {w}", "year": 2025,
                         "total_rev": 7_500_000 + w*150_000,
                         "total_orders": 700 + w*12,
                         "channels": {
                             "Обычный заказ": {"rev": (7_500_000 + w*150_000)*0.75, "orders": (700 + w*12)*0.75},
                             "Доставка":      {"rev": (7_500_000 + w*150_000)*0.18, "orders": (700 + w*12)*0.18},
                             "Самовывоз":     {"rev": (7_500_000 + w*150_000)*0.07, "orders": (700 + w*12)*0.07},
                         }} for w in range(18, 23)
                    ],
                },
                "months": {
                    "current":  [{"key": f"2026-{m:02d}", "label": l, "year": 2026,
                                  "total_rev": 30_000_000 + m*2_000_000, "total_orders": 3200 + m*100,
                                  "channels": {"Обычный заказ": {"rev": (30_000_000 + m*2_000_000)*0.7,
                                                                 "orders": (3200 + m*100)*0.7}}}
                                 for m, l in enumerate(["Янв","Фев","Мар","Апр","Май"], start=1)],
                    "previous": [{"key": f"2025-{m:02d}", "label": l, "year": 2025,
                                  "total_rev": 25_000_000 + m*1_500_000, "total_orders": 2800 + m*80,
                                  "channels": {"Обычный заказ": {"rev": (25_000_000 + m*1_500_000)*0.7,
                                                                 "orders": (2800 + m*80)*0.7}}}
                                 for m, l in enumerate(["Янв","Фев","Мар","Апр","Май"], start=1)],
                },
            })
        await ctx.route("**/api/comparison/**", mock_comparison)

        print("→ Открываю онбординг")
        await page.goto(BASE, wait_until="networkidle")
        # Сплеш висит 1500ms + fade 500ms — ждём пока уйдёт и появится онбординг
        await page.wait_for_function(
            "document.getElementById('splash')?.style.display === 'none'",
            timeout=5000,
        )
        await page.wait_for_selector("button:has-text('Попробовать демо')", timeout=3000)
        await page.wait_for_timeout(400)  # дать анимации входа доиграть
        await shot(page, "01_onboarding_welcome", full_page=False)

        print("→ Жму 'Попробовать демо'")
        await page.click("button:has-text('Попробовать демо')")
        # Демо может либо идти к step2/3/4 либо сразу на main — ждём навбар
        await page.wait_for_selector("#navbar.visible", timeout=5000)
        await page.wait_for_timeout(1500)  # анимация чисел
        await shot(page, "02_main_dashboard")

        # Скроллим главную чтобы захватить чарт
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
        await shot(page, "02b_main_top", full_page=False)

        # Tab: Продажи
        print("→ Вкладка Продажи")
        await page.click(".nav-item:has-text('Продажи')")
        await page.wait_for_timeout(1500)
        await shot(page, "03_sales_channels")

        # Внутри Продаж переключаем под-табы
        for tab_label, slug in [
            ("Каналы", "03a_sales_channels"),
            ("Сотрудники", "03b_sales_staff"),
            ("ABC", "03c_sales_abc"),
            ("Отмены", "03d_sales_cancels"),
        ]:
            try:
                await page.click(f".tabs >> text='{tab_label}'", timeout=2000)
                await page.wait_for_timeout(1200)
                await shot(page, slug)
            except Exception as e:
                print(f"  ⚠ pod-tab '{tab_label}' пропускаю: {e}")

        # Tab: Персонал
        print("→ Вкладка Персонал")
        await page.click(".nav-item:has-text('Персонал')")
        await page.wait_for_timeout(1500)
        await shot(page, "04_staff")

        # Tab: Расходы
        print("→ Вкладка Расходы")
        await page.click(".nav-item:has-text('Расходы')")
        await page.wait_for_timeout(1500)
        await shot(page, "05_expenses")

        # Tab: Год/год — данные приходят из мока, ждём пока отрендерится таблица
        print("→ Вкладка Год/год")
        await page.click(".nav-item:has-text('Год/год')")
        # Ждём пока заголовок заполнится (показатель что данные пришли и отрендерились)
        await page.wait_for_function(
            "document.getElementById('comp-years')?.textContent.includes('vs')",
            timeout=5000,
        )
        await page.wait_for_timeout(1500)  # анимации цифр
        await shot(page, "06_comparison")

        # Settings modal
        print("→ Настройки (модалка)")
        # Возвращаемся на главную чтобы фон модалки был осмысленным
        await page.click(".nav-item:has-text('Главная')")
        await page.wait_for_timeout(800)
        try:
            await page.evaluate("document.getElementById('settings-modal')?.classList.add('active')")
            await page.wait_for_timeout(500)
            await shot(page, "07_settings_modal", full_page=False)
        except Exception as e:
            print(f"  ⚠ settings: {e}")
        # Закрываем модалку
        await page.evaluate("document.getElementById('settings-modal')?.classList.remove('active')")
        await page.wait_for_timeout(300)

        # ============ Накладные и цены ============

        # Мокаем поиск номенклатуры и price-stats — для демо-юзера бэк отдаёт пусто
        async def mock_nomenclature(route):
            await route.fulfill(json={
                "products": [
                    {"id": "p-tomato",     "name": "Помидор грунтовый",     "unit": "кг", "code": "T-001"},
                    {"id": "p-tomato-bio", "name": "Помидор био Эко-ферма", "unit": "кг", "code": "T-002"},
                    {"id": "p-tomato-ch",  "name": "Помидоры черри 250г",   "unit": "уп", "code": "T-003"},
                    {"id": "p-tomato-pas", "name": "Томатная паста 1кг",    "unit": "шт", "code": "T-010"},
                ]
            })
        await ctx.route("**/api/nomenclature/search/**", mock_nomenclature)

        async def mock_price_stats(route):
            await route.fulfill(json={
                "product": {"id": "p-tomato", "name": "Помидор грунтовый", "unit": "кг"},
                "best": {
                    "supplier_name": "ТОО АгроПром",
                    "price_per_unit": 720,
                    "unit": "кг",
                    "date": "01.06.2026",
                },
                "suppliers": [
                    {"supplier_name": "ТОО АгроПром",     "price_per_unit": 720,  "unit": "кг",
                     "date": "01.06.2026", "delta_pct": 0},
                    {"supplier_name": "ИП Фермер",        "price_per_unit": 780,  "unit": "кг",
                     "date": "28.05.2026", "delta_pct": 8.3},
                    {"supplier_name": "Овощебаза №3",     "price_per_unit": 850,  "unit": "кг",
                     "date": "24.05.2026", "delta_pct": 18.0},
                    {"supplier_name": "ТОО СвежиеОвощи",  "price_per_unit": 690,  "unit": "кг",
                     "date": "20.05.2026", "delta_pct": -4.2},
                    {"supplier_name": "Магнум Cash&Carry", "price_per_unit": 990, "unit": "кг",
                     "date": "15.05.2026", "delta_pct": 37.5},
                ],
                "stats": {"purchases": 12, "days": 60},
                "iiko_ok": True,
            })
        await ctx.route("**/api/price-stats/**", mock_price_stats)

        # На вкладку Расходы → Накладные
        print("→ Накладные: список")
        await page.click(".nav-item:has-text('Расходы')")
        await page.wait_for_timeout(800)
        await page.click(".tab:has-text('Накладные')")
        await page.wait_for_timeout(1500)
        await shot(page, "08_invoices_list")

        # Pill: Цены (пустое состояние)
        print("→ Накладные: вкладка Цены (пустой поиск)")
        await page.click("#inv-pill-prices")
        await page.wait_for_timeout(600)
        await shot(page, "09_price_search_empty", full_page=False)

        # Ввод запроса → dropdown
        print("→ Накладные: ищу 'помидор'")
        await page.fill("#ps-input", "помидор")
        # Поиск дебаунсится 300ms — ждём ответ + рендер
        await page.wait_for_selector("#ps-results.active .ps-result", timeout=3000)
        await page.wait_for_timeout(400)
        await shot(page, "10_price_search_dropdown", full_page=False)

        # Клик на первый продукт → карточка цен
        print("→ Накладные: карточка цен по поставщикам")
        await page.click("#ps-results .ps-result >> nth=0")
        await page.wait_for_selector(".ps-card .ps-best", timeout=3000)
        await page.wait_for_timeout(500)
        await shot(page, "11_price_card")

        await browser.close()
        print(f"\n✓ Готово. Файлы в {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
