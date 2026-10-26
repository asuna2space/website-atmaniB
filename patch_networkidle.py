with open('bot.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update page.goto wait_until to networkidle
text = text.replace(
    'await page.goto(MEET_URL, wait_until="domcontentloaded", timeout=60000)',
    'await page.goto(MEET_URL, wait_until="networkidle", timeout=60000)'
)

# 2. Update click_join_button to use the proven working locator with 5s timeout
old_click_func = '''async def click_join_button(page: Page, timeout_seconds: int = 10) -> tuple[bool, str]:
    """Waits for and clicks the primary 'Join now' or 'Ask to join' button as soon as it renders."""
    logger.info(f"Waiting up to {timeout_seconds}s for Join button to render...")
    join_selector = (
        'button:has-text("Join now"), '
        'button:has-text("الانضمام الآن"), '
        'button:has-text("Ask to join"), '
        'button:has-text("طلب الانضمام"), '
        'button[jsname="Qx7uuf"], '
        'button[jsname="V67aGc"]'
    )
    try:
        btn = page.locator(join_selector).first
        await btn.wait_for(state="visible", timeout=timeout_seconds * 1000)
        txt = (await btn.inner_text()).strip().replace("\\n", " ")
        await btn.click()
        logger.info(f"Successfully clicked join button: '{txt}'")
        return True, txt
    except Exception as e:
        logger.warning(f"Join button not visible within {timeout_seconds}s: {e}")
        return False, ""'''

new_click_func = '''async def click_join_button(page: Page) -> tuple[bool, str]:
    """Clicks the primary 'Join now' or 'Ask to join' button."""
    logger.info("Looking for Join button...")
    try:
        btn = page.locator("button:has-text('Join now'), button:has-text('الانضمام الآن'), button:has-text('Ask to join'), button:has-text('طلب الانضمام')").first
        if await btn.is_visible(timeout=6000):
            txt = (await btn.inner_text()).strip().replace("\\n", " ")
            await btn.click()
            logger.info(f"Successfully clicked join button: '{txt}'")
            return True, txt
    except Exception as e:
        logger.warning(f"Error clicking join button: {e}")
    return False, ""'''

text = text.replace(old_click_func, new_click_func)

# 3. Clean error screen selectors to remove false positives
old_errors = '''async def check_for_error_screen(page: Page) -> tuple[bool, str]:
    """Checks if Google Meet displays an error or return-to-home screen and returns (has_error, error_text)."""
    error_selectors = [
        ('text="Sorry, we\'ve encountered a problem"', "عذراً، حدثت مشكلة (حظر خوادم Render السحابية)"),
        ('text="عذراً، حدثت مشكلة"', "عذراً، حدثت مشكلة (حظر خوادم Render السحابية)"),
        ('text="Returning to home screen"', "الرجوع إلى الشاشة الرئيسية (سقوط الاتصال)"),
        ('text="الرجوع إلى الشاشة الرئيسية"', "الرجوع إلى الشاشة الرئيسية (سقوط الاتصال)"),
        ('text="You can\'t join this video call"', "لا يمكنك الانضمام إلى مكالمة الفيديو هذه"),
        ('text="لا يُمكنك الانضمام إلى مكالمة الفيديو هذه"', "لا يمكنك الانضمام إلى مكالمة الفيديو هذه")
    ]'''

new_errors = '''async def check_for_error_screen(page: Page) -> tuple[bool, str]:
    """Checks if Google Meet displays an error screen."""
    # If in call or join button visible, definitely no error
    if await is_in_call(page):
        return False, ""
    error_selectors = [
        ('text="Sorry, we\'ve encountered a problem"', "عذراً، حدثت مشكلة (حظر خوادم Render السحابية)"),
        ('text="عذراً، حدثت مشكلة"', "عذراً، حدثت مشكلة (حظر خوادم Render السحابية)"),
        ('text="You can\'t join this video call"', "لا يمكنك الانضمام إلى مكالمة الفيديو هذه (الحساب غير مصرح له)"),
        ('text="لا يُمكنك الانضمام إلى مكالمة الفيديو هذه"', "لا يمكنك الانضمام إلى مكالمة الفيديو هذه (الحساب غير مصرح له)"),
        ('text="Someone in the call denied your request"', "المعلم رفض طلب الانضمام")
    ]'''

text = text.replace(old_errors, new_errors)

with open('bot.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Applied networkidle & button wait patch!")
