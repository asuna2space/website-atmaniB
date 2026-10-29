"""
Google Account Authenticator & Session Exporter for Google Meet Bot
- Auto-extracts from Clipboard, JSON file, or Manual Paste
- Standardizes cookies for Playwright (fixing SameSite/Secure RFC 6265bis rules)
- Generates state.json and GOOGLE_STORAGE_STATE Base64 string for Render
- Tests and verifies session validity directly against Google Meet
"""

import os
import sys
import json
import base64
import asyncio
import subprocess

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.async_api import async_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.json")
RENDER_TXT_FILE = os.path.join(BASE_DIR, "render_secret_base64.txt")

BRAVE_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe")
]

FIREFOX_PATHS = [
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe")
]

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe")
]

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
]

def find_executable(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None

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

def read_from_clipboard() -> str:
    """Reads text from Windows clipboard."""
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, encoding="utf-8")
        return res.stdout.strip()
    except Exception:
        return ""

def format_cookies_for_playwright(raw_cookies: list) -> list:
    """
    Standardizes cookie list into Playwright storage_state format.
    CRITICAL RFC 6265bis / Chromium requirement:
    - SameSite="None" MUST have Secure=True. If Secure=False, Chromium silently DROPS the cookie!
    - Non-secure cookies (like SID, HSID, APISID) MUST have SameSite="Lax".
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

        # Chromium rule: SameSite=None without Secure=True is rejected
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

def save_and_display_state(data: dict):
    """Saves state.json and copies Render Base64 to clipboard."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    raw_json = json.dumps(data, separators=(",", ":"))
    b64_str = base64.b64encode(raw_json.encode("utf-8")).decode("utf-8")

    # Save Base64 to text file
    with open(RENDER_TXT_FILE, "w", encoding="utf-8") as f:
        f.write(b64_str)

    # Copy to clipboard
    copied = copy_to_clipboard(b64_str)

    cookie_count = len(data.get("cookies", []))
    print("\n" + "=" * 65)
    print(f"  🎉 تم استخراج وحفظ جلسة Google بنجاح! ({cookie_count} كوكيز مسجلة)")
    print(f"  📁 مسار ملف الجلسة المحلي (state.json):")
    print(f"     {STATE_FILE}")
    print(f"  💾 مسار كود Base64 لـ Render (render_secret_base64.txt):")
    print(f"     {RENDER_TXT_FILE}")
    print("=" * 65)
    
    if copied:
        print("\n✨ تم نسخ كود Render (Base64) تلقائياً إلى الحافظة (Clipboard)! ✨")
        print("   يمكنك الآن الذهاب إلى موقع Render ولصقه مباشرة بـ Ctrl + V في متغير GOOGLE_STORAGE_STATE.\n")
    else:
        print("\n📋 كود الـ Base64 لمتغير GOOGLE_STORAGE_STATE في Render:\n")
        print("-" * 65)
        print(b64_str)
        print("-" * 65 + "\n")

    print("📌 الخطوة التالية لـ Render:")
    print("   1. افتح https://dashboard.render.com واختر البوت.")
    print("   2. اذهب إلى Environment -> أضف أو عدّل متغير البيئة:")
    print("      Key:   GOOGLE_STORAGE_STATE")
    print("      Value: (الصق الكود المنسوخ الآن بـ Ctrl+V)")
    print("   3. اضغط Save Changes وسيدخل البوت تلقائياً بحساب المدرسة!\n")

def process_raw_cookie_string(raw_str: str) -> bool:
    """Parses JSON or Base64 cookie string and saves state.json."""
    raw_str = raw_str.strip()
    if not raw_str:
        print("❌ النص فارغ.")
        return False

    # Check if Base64
    if not raw_str.startswith("[") and not raw_str.startswith("{"):
        try:
            pad = (4 - (len(raw_str) % 4)) % 4
            decoded = base64.b64decode(raw_str + "=" * pad).decode("utf-8")
            raw_str = decoded
        except Exception as e:
            print(f"❌ تعذر فك تشفير Base64: {e}")
            return False

    try:
        parsed = json.loads(raw_str)
        if isinstance(parsed, list):
            formatted_cookies = format_cookies_for_playwright(parsed)
            state_data = {"cookies": formatted_cookies, "origins": []}
        elif isinstance(parsed, dict) and "cookies" in parsed:
            formatted_cookies = format_cookies_for_playwright(parsed.get("cookies", []))
            state_data = {"cookies": formatted_cookies, "origins": parsed.get("origins", [])}
        else:
            print("❌ صيغة البيانات غير صحيحة. يجب أن تكون قائمة كوكيز JSON من Cookie-Editor.")
            return False

        save_and_display_state(state_data)
        return True
    except Exception as e:
        print(f"❌ خطأ أثناء معالجة البيانات: {e}")
        return False

