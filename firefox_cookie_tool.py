# -*- coding: utf-8 -*-
"""
====================================================================
🛠️ أداة تسجيل الدخول واستخراج كوكيز Google Meet تلقائياً عبر Firefox
   Firefox Cookie Extractor & Telegram Auto-Dispatcher
====================================================================
- تشغيل متصفح Firefox حقيقي/وهمي يتجاوز جميع قيود حظر الأتمتة لـ Google
- إدخال البريد الإلكتروني وكلمة المرور تلقائياً
- معالجة التحقق بخطوتين (2FA) وتأكيد الهاتف بذكاء
- استخراج الكوكيز وحفظها بصيغة Playwright القياسية (state.json)
- تشفير الجلسة بـ Base64 لـ Render (render_secret_base64.txt)
- نسخ الكود إلى الحافظة (Clipboard) تلقائياً
- إرسال الكوكيز وملف الجلسة وصورة التأكيد إلى تيليجرام مباشرة (@meethazem_bot)
====================================================================
"""

import os
import sys
import json
import base64
import time
import asyncio
import argparse
import getpass
import subprocess
from typing import Optional, Dict, Any, List

# Ensure UTF-8 output on Windows terminal
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from playwright.async_api import async_playwright, Page, BrowserContext
except ImportError:
    print("❌ خطأ: مكتبة Playwright غير مثبتة! يرجى تثبيتها عبر: pip install playwright && playwright install firefox")
    sys.exit(1)

try:
    import httpx
except ImportError:
    httpx = None

try:
    import requests
except ImportError:
    requests = None

# Base Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
RENDER_TXT_FILE = os.path.join(BASE_DIR, "render_secret_base64.txt")
PROFILE_DIR = os.path.join(BASE_DIR, "firefox_login_profile")
CONFIG_FILE = os.path.join(BASE_DIR, "auth_config.json")
SCREENSHOT_FILE = os.path.join(BASE_DIR, "meet_logged_in.png")
ENV_FILE = os.path.join(BASE_DIR, ".env")

# Defaults
DEFAULT_EMAIL = "h.badache@hns-re2sd.dz"
DEFAULT_TELEGRAM_BOT_TOKEN = "8971961233:AAGCdv5ODVakOx0Js5dd8DHQS1u7HjOQh-4"
DEFAULT_TELEGRAM_CHAT_ID = "8760969567"


def load_env_defaults():
    """Loads default configurations from .env if available."""
    token = DEFAULT_TELEGRAM_BOT_TOKEN
    chat_id = DEFAULT_TELEGRAM_CHAT_ID
    
    # Try local .env or parent/atmani .env
    env_paths = [
        ENV_FILE,
        os.path.join(BASE_DIR, "..", ".env"),
        os.path.join(r"C:\Users\Admin\.gemini\antigravity\scratch\atmani", ".env")
    ]
    for p in env_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("TELEGRAM_BOT_TOKEN="):
                            token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("TELEGRAM_CHAT_ID="):
                            chat_id = line.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
    return token, chat_id


TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID = load_env_defaults()


