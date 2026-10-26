import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")
from playwright.async_api import async_playwright

async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state="state.json", locale="ar")
        page = await context.new_page()
        await page.goto("https://meet.google.com/kfa-rztj-qsn")
        await asyncio.sleep(4)
        
        error_selectors = [
            ("Sorry, we've encountered a problem", "عذراً، حدثت مشكلة"),
            ("Returning to home screen", "الرجوع إلى الشاشة الرئيسية"),
            ("You can't join this video call", "لا يمكنك الانضمام إلى مكالمة الفيديو هذه"),
            ("You can't join this video call", "لا يُمكنك الانضمام إلى مكالمة الفيديو هذه"),
        ]
        
        for en, ar in error_selectors:
            for s in [en, ar]:
                try:
                    el = page.locator(f'text="{s}"').first
                    if await el.is_visible(timeout=500):
                        print(f"FOUND ERROR SELECTOR: '{s}'")
                except:
                    pass
                    
        await browser.close()

if __name__ == "__main__":
    asyncio.run(check())
