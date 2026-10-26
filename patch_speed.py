with open('bot.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace slow disable_mic_and_camera
old_disable = '''async def disable_mic_and_camera(page: Page):
    """Mutes mic and camera using universal shortcuts and buttons."""
    logger.info("Muting mic and turning off camera...")
    try:
        await page.keyboard.press("Control+d")
        await page.keyboard.press("Control+e")
    except Exception as e:
        logger.warning(f"Error sending shortcut keys: {e}")

    try:
        mute_buttons = await page.locator(
            'div[role="button"][data-is-muted="false"], '
            'div[role="button"][aria-label*="mic" i], '
            'div[role="button"][aria-label*="ميكروفون" i], '
            'div[role="button"][aria-label*="كاميرا" i], '
            'div[role="button"][aria-label*="camera" i]'
        ).all()
        for btn in mute_buttons:
            try:
                if await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(0.2)
            except Exception:
                pass
    except Exception:
        pass'''

new_disable = '''async def disable_mic_and_camera(page: Page):
    """Mutes mic and camera using universal shortcuts."""
    try:
        await page.keyboard.press("Control+d")
        await page.keyboard.press("Control+e")
    except Exception:
        pass'''

# Replace the slow sequence in loop
old_seq = '''                # المهمة 5: التعامل مع النوافذ وكتم الصوت والفيديو
                record_task("المهمة 5: كتم الصوت وإغلاق الكاميرا", "info", "جاري كتم المايك والكاميرا وإغلاق التنبيهات...", "اختصارات Ctrl+d و Ctrl+e")
                await dismiss_popups_and_banners(page)
                await wait_for_ready_screen(page, timeout_seconds=12)
                await dismiss_popups_and_banners(page)
                await fill_guest_name_if_present(page, STUDENT_NAME)
                await disable_mic_and_camera(page)
                await dismiss_popups_and_banners(page)
                record_task("المهمة 5: كتم الصوت وإغلاق الكاميرا", "success", "تم إغلاق المايك والكاميرا وتخطي النوافذ التنبيهية بنجاح.")'''

new_seq = '''                # المهمة 5: التعامل مع النوافذ وكتم الصوت والفيديو (سريع جداً < ثانية)
                record_task("المهمة 5: كتم الصوت وإغلاق الكاميرا", "info", "جاري كتم المايك والكاميرا وتخطي النوافذ التنبيهية فوراً...")
                await fast_dismiss_and_mute(page)
                record_task("المهمة 5: كتم الصوت وإغلاق الكاميرا", "success", "تم إغلاق المايك والكاميرا وتخطي النوافذ التنبيهية بنجاح.")'''

text = text.replace(old_disable, new_disable)
text = text.replace(old_seq, new_seq)

with open('bot.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Patch applied successfully!")