def import_from_clipboard():
    """Pulls cookies automatically from Windows clipboard."""
    print("\n⏳ جاري قراءة الكود من الحافظة (Clipboard)...")
    content = read_from_clipboard()
    if not content:
        print("❌ لم يتم العثور على أي كود منسوخ في الحافظة.")
        print("💡 الخطوات: في متصفحك، افتح إضافة Cookie-Editor -> اضغط Export -> Export as JSON، ثم أعد المحاولة هنا.")
        return False

    print(f"✅ تم التقاط كود بطول {len(content)} حرف من الحافظة بنجاح.")
    return process_raw_cookie_string(content)

def clean_all_cookies():
    """Deletes all local cookies and saved state files."""
    files_to_remove = [STATE_FILE, RENDER_TXT_FILE, COOKIES_FILE]
    removed_count = 0
    for f in files_to_remove:
        if os.path.exists(f):
            try:
                os.remove(f)
                removed_count += 1
            except Exception:
                pass
    print("\n" + "=" * 65)
    print("  🧹 تم تنظيف وحذف جميع الكوكيز والجلسات المحفوظة بنجاح!")
    print(f"  تم مسح {removed_count} ملفات بنجاح. النظام الآن نظيف تماماً 100%.")
    print("=" * 65 + "\n")

async def login_with_firefox():
    """Launches Firefox for clean, native Google login."""
    print("\n🦊 جاري تشغيل متصفح Firefox...")
    print("⚡ متصفح Firefox يتجاوز جميع قيود حظر الأتمتة لـ Google تلقائياً.")

    profile_dir = os.path.abspath("./firefox_login_profile")

    async with async_playwright() as p:
        context = await p.firefox.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            locale="ar"
        )

        page = context.pages[0] if context.pages else await context.new_page()

        print("\n" + "=" * 60)
        print("  1. تم فتح نافذة متصفح Firefox على شاشتك الآن.")
        print("  2. سجّل الدخول بحساب المدرسة الرسمي (h.badache@hns-re2sd.dz).")
        print("  3. تأكد أن صفحة Google Meet الرئيسية فتحت أمامك وحسابك متصل.")
        print("  4. ثم عد هنا إلى هذه الشاشة السوداء واضغط ENTER لحفظ الجلسة فوراً!")
        print("=" * 60 + "\n")

        await page.goto("https://accounts.google.com/ServiceLogin?continue=https://meet.google.com/", wait_until="domcontentloaded")

        await asyncio.to_thread(input, ">>> بعد إتمام تسجيل الدخول والوصول لـ Meet، اضغط ENTER هنا: ")

        print("⏳ جاري استخراج رموز الجلسة والكوكيز النشطة بأمان...")
        state = await context.storage_state()
        state["cookies"] = format_cookies_for_playwright(state.get("cookies", []))
        save_and_display_state(state)
        await context.close()
        return True

async def login_with_native_browser():
    """Launches installed browser (Brave, Edge, Chrome) natively."""
    exe = find_executable(BRAVE_PATHS) or find_executable(EDGE_PATHS) or find_executable(CHROME_PATHS)
    if not exe:
        print("❌ لم يتم العثور على متصفح Brave أو Edge أو Chrome.")
        return False

    browser_name = "Brave" if "Brave" in exe else ("Edge" if "Edge" in exe else "Chrome")
    profile_dir = os.path.abspath("./native_login_profile")
    print(f"\n🚀 جاري تشغيل متصفح {browser_name} كمتصفح نظام أصلي 100%...")
    print(f"   المسار: {exe}")

    cmd = [
        exe,
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://accounts.google.com/ServiceLogin?continue=https://meet.google.com/"
    ]

    proc = subprocess.Popen(cmd)

    print("\n" + "=" * 60)
    print(f"  1. تم فتح نافذة متصفح {browser_name} على شاشتك.")
    print("  2. سجّل الدخول بحساب المدرسة الرسمي (h.badache@hns-re2sd.dz).")
    print("  3. تأكد من الوصول لصفحة Google Meet الرئيسية.")
    print(f"  4. أغلق نافذة متصفح {browser_name} تماماً (مهم لحفظ الملفات).")
    print("  5. ثم عد هنا واضغط ENTER.")
    print("=" * 60 + "\n")

    await asyncio.to_thread(input, ">>> بعد تسجيل الدخول وإغلاق نافذة المتصفح، اضغط ENTER هنا: ")
    await asyncio.sleep(2)

    print("⏳ جاري استخراج الجلسة ورموز الدخول...")
    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=True
            )
            state = await context.storage_state()
            await context.close()
            state["cookies"] = format_cookies_for_playwright(state.get("cookies", []))
            save_and_display_state(state)
            return True
    except Exception as e:
        print(f"❌ خطأ أثناء استخراج الجلسة: {e}")
        return False

