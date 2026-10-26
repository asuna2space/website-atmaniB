with open('bot.py', 'r', encoding='utf-8') as f:
    text = f.read()

old_click = '''async def click_join_button(page: Page) -> tuple[bool, str]:
    """Clicks the primary 'Join now' (open meeting) or 'Ask to join' button and returns (success, clicked_label)."""
    logger.info("Looking for Join button...")
    join_selectors = [
        # Open Meet direct join (instant entry without host approval)
        ('button:has-text("Join now")', "Join now (انضمام فوري)"),
        ('button:has-text("الانضمام الآن")', "الانضمام الآن (انضمام فوري)"),
        ('button[jsname="Qx7uuf"]', "زر الانضمام المباشر (Qx7uuf)"),
        ('//button[contains(., "Join now")]', "Join now (xpath)"),
        ('//button[contains(., "الانضمام الآن")]', "الانضمام الآن (xpath)"),
        # Regular meetings requiring permission
        ('button:has-text("Ask to join")', "Ask to join (طلب إذن الانضمام)"),
        ('button:has-text("طلب الانضمام")', "طلب الانضمام (طلب إذن الانضمام)"),
        ('//button[contains(., "Ask to join")]', "Ask to join (xpath)"),
        ('//button[contains(., "طلب الانضمام")]', "طلب الانضمام (xpath)"),
        ('button[jsname="V67aGc"]', "زر طلب الانضمام (V67aGc)")
    ]
    for selector, label in join_selectors:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                logger.info(f"Clicked join button using: {selector} ({label})")
                return True, label
        except Exception:
            continue
    return False, ""'''

new_click = '''async def click_join_button(page: Page, timeout_seconds: int = 10) -> tuple[bool, str]:
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

if old_click in text:
    text = text.replace(old_click, new_click)
    with open('bot.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print("SUCCESS: Updated click_join_button to wait_for(visible)")
else:
    print("Old click_join_button pattern not found!")
