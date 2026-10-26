import re

new_check_gmail = '''async def check_gmail_for_meet_links(browser) -> tuple[bool, str, Optional[str]]:
    """
    Checks the student's Gmail inbox for incoming Google Meet links.
    Returns: (success, message, new_link_or_None)
    """
    global bot_state
    bot_state["gmail_last_check"] = time.strftime("%H:%M:%S")

    # 1. First check IMAP if configured in .env
    imap_user = os.getenv("GMAIL_IMAP_USER", "").strip()
    imap_pass = os.getenv("GMAIL_IMAP_PASSWORD", "").strip()
    if imap_user and imap_pass:
        try:
            meet_regex = re.compile(r"https?://meet\\.google\\.com/[a-zA-Z0-9_-]+")
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(imap_user, imap_pass)
            mail.select("INBOX")
            status, messages = mail.search(None, '(UNSEEN)')
            if status == "OK" and messages[0]:
                for num in messages[0].split()[-5:]:
                    res, data = mail.fetch(num, '(RFC822)')
                    for response_part in data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        body += str(part.get_payload(decode=True))
                            else:
                                body = str(msg.get_payload(decode=True))

                            match = meet_regex.search(body)
                            if match:
                                mail.close()
                                mail.logout()
                                return True, f"✅ تم العثور على رابط في البريد: {match.group(0)}", match.group(0)
            mail.close()
            mail.logout()
        except Exception as ie:
            logger.debug(f"IMAP check note: {ie}")

    # 2. Browser-based Gmail check using state.json
    storage_state_path = get_storage_state()
    if not storage_state_path or not os.path.exists(storage_state_path):
        return False, "⚠️ لم يتم استيراد كوكيز Google بعد. يرجى استيراد كوكيز حساب المدرسة من الخيار 4.", None

    if not browser:
        return False, "⚠️ المتصفح قيد التهيئة، يرجى الانتظار...", None

    try:
        ctx = await browser.new_context(
            storage_state=storage_state_path,
            locale=BROWSER_LOCALE,
            user_agent=MODERN_CHROME_UA
        )
        gpage = await ctx.new_page()
        try:
            # Navigate to Inbox directly (much faster and reliable)
            await gpage.goto("https://mail.google.com/mail/u/0/#inbox", wait_until="domcontentloaded", timeout=25000)

            if "accounts.google.com" in gpage.url:
                bot_state["google_account_info"] = "❌ كوكيز منتهية"
                return False, "❌ جلسة Google منتهية الصلاحية. يرجى استخراج كوكيز جديدة ولصقها في الخيار 4.", None
            else:
                bot_state["google_account_info"] = "✅ حساب المدرسة متصل ونشط"

            # Wait for email rows to render
            try:
                await gpage.wait_for_selector("tr.zA", timeout=12000)
            except Exception:
                pass

            meet_regex = re.compile(r"meet\\.google\\.com/([a-zA-Z]{3}-[a-zA-Z]{4}-[a-zA-Z]{3})")
            rows = await gpage.locator("tr.zA").all()
            
            # Check the top 10 newest emails in the inbox
            for row in rows[:10]:
                try:
                    txt = await row.inner_text()
                    match = meet_regex.search(txt)
                    if match:
                        code = match.group(1).lower()
                        found_url = f"https://meet.google.com/{code}"
                        bot_state["gmail_last_detected_url"] = found_url
                        return True, f"✅ تم رصد رابط Google Meet في البريد: {found_url}", found_url
                except Exception:
                    continue

            # Fallback check entire content
            content = await gpage.content()
            matches = meet_regex.findall(content)
            if matches:
                found_url = f"https://meet.google.com/{matches[0].lower()}"
                bot_state["gmail_last_detected_url"] = found_url
                return True, f"✅ تم رصد رابط Google Meet في البريد: {found_url}", found_url

            return True, "✅ تم فحص البريد: لا توجد رسائل جديدة تحتوي على روابط Google Meet حالياً.", None
        finally:
            await ctx.close()
    except Exception as ge:
        return False, f"⚠️ حدث خطأ أثناء فحص بريد Gmail: {ge}", None'''

