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
        
        el = page.locator("text='الرجوع إلى الشاشة الرئيسية'").first
        is_vis = await el.is_visible()
        print("Is 'الرجوع إلى الشاشة الرئيسية' visible on normal lobby?:", is_vis)
        
        # Check what buttons are on the page right now
        btns = await page.locator("button, [role='button']").all()
        for b in btns:
            try:
                t = (await b.inner_text()).strip().replace("\n", " ")
                if t:
                    print("VISIBLE BUTTON:", t)
            except:
                pass
                
        await browser.close()

if __name__ == "__main__":
    asyncio.run(check())