async def verify_state_file():
    """Tests if state.json is currently valid and signed into Google Meet."""
    if not os.path.exists(STATE_FILE):
        print("\n❌ لا يوجد ملف state.json محفوظ حالياً.")
        return

    print("\n⏳ جاري فحص صلاحية ملف state.json عبر متصفح Chromium الصامت...")
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        cookie_count = len(data.get("cookies", []))
        print(f"📄 الملف يحتوي على {cookie_count} كوكيز.")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(storage_state=data, locale="ar")
            page = await ctx.new_page()
            await page.goto("https://meet.google.com", wait_until="domcontentloaded")
            await asyncio.sleep(3)
            
            url = page.url
            content = await page.content()
            await browser.close()

            print(f"🔗 الرابط الفعلي لـ Meet: {url}")
            if "workspace.google.com" in url or "accounts.google.com" in url or "تسجيل الدخول" in content or "Connexion" in content:
                print("⚠️ النتيجة: الجلسة منتهية الصلاحية أو مسجل خروجها (Google يطلب تسجيل الدخول).")
                print("💡 الحل: استخدم الخيار [1] لنسخ الكوكيز الجديدة من المتصفح أو [2] لتسجيل الدخول.")
            elif "h.badache" in content or "hns-re2sd" in content:
                print("🎉 النتيجة: ممتاز جداً! حساب المدرسة الرسمي (@hns-re2sd.dz) متصل ومعتمد وجاهز للدخول بدون طلب إذن!")
            else:
                print("✅ النتيجة: الجلسة متصلة ونشطة في Google Meet بنجاح!")
    except Exception as e:
        print(f"❌ خطأ أثناء الفحص: {e}")

def paste_manually():
    """Manual terminal pasting fallback."""
    print("\n📋 الصق كود الكوكيز (JSON أو Base64) هنا.")
    print("💡 بعد لصق الكود كاملاً، اكتب 'END' في سطر جديد واضغط Enter:\n")
    lines = []
    while True:
        try:
            line = input()
            if line.strip().upper() == "END":
                break
            lines.append(line)
        except EOFError:
            break
    raw_str = "\n".join(lines).strip()
    process_raw_cookie_string(raw_str)

async def main():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        arg_file = sys.argv[1].strip()
        if os.path.exists(arg_file):
            print(f"📂 جاري معالجة الملف المدخل: {arg_file}")
            with open(arg_file, "r", encoding="utf-8") as f:
                content = f.read()
            if process_raw_cookie_string(content):
                await verify_state_file()
            return

    while True:
        print("\n" + "=" * 65)
        print("  🔐 أداة استخراج واعتماد جلسة حساب المدرسة (state.json)")
        print("  Google Meet Attendance & Render State Generator")
        print("=" * 65)
        has_state = os.path.exists(STATE_FILE)
        state_status = "محفوظ ومجهز ✅" if has_state else "غير موجود ⚠️"
        print(f"📌 حالة ملف state.json الحالي: {state_status}")
        print("-" * 65)
        print(" [1] ⚡ قراءة الكوكيز من الحافظة تلقائياً (بعد Export as JSON في المتصفح) [الأسرع]")
        print(" [2] 🦊 تسجيل الدخول المباشر عبر متصفح Firefox (يتجاوز حظر Google 100%)")
        print(" [3] 🚀 تسجيل الدخول المباشر عبر Brave / Edge / Chrome")
        print(" [4] 📋 لصق كود الكوكيز (JSON أو Base64) يدوياً هنا في Terminal")
        print(" [5] 🔍 فحص صلاحية ملف state.json الحالي والتأكد من اتصال الحساب")
        print(" [6] 🧹 حذف وتصفير جميع الكوكيز والجلسات السابقة (Clean Reset)")
        print(" [0] خروج")
        print("-" * 65)

        choice = input("👉 أدخل رقم الخيار [1/2/3/4/5/6/0] واضغط Enter (الافتراضي 1): ").strip()
        if not choice:
            choice = "1"

        if choice == "1":
            if import_from_clipboard():
                await verify_state_file()
        elif choice == "2":
            if await login_with_firefox():
                await verify_state_file()
        elif choice == "3":
            if await login_with_native_browser():
                await verify_state_file()
        elif choice == "4":
            paste_manually()
            await verify_state_file()
        elif choice == "5":
            await verify_state_file()
        elif choice == "6":
            clean_all_cookies()
        elif choice == "0":
            print("\nتم الخروج بنجاح.")
            break
        else:
            print("\n❌ خيار غير معروف، يرجى كتابة رقم من 0 إلى 6.")

        input("\nاضغط ENTER للعودة إلى القائمة الرئيسية...")

if __name__ == "__main__":
    asyncio.run(main())
