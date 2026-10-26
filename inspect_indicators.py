import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\state.json",
            viewport={"width": 1280, "height": 720},
            locale="fr-FR"
        )
        page = await context.new_page()
        await page.goto("https://meet.google.com/kfa-rztj-qsn", wait_until="domcontentloaded")
        
        # Wait until pre-join controls load (around 8-10 seconds)
        print("Waiting for pre-join lobby to settle...")
        await asyncio.sleep(10)
        
        # Find all elements matching our indicators
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
        
        print("\n--- Testing each indicator on the PRE-JOIN LOBBY ---")
        for ind in indicators:
            vis = False
            try:
                el = page.locator(ind).first
                vis = await el.is_visible(timeout=200)
                txt = (await el.inner_text()).replace("\n", " ") if vis else ""
                aria = await el.get_attribute("aria-label") if vis else ""
            except Exception:
                pass
            print(f"Indicator: {ind:50} -> Visible on PRE-JOIN? {vis} (aria='{aria}')")

        # Now let's see the join button on pre-join
        join_btn = page.locator('button:has-text("Participer à la réunion"), button:has-text("Demander à participer")').first
        print(f"\nJoin Button visible on lobby: {await join_btn.is_visible(timeout=1000)}")

        await page.screenshot(path=r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\inspect_lobby.png")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect())
