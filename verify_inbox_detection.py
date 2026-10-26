import asyncio
import re
import sys
import os
sys.stdout.reconfigure(encoding="utf-8")
from playwright.async_api import async_playwright

async def test_full():
    state_path = r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\state.json"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=state_path, locale="fr-FR")
        gpage = await ctx.new_page()
        
        print("Checking inbox...")
        await gpage.goto("https://mail.google.com/mail/u/0/#inbox", wait_until="domcontentloaded")
        await gpage.wait_for_selector("tr.zA", timeout=12000)
        
        rows = await gpage.locator("tr.zA").all()
        meet_regex = re.compile(r"meet\.google\.com/([a-zA-Z]{3}-[a-zA-Z]{4}-[a-zA-Z]{3})")
        
        found = None
        for r in rows[:10]:
            txt = await r.inner_text()
            m = meet_regex.search(txt)
            if m:
                found = f"https://meet.google.com/{m.group(1).lower()}"
                print(f"FOUND LATEST MEET LINK: {found}!")
                print(f"Snippet: {txt.replace('\n', ' ')[:100]}")
                break
                
        await ctx.close()
        await browser.close()
        return found

if __name__ == "__main__":
    link = asyncio.run(test_full())
    print("Test Result Link:", link)
