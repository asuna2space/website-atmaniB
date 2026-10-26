import re

new_is_in_call = '''async def is_in_call(page: Page) -> bool:
    """Checks if the bot is actively inside the call (strictly in-call controls only)."""
    indicators = [
        'button[aria-label*="Leave call" i]',
        'button[aria-label*="Quitter l\\'appel" i]',
        'button[aria-label*="مغادرة المكالمة" i]',
        'button[aria-label*="إنهاء المكالمة" i]',
        'button[jsname="CQylAd"]',
        'button[data-panel-id="2"]',
        'button[aria-label*="Discuter avec" i]',
        'button[aria-label*="Chat with" i]',
        'button[aria-label*="محادثة مع" i]',
        'button[data-panel-id="1"]',
        'div[data-call-controls]'
    ]
    for ind in indicators:
        try:
            if await page.locator(ind).first.is_visible(timeout=300):
                return True
        except Exception:
            pass
    return False'''

new_click_join = '''async def click_join_button(page: Page) -> tuple[bool, str]:
    """Waits for preparation screen to settle and clicks the primary join button."""
    logger.info("Looking for Join button (waiting up to 25s for preparation to finish)...")
    
    start_wait = time.time()
    while time.time() - start_wait < 25:
        # 1. Dismiss modal popup if open (e.g. Continuer sans micro)
        try:
            modal_dismiss = page.locator(
                'button[jsname="IbE0S"], '
                'button:has-text("Continuer sans micro"), '
                'button:has-text("Continuer sans"), '
                ':has-text("المتابعة بدون استخدام الميكروفون والكاميرا"), '
                ':has-text("المتابعة بدون"), '
                ':has-text("Continue without microphone and camera"), '
                ':has-text("Continue without"), '
                'button:has-text("Compris"), '
                'button:has-text("Got it"), '
                'button:has-text("حسناً")'
            ).first
            if await modal_dismiss.is_visible(timeout=300):
                txt = (await modal_dismiss.inner_text()).strip().replace("\\n", " ")
                await modal_dismiss.click(force=True)
                logger.info(f"Dismissed mic/cam popup modal: '{txt}'")
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # 2. Enter student name if guest name field is present
        try:
            name_inp = page.locator('input[placeholder*="اسم"], input[aria-label*="اسم"], input[placeholder*="name" i]').first
            if await name_inp.is_visible(timeout=300):
                curr_val = await name_inp.input_value()
                if not curr_val:
                    st_name = bot_state.get("student_name") or STUDENT_NAME or "hazem"
                    await name_inp.fill(st_name)
                    logger.info(f"Entered student name in Join screen: '{st_name}'")
                    await asyncio.sleep(0.5)
        except Exception:
            pass

        # 3. Locate primary Join button
        btn = page.locator(
            'button:has-text("Participer à la réunion"), '
            'button:has-text("انضم الآن"), '
            'button:has-text("الانضمام الآن"), '
            'button:has-text("Join now"), '
            'button:has-text("Demander à participer"), '
            'button:has-text("Ask to join"), '
            'button:has-text("Switch here"), '
            'button:has-text("التبديل إلى هنا"), '
            'button:has-text("Join here too"), '
            'button:has-text("طلب الانضمام")'
        ).first
        try:
            if await btn.is_visible(timeout=600):
                txt = (await btn.inner_text()).strip().replace("\\n", " ")
                try:
                    await btn.click(force=True)
                except Exception:
                    await btn.evaluate("el => el.click()")
                logger.info(f"Successfully clicked join button: '{txt}'")
                return True, txt
        except Exception:
            pass

        # 4. Check if already inside call (strict in-call indicators only)
        if await is_in_call(page):
            logger.info("Confirmed inside call room!")
            return True, "تم الدخول المباشر"

        await asyncio.sleep(1)

    logger.warning("Join button was not found within 25 seconds.")
    return False, ""'''

paths = [
    r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\bot.py",
    r"C:\Users\Admin\.gemini\antigravity\scratch\google-meet-bot\bot.py",
    r"C:\Users\Admin\Desktop\last\bot.py"
]

pattern_is_in_call = re.compile(r"async def is_in_call\(page: Page\) -> bool:.*?return False", re.DOTALL)
pattern_click_join = re.compile(r"async def click_join_button\(page: Page\) -> tuple\[bool, str\]:.*?return False, \"\"", re.DOTALL)

for p in paths:
    with open(p, "r", encoding="utf-8") as f:
        content = f.read()

    m1 = pattern_is_in_call.search(content)
    if m1:
        content = content[:m1.start()] + new_is_in_call + content[m1.end():]
        print(f"Replaced is_in_call in {p}")
    else:
        print(f"pattern_is_in_call NOT found in {p}")

    m2 = pattern_click_join.search(content)
    if m2:
        content = content[:m2.start()] + new_click_join + content[m2.end():]
        print(f"Replaced click_join_button in {p}")
    else:
        print(f"pattern_click_join NOT found in {p}")

    with open(p, "w", encoding="utf-8") as f:
        f.write(content)

print("All bot.py files updated successfully!")
