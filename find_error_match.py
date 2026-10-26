import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state="state.json", locale="ar")
        page = await context.new_page()
        await page.goto("https://meet.google.com/kfa-rztj-qsn")
        await asyncio.sleep(4)
        
        patterns = [
            "Returning to home screen",
            "الرجوع إلى الشاشة الرئيسية",
            "Returning",
            "الرجوع",
            "Sorry, we've encountered a problem",
            "عذراً، حدثت مشكلة"
        ]
        for pat in patterns:
            loc = page.locator(f':text("{pat}")')
            cnt = await loc.count()
            print(f"Pattern '{pat}' count: {cnt}")
            for i in range(cnt):
                el = loc.nth(i)
                vis = await el.is_visible()
                tag = await el.evaluate("e => e.tagName")
                outer = (await el.evaluate("e => e.outerHTML"))[:150]
                print(f"   [{i}] visible={vis}, tag={tag}, html={outer}")
                
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect())