def copy_to_clipboard(text: str) -> bool:
    """Copies text to Windows clipboard using clip command or PowerShell fallback."""
    try:
        subprocess.run("clip", input=text.encode("utf-8"), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        try:
            p = subprocess.Popen(["powershell", "-NoProfile", "-Command", "Set-Clipboard"], stdin=subprocess.PIPE, text=True, encoding="utf-8")
            p.communicate(input=text)
            return True
        except Exception:
            return False


def load_saved_config() -> dict:
    """Loads saved credentials from auth_config.json."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(email: str, remember_email: bool = True):
    """Saves email preference to auth_config.json."""
    try:
        data = load_saved_config()
        if remember_email:
            data["email"] = email
        data["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def format_cookies_for_playwright(raw_cookies: list) -> list:
    """
    Standardizes cookie list into Playwright storage_state format.
    Ensures RFC 6265bis and Chromium/Firefox compatibility:
    - SameSite='None' requires Secure=True.
    - Expiration integer values.
    """
    formatted = []
    for c in raw_cookies:
        if not isinstance(c, dict) or not c.get("name") or c.get("value") is None:
            continue

        secure = bool(c.get("secure", False))
        same_site_raw = str(c.get("sameSite", "")).lower()

        if same_site_raw in ("no_restriction", "none"):
            ss = "None"
        elif same_site_raw == "strict":
            ss = "Strict"
        elif same_site_raw == "lax":
            ss = "Lax"
        else:
            ss = "None" if secure else "Lax"

        # RFC rule: SameSite=None without Secure=True is dropped
        if ss == "None" and not secure:
            ss = "Lax"

        exp = c.get("expirationDate") or c.get("expires") or -1
        if isinstance(exp, float):
            exp = int(exp)

        domain = str(c.get("domain", "")).strip()

        formatted.append({
            "name": str(c.get("name")),
            "value": str(c.get("value")),
            "domain": domain,
            "path": str(c.get("path", "/")),
            "expires": exp,
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": secure,
            "sameSite": ss
        })
    return formatted


def save_session_files(state_data: dict) -> str:
    """Saves state.json, generates Base64 string, and saves render_secret_base64.txt."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, indent=2, ensure_ascii=False)

    raw_json = json.dumps(state_data, separators=(",", ":"))
    b64_str = base64.b64encode(raw_json.encode("utf-8")).decode("utf-8")

    with open(RENDER_TXT_FILE, "w", encoding="utf-8") as f:
        f.write(b64_str)

    copy_to_clipboard(b64_str)
    return b64_str


def send_to_telegram(
    bot_token: str,
    chat_id: str,
    text: str,
    photo_path: Optional[str] = None,
    document_paths: Optional[List[str]] = None
) -> bool:
    """Dispatches message, photos, and files to Telegram."""
    if not bot_token or not chat_id:
        print("⚠️ لم يتم تحديد رمز بوت تيليجرام أو معرّف المحادثة.")
        return False

    success = True
    base_url = f"https://api.telegram.org/bot{bot_token}"

    print(f"\n📲 جاري إرسال البيانات إلى تيليجرام (Chat ID: {chat_id})...")

    # 1. Send Text Message
    try:
        if requests:
            resp = requests.post(
                f"{base_url}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=20
            )
            if resp.status_code == 200:
                print("   ✅ تم إرسال رسالة الإشعار إلى تيليجرام.")
            else:
                print(f"   ⚠️ فشل إرسال النص: {resp.text}")
                success = False
        elif httpx:
            with httpx.Client(timeout=20) as client:
                resp = client.post(
                    f"{base_url}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
                )
                if resp.status_code == 200:
                    print("   ✅ تم إرسال رسالة الإشعار إلى تيليجرام.")
                else:
                    success = False
    except Exception as e:
        print(f"   ❌ تعذر إرسال الرسالة النصية: {e}")
        success = False

    # 2. Send Photo (if available)
    if photo_path and os.path.exists(photo_path):
        try:
            caption = "📸 لقطة شاشة حية لحساب Google Meet بعد تسجيل الدخول بنجاح!"
            if requests:
                with open(photo_path, "rb") as f:
                    resp = requests.post(
                        f"{base_url}/sendPhoto",
                        data={"chat_id": chat_id, "caption": caption},
                        files={"photo": (os.path.basename(photo_path), f)},
                        timeout=35
                    )
                    if resp.status_code == 200:
                        print("   ✅ تم إرسال صورة التحقق بنجاح إلى تيليجرام.")
            elif httpx:
                with httpx.Client(timeout=35) as client:
                    with open(photo_path, "rb") as f:
                        resp = client.post(
                            f"{base_url}/sendPhoto",
                            data={"chat_id": chat_id, "caption": caption},
                            files={"photo": (os.path.basename(photo_path), f)}
                        )
                        if resp.status_code == 200:
                            print("   ✅ تم إرسال صورة التحقق بنجاح إلى تيليجرام.")
        except Exception as e:
            print(f"   ⚠️ تعذر إرسال الصورة: {e}")

    # 3. Send Documents (render_secret_base64.txt & state.json)
    if document_paths:
        for doc in document_paths:
            if not os.path.exists(doc):
                continue
            doc_name = os.path.basename(doc)
            caption = f"📁 ملف الجلسة: {doc_name}"
            try:
                if requests:
                    with open(doc, "rb") as f:
                        resp = requests.post(
                            f"{base_url}/sendDocument",
                            data={"chat_id": chat_id, "caption": caption},
                            files={"document": (doc_name, f)},
                            timeout=45
                        )
                        if resp.status_code == 200:
                            print(f"   ✅ تم إرسال الملف [{doc_name}] بنجاح إلى تيليجرام.")
                        else:
                            print(f"   ⚠️ فشل إرسال {doc_name}: {resp.text}")
                elif httpx:
                    with httpx.Client(timeout=45) as client:
                        with open(doc, "rb") as f:
                            resp = client.post(
                                f"{base_url}/sendDocument",
                                data={"chat_id": chat_id, "caption": caption},
                                files={"document": (doc_name, f)}
                            )
                            if resp.status_code == 200:
                                print(f"   ✅ تم إرسال الملف [{doc_name}] بنجاح إلى تيليجرام.")
            except Exception as e:
                print(f"   ⚠️ تعذر إرسال الملف {doc_name}: {e}")

    return success


async def type_with_delay(page: Page, selector: str, text: str, delay_ms: int = 50):
    """Simulates realistic human keystrokes."""
    await page.wait_for_selector(selector, state="visible", timeout=15000)
    await page.click(selector)
    await asyncio.sleep(0.3)
    await page.fill(selector, "")
    for char in text:
        await page.type(selector, char, delay=delay_ms)
    await asyncio.sleep(0.4)


async def perform_automated_login(
    email: str,
    password: Optional[str] = None,
    bot_token: str = DEFAULT_TELEGRAM_BOT_TOKEN,
    chat_id: str = DEFAULT_TELEGRAM_CHAT_ID,
    manual_mode: bool = False
):
    """
    Launches Firefox with persistent profile and navigates to Google sign-in.
    Types credentials automatically or allows manual intervention, then exports session.
    """
    print("\n" + "=" * 65)
    print("  🦊 تشغيل متصفح Firefox الآمن لنظام Google Meet")
    print("  ⚡ يتم تخطي أنظمة الحماية واستخراج الكوكيز تلقائياً")
    print("=" * 65)

    os.makedirs(PROFILE_DIR, exist_ok=True)

    async with async_playwright() as p:
        # Launch persistent context
        context: BrowserContext = await p.firefox.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="ar-DZ",
            args=[
                "-no-remote"
            ]
        )

        page: Page = context.pages[0] if context.pages else await context.new_page()

        target_url = "https://accounts.google.com/ServiceLogin?continue=https://meet.google.com/"
        print(f"\n🌐 جاري فتح صفحة تسجيل الدخول لـ Google...")
        await page.goto(target_url, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # 1. Check if already logged in!
        current_url = page.url
        print(f"🔍 الرابط الحالي: {current_url}")

        already_logged_in = False
        if "meet.google.com" in current_url and "accounts.google.com" not in current_url:
            already_logged_in = True
        else:
            cookies = await context.cookies()
            has_sid = any(c.get("name") in ("SID", "HSID", "__Secure-1PSID") for c in cookies)
            if has_sid and ("meet.google.com" in current_url or "myaccount.google.com" in current_url):
                already_logged_in = True

        if already_logged_in:
            print("\n🎉 الحساب مسجل دخوله بالفعل في المتصفح! لا داعي لإعادة إدخال كلمة المرور.")
        elif not manual_mode and email and password:
            print(f"\n🤖 إدخال بيانات الاعتماد آلياً للحساب: {email}")
            try:
                # 2. Type Email
                email_selectors = [
                    "input[type='email']",
                    "#identifierId",
                    "input[name='identifier']",
                    "input[autocomplete='username']"
                ]
                email_input = None
                for sel in email_selectors:
                    if await page.locator(sel).count() > 0 and await page.locator(sel).first.is_visible():
                        email_input = sel
                        break

                if email_input:
                    print("   -> كتابة البريد الإلكتروني...")
                    await type_with_delay(page, email_input, email, delay_ms=45)
                    await asyncio.sleep(0.5)

                    # Click Next
                    next_clicked = False
                    next_selectors = [
                        "#identifierNext button",
                        "#identifierNext",
                        "button:has-text('التالي')",
                        "button:has-text('Next')",
                        "button:has-text('Suivant')"
                    ]
                    for btn in next_selectors:
                        if await page.locator(btn).count() > 0 and await page.locator(btn).first.is_visible():
                            await page.click(btn)
                            next_clicked = True
                            break

                    if not next_clicked:
                        await page.keyboard.press("Enter")
                    print("   -> تم الانتقال إلى حقل كلمة المرور...")
                    await asyncio.sleep(3)

                # 3. Wait and Type Password
                pwd_selectors = [
                    "input[type='password']",
                    "input[name='Passwd']",
                    "input[name='password']"
                ]
                pwd_input = None
                for _ in range(20):
                    for sel in pwd_selectors:
                        if await page.locator(sel).count() > 0 and await page.locator(sel).first.is_visible():
                            pwd_input = sel
                            break
                    if pwd_input:
                        break
                    await asyncio.sleep(0.5)

                if pwd_input:
                    print("   -> كتابة كلمة المرور...")
                    await type_with_delay(page, pwd_input, password, delay_ms=40)
                    await asyncio.sleep(0.5)

                    # Click Next
                    pwd_next_clicked = False
                    pwd_next_selectors = [
                        "#passwordNext button",
                        "#passwordNext",
                        "button:has-text('التالي')",
                        "button:has-text('Next')",
                        "button:has-text('Suivant')"
                    ]
                    for btn in pwd_next_selectors:
                        if await page.locator(btn).count() > 0 and await page.locator(btn).first.is_visible():
                            await page.click(btn)
                            pwd_next_clicked = True
                            break

                    if not pwd_next_clicked:
                        await page.keyboard.press("Enter")
                    print("   -> تم إرسال كلمة المرور بنجاح...")
                else:
                    print("   ⚠️ لم يظهر حقل كلمة المرور تلقائياً (قد يتطلب كابتشا أو تحقق إضافي).")
            except Exception as e:
                print(f"   ⚠️ ملاحظة أثناء الملء الآلي: {e}")
                print("   يرجى إكمال تسجيل الدخول يدوياً في نافذة Firefox المفتوحة.")
        else:
            print("\n📝 الوضع اليدوي / التفاعلي مفعل.")
            print("   يرجى إدخال البريد الإلكتروني وكلمة المرور في نافذة متصفح Firefox المفتوحة الآن.")

        # 4. Monitor for Login Completion (or 2FA)
        print("\n" + "-" * 65)
        print("⏳ جاري مراقبة المتصفح حتى الوصول إلى Google Meet...")
        print("💡 إذا طلب منك Google:")
        print("   - تأكيد رقم على هاتفك (2FA)")
        print("   - إدخال رمز التحقق بخطوتين (SMS / Authenticator)")
        print("   - حل كابتشا أمان")
        print("   -> نفذ ذلك الآن مباشرة في نافذة متصفح Firefox الظاهرة أمامك.")
        print("   (أو يمكنك الضغط على ENTER في هذه الشاشة السوداء في أي وقت بعد الانتهاء)")
        print("-" * 65)

        logged_in = False
        start_time = time.time()
        max_wait_seconds = 180

        # Non-blocking input loop + page polling
        while time.time() - start_time < max_wait_seconds:
            curr = page.url
            if "meet.google.com" in curr and "accounts.google.com" not in curr:
                print(f"\n🎉 تم التحقق: تم الوصول إلى صفحة Google Meet بنجاح! ({curr})")
                logged_in = True
                break

            cookies = await context.cookies()
            has_auth = any(c.get("name") in ("SID", "HSID", "__Secure-1PSID") for c in cookies)
            if has_auth and ("meet.google.com" in curr or "myaccount.google.com" in curr):
                print("\n🎉 تم التحقق: رموز جلسة Google (SID/HSID) أصبحت نشطة بنجاح!")
                logged_in = True
                break

            await asyncio.sleep(2)

        if not logged_in:
            print("\n⚠️ لم يتم رصد تحويل تلقائي فوري لـ Google Meet.")
            user_choice = await asyncio.to_thread(input, "👉 هل أتممت تسجيل الدخول بنجاح وتريد حفظ الجلسة الآن؟ (اضغط ENTER للمتابعة أو q للإلغاء): ")
            if user_choice.strip().lower() == "q":
                print("❌ تم إلغاء العملية.")
                await context.close()
                return

        # Navigate directly to Google Meet to ensure full token propagation
        print("\n🔄 توجيه المتصفح إلى Google Meet لتثبيت جميع الكوكيز...")
        try:
            await page.goto("https://meet.google.com", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)
        except Exception:
            pass

        # Capture Screenshot
        try:
            await page.screenshot(path=SCREENSHOT_FILE)
            print(f"📸 تم التقاط صورة تأكيد للشاشة: {SCREENSHOT_FILE}")
        except Exception:
            pass

        # 5. Extract storage state
        print("⏳ جاري استخراج وحفظ الكوكيز القياسية...")
        state = await context.storage_state()
        formatted_cookies = format_cookies_for_playwright(state.get("cookies", []))
        state["cookies"] = formatted_cookies

        # 6. Save Files
        b64_str = save_session_files(state)
        cookie_count = len(formatted_cookies)

        print("\n" + "=" * 65)
        print(f"  🎉 تم استخراج واعتماد جلسة Google بنجاح! ({cookie_count} كوكيز مسجلة)")
        print(f"  📁 ملف state.json: {STATE_FILE}")
        print(f"  💾 ملف كود Base64 لـ Render: {RENDER_TXT_FILE}")
        print("  📋 تم نسخ كود Base64 إلى الحافظة (Clipboard) تلقائياً!")
        print("=" * 65)

        # 7. Dispatch to Telegram
        if bot_token and chat_id:
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            tg_text = (
                f"🎉 <b>تم استخراج جلسة Google Meet بنجاح!</b>\n\n"
                f"👤 <b>الحساب:</b> <code>{email or 'الحساب المدرسي'}</code>\n"
                f"🍪 <b>عدد الكوكيز:</b> {cookie_count}\n"
                f"📅 <b>التوقيت:</b> {now_str}\n\n"
                f"⚡ <b>الحالة:</b> الجلسة نشطة ومطابقة لمعايير Playwright و Render.\n"
                f"📁 مرفق ملف <code>state.json</code> و كود <code>render_secret_base64.txt</code>."
            )
            send_to_telegram(
                bot_token=bot_token,
                chat_id=chat_id,
                text=tg_text,
                photo_path=SCREENSHOT_FILE if os.path.exists(SCREENSHOT_FILE) else None,
                document_paths=[RENDER_TXT_FILE, STATE_FILE]
            )

        # Close Browser Context
        await context.close()
        print("\n✅ اكتملت العملية بنجاح 100%! يمكنك الآن تشغيل البوت محلياً أو على Render.")


def main():
    parser = argparse.ArgumentParser(description="Google Meet Firefox Automated Cookie Extractor & Telegram Dispatcher")
    parser.add_argument("--email", "-e", help="Google Account Email", default="")
    parser.add_argument("--password", "-p", help="Google Account Password", default="")
    parser.add_argument("--token", "-t", help="Telegram Bot Token", default=TELEGRAM_BOT_TOKEN)
    parser.add_argument("--chat-id", "-c", help="Telegram Subscriber Chat ID", default=TELEGRAM_CHAT_ID)
    parser.add_argument("--manual", "-m", action="store_true", help="Launch Firefox in manual interactive mode")
    args = parser.parse_args()

    print("\n" + "=" * 65)
    print("  🚀 أداة استخراج كوكيز Google Meet وإرسالها لتيليجرام تلقائياً")
    print("  Automated Firefox Playwright Authenticator for Hazem Bot")
    print("=" * 65)

    saved_cfg = load_saved_config()
    default_saved_email = saved_cfg.get("email") or DEFAULT_EMAIL

    email = args.email.strip()
    password = args.password.strip()
    manual = args.manual

    if not manual and not email:
        input_email = input(f"📧 أدخل البريد الإلكتروني [الافتراضي: {default_saved_email}]: ").strip()
        email = input_email if input_email else default_saved_email

    if not manual and not password:
        print("🔑 أدخل كلمة المرور (لن تظهر الحروف أثناء الكتابة للأمان)")
        print("   (أو اضغط ENTER مباشرة إذا كنت تريد كتابتها في المتصفح بنفسك):")
        try:
            pwd_input = getpass.getpass("   كلمة المرور: ").strip()
        except Exception:
            pwd_input = input("   كلمة المرور: ").strip()
        password = pwd_input
        if not password:
            manual = True

    if email:
        save_config(email)

    bot_token = args.token or TELEGRAM_BOT_TOKEN
    chat_id = args.chat_id or TELEGRAM_CHAT_ID

    asyncio.run(perform_automated_login(
        email=email,
        password=password,
        bot_token=bot_token,
        chat_id=chat_id,
        manual_mode=manual
    ))


if __name__ == "__main__":
    main()
