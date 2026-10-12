"""Снимок шага 2 онбординга с '?' кнопкой и открытой help-модалкой."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2, is_mobile=True, has_touch=True,
        )
        page = await ctx.new_page()
        await page.add_init_script("window.Telegram = {WebApp:{initData:'',initDataUnsafe:{},ready:()=>{},expand:()=>{},HapticFeedback:{impactOccurred:()=>{}}}};")
        await page.goto("http://localhost:8000/", wait_until="networkidle")
        await page.wait_for_function("document.getElementById('splash')?.style.display === 'none'", timeout=5000)
        await page.click("button:has-text('Начать')")
        await page.wait_for_selector("#onboarding-step2", state="visible")
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(OUT / "12_register_with_help_btn.png"), full_page=False)
        print("✓ 12_register_with_help_btn.png")

        # Открываем модалку
        await page.click(".help-btn")
        await page.wait_for_selector("#help-modal.active", timeout=2000)
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(OUT / "13_register_help_modal.png"), full_page=False)
        print("✓ 13_register_help_modal.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
