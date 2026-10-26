import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")
from playwright.async_api import async_playwright

async def test_join_flow():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--mute-audio",
                "--no-first-run",
                "--hide-scrollbars",
                "--disable-breakpad",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        context = await browser.new_context(
            storage_state=r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\state.json",
            viewport={"width": 1280, "height": 720},
            locale="fr-FR"
        )
        page = await context.new_page()
        
        target_url = "https://meet.google.com/kfa-rztj-qsn"
        print(f"Navigating to {target_url}...")
        await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        
        # 1. Wait for preparation to settle and dismiss modal
        print("Waiting for modal / preparation...")
        for sec in range(1, 20):
            await asyncio.sleep(1)
            # Dismiss mic/cam modal
            try:
                m = page.locator('button[jsname="IbE0S"], button:has-text("Continuer sans micro")').first
                if await m.is_visible(timeout=200):
                    print(f"[{sec}s] Dismissing modal popup...")
                    await m.click(force=True)
                    await asyncio.sleep(0.5)
            except Exception:
                pass
            
            # Check if join button is visible
            join_btn = page.locator('button:has-text("Participer à la réunion"), button:has-text("Join now"), button:has-text("انضم الآن")').first
            if await join_btn.is_visible(timeout=300):
                txt = (await join_btn.inner_text()).replace("\n", " ").strip()
                print(f"[{sec}s] SUCCESS: Found Join Button: '{txt}'!")
                await page.screenshot(path=r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\join_button_visible.png")
                
                print("Clicking Join Button now...")
                try:
                    await join_btn.click(force=True)
                except Exception:
                    await join_btn.evaluate("e => e.click()")
                print("Join Button clicked successfully!")
                break
                
        # 2. Wait for transition into room
        print("\nMonitoring transition into call...")
        inside = False
        for post in range(1, 20):
            await asyncio.sleep(1)
            
            # Dismiss leftover modal if re-popped inside room
            try:
                in_call_m = page.locator('button[jsname="IbE0S"], button:has-text("Continuer sans micro")').first
                if await in_call_m.is_visible(timeout=200):
                    print(f"[+{post}s] Dismissing in-call popup...")
                    await in_call_m.click(force=True)
            except Exception:
                pass

            # True call indicators
            leave_btn = page.locator('button[aria-label*="Quitter l\'appel" i], button[aria-label*="Leave call" i], button[jsname="CQylAd"]').first
            chat_btn = page.locator('button[data-panel-id="2"], button[aria-label*="Discuter avec" i]').first
            
            is_leave_vis = await leave_btn.is_visible(timeout=200)
            is_chat_vis = await chat_btn.is_visible(timeout=200)
            
            print(f"[+{post}s] Leave button: {is_leave_vis} | Chat button: {is_chat_vis} | URL: {page.url}")
            
            if is_leave_vis or is_chat_vis:
                print(f"\n🎉 CONFIRMED: BOT IS ACTIVELY INSIDE THE MEETING ROOM AT +{post}s!")
                inside = True
                await page.screenshot(path=r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\inside_call_success.png")
                break

        if not inside:
            print("Failed to confirm entry within 20s.")
            await page.screenshot(path=r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\inside_call_failed.png")

        await browser.close()
        print("Test complete.")

if __name__ == "__main__":
    asyncio.run(test_join_flow())
