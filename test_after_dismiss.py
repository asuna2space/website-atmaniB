import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\state.json",
            viewport={"width": 1280, "height": 720},
            locale="fr-FR"
        )
        page = await context.new_page()
        await page.goto("https://meet.google.com/kfa-rztj-qsn", wait_until="domcontentloaded")
        
        print("Waiting for modal...")
        modal_btn = page.locator('button[jsname="IbE0S"], button:has-text("Continuer sans micro")').first
        await modal_btn.wait_for(state="visible", timeout=15000)
        print("Modal visible! Dismissing...")
        await modal_btn.click(force=True)
        await asyncio.sleep(2)
        
        print("Modal dismissed! Inspecting elements now:")
        indicators = [
            'button[aria-label*="chat" i]',
            'button[aria-label*="الدردشة" i]',
            'button[data-panel-id="2"]',
            'button[aria-label*="Leave" i]',
            'button[aria-label*="مغادرة" i]',
            'button[aria-label*="إنهاء" i]',
            'button[aria-label*="Quitter" i]',
            'button[aria-label*="appel" i]',
            'button[jsname="CQylAd"]',
            'div[data-meeting-title]',
            'div[data-call-controls]',
            '*[aria-label*="Désactiver la caméra"]',
            '*[aria-label*="Partager l\'écran"]',
            '*[aria-label*="Paramètres audio"]',
            '*[aria-label*="Informations sur la réunion"]'
        ]
        
        for ind in indicators:
            try:
                locator = page.locator(ind)
                count = await locator.count()
                for idx in range(count):
                    el = locator.nth(idx)
                    vis = await el.is_visible()
                    if vis:
                        txt = (await el.inner_text()).replace("\n", " ")
                        aria = await el.get_attribute("aria-label") or ""
                        jsname = await el.get_attribute("jsname") or ""
                        tag = await el.evaluate("e => e.tagName")
                        print(f"MATCH: {ind} | Tag: {tag} | Visible: {vis} | Aria: '{aria}' | Text: '{txt}' | jsname: '{jsname}'")
            except Exception as e:
                print(f"Error on {ind}: {e}")
                
        await page.screenshot(path=r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\after_modal_dismiss.png")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test())