new_gmail_loop = '''async def gmail_monitor_loop(browser):
    """
    Dedicated background worker that monitors the student's Gmail for incoming Google Meet links.
    When a new link is detected, it automatically joins the meeting and notifies the user on Telegram.
    """
    global bot_state, new_meeting_event, MEET_URL, STUDENT_NAME, current_page_ref
    logger.info("Starting Gmail Meet Link Watcher background loop...")

    seen_meet_links: Set[str] = set()
    if MEET_URL:
        seen_meet_links.add(MEET_URL.strip().lower())

    while True:
        await asyncio.sleep(30)  # Check every 30 seconds

        # Only check if enabled
        if not bot_state.get("gmail_monitor_enabled", True):
            continue

        # If currently inside an active call, don't interrupt
        if current_page_ref and not current_page_ref.is_closed():
            try:
                if await is_in_call(current_page_ref):
                    continue
            except Exception:
                pass

        storage_state_path = get_storage_state()
        if not storage_state_path or not os.path.exists(storage_state_path):
            continue

        try:
            is_ok, msg, found_link = await check_gmail_for_meet_links(browser)
            if found_link and found_link.lower() not in seen_meet_links:
                seen_meet_links.add(found_link.lower())
                bot_state["gmail_last_detected_url"] = found_link
                logger.info(f"📧 [GMAIL WATCHER] Auto-joining meeting detected in Gmail: {found_link}")

                record_task(
                    "رصد رابط من Gmail",
                    "success",
                    f"📧 تم رصد رابط جديد من إيميل المدرسة: {found_link}",
                    "جاري الانضمام تلقائياً بالحساب المدرسي المعتمد (@hns-re2sd.dz)..."
                )

                sub_id = telegram_subscriber_chat_id or load_telegram_subscriber()
                if sub_id:
                    tg_msg = (
                        "📧 <b>تم اكتشاف رابط Google Meet جديد في بريد Gmail!</b>\\n\\n"
                        f"🔗 <b>الرابط:</b> {found_link}\\n"
                        "👤 <b>الحساب:</b> حساب المدرسة الرسمي (@hns-re2sd.dz)\\n\\n"
                        "🚀 <i>جاري الانضمام للحصة فوراً وتلقائياً...</i>"
                    )
                    await send_telegram_message(sub_id, tg_msg)

                # Automatically start meeting!
                start_or_restart_meeting(found_link, STUDENT_NAME or "hazem")
        except Exception as e:
            logger.debug(f"Error in gmail_monitor_loop: {e}")'''

paths = [
    r"C:\Users\Admin\.gemini\antigravity\scratch\atmani\bot.py",
    r"C:\Users\Admin\.gemini\antigravity\scratch\google-meet-bot\bot.py",
    r"C:\Users\Admin\Desktop\last\bot.py"
]

pattern_check = re.compile(r"async def check_gmail_for_meet_links\(browser\).*?return False, f\"⚠️ حدث خطأ أثناء فحص بريد Gmail: \{ge\}\", None", re.DOTALL)
pattern_loop = re.compile(r"async def gmail_monitor_loop\(browser\):.*?logger\.debug\(f\"Error in gmail_monitor_loop: \{e\}\"\)", re.DOTALL)

for p in paths:
    with open(p, "r", encoding="utf-8") as f:
        content = f.read()

    m1 = pattern_check.search(content)
    if m1:
        content = content[:m1.start()] + new_check_gmail + content[m1.end():]
        print(f"Replaced check_gmail_for_meet_links in {p}")
    else:
        print(f"pattern_check NOT found in {p}")

    m2 = pattern_loop.search(content)
    if m2:
        content = content[:m2.start()] + new_gmail_loop + content[m2.end():]
        print(f"Replaced gmail_monitor_loop in {p}")
    else:
        print(f"pattern_loop NOT found in {p}")

    with open(p, "w", encoding="utf-8") as f:
        f.write(content)

print("Gmail monitoring fix applied across all files!")
