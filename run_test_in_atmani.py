import asyncio
import os
import sys

# Ensure current directory is atmani
os.chdir(r"C:\Users\Admin\.gemini\antigravity\scratch\atmani")
sys.path.insert(0, r"C:\Users\Admin\.gemini\antigravity\scratch\atmani")

from playwright.async_api import async_playwright
import bot

async def main():
    print("=== Testing bot.py in C:\\Users\\Admin\\.gemini\\antigravity\\scratch\\atmani ===")
    
    # Initialize basic state
    bot.bot_state["meet_url"] = "https://meet.google.com/kfa-rztj-qsn"
    bot.bot_state["student_name"] = "hazem"
    bot.MEET_URL = "https://meet.google.com/kfa-rztj-qsn"
    bot.STUDENT_NAME = "hazem"
    bot.HEADLESS = True

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
        print("Chromium launched.")
        
        # Test the meeting flow
        target_url = "https://meet.google.com/kfa-rztj-qsn"
        student_name = "hazem"
        
        print(f"Executing run_single_meeting_flow for {target_url} ...")
        # Run in background task so we can monitor and take screenshots
        flow_task = asyncio.create_task(bot.run_single_meeting_flow(browser, target_url, student_name))
        
        # Monitor for up to 45 seconds
        for sec in range(1, 46):
            await asyncio.sleep(1)
            page = bot.current_page_ref
            
            if sec % 5 == 0 or (page and await bot.is_in_call(page)):
                status = bot.bot_state.get("status", "unknown")
                prob = bot.bot_state.get("problem_type", "NONE")
                print(f"[{sec:02d}s] Task status: prob={prob} | Last task: {bot.task_history[-1]['summary'] if bot.task_history else 'None'}")
                
                if page and not page.is_closed():
                    shot_path = rf"C:\Users\Admin\.gemini\antigravity\scratch\atmani\shot_{sec}s.png"
                    try:
                        await page.screenshot(path=shot_path, timeout=3500)
                        print(f"[{sec:02d}s] Saved screenshot: {shot_path}")
                    except Exception as e:
                        print(f"[{sec:02d}s] Screenshot failed: {e}")
            
            if page and await bot.is_in_call(page):
                print(f"[{sec:02d}s] SUCCESS: BOT IS CONFIRMED INSIDE CALL!")
                shot_final = r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\shot_SUCCESS.png"
                try:
                    await page.screenshot(path=shot_final, timeout=5000)
                    print(f"Saved final success screenshot: {shot_final}")
                except Exception as e:
                    print(f"Could not save final screenshot: {e}")
                break
                
            if flow_task.done():
                print("Flow task finished.")
                break

        bot.stop_requested = True
        if not flow_task.done():
            flow_task.cancel()
        await browser.close()
        print("Test completed.")

if __name__ == "__main__":
    asyncio.run(main())
