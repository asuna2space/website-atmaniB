import subprocess
"""
Google Meet Headless Attendance & Auto-Reply Bot
Optimized for ultra-low memory cloud containers (< 512MB RAM, Render Free Tier) & Local execution.
Features:
- Dual Input Methods:
  1. Web Dashboard at http://localhost:10000 (URL & Student Name inputs + Start/Stop buttons).
  2. Telegram Bot (@meethazem_bot) with live link reception & 5-minute periodic screenshot dispatch.
- Headless, completely silent (camera OFF, microphone MUTED - no green screen or sine waves).
- Student name: hazem (as requested).
- Auto-handles "Continue without microphone and camera" popups and "Switch here" buttons.
- In-call chat monitoring and automated response to keywords.
- Built-in live /screenshot, /status, and /logs endpoints.
"""

import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import json
import base64
import time
import asyncio
import logging
import threading
import re
import hashlib
import imaplib
import email
from email.header import decode_header
from urllib.parse import parse_qs, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Set, List, Optional

try:
    import httpx
except ImportError:
    httpx = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip()

try:
    import psutil
except ImportError:
    psutil = None

from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MeetBot")

def clean_meet_url(raw_url: str) -> str:
    """
    Cleans and standardizes Google Meet URLs:
    - Extracts standard meeting code (e.g. xxx-yyyy-zzz or lookup/xxx)
    - Strips /meet/ prefix
    - Strips all toxic query params (?authuser=..., ?pli=..., ?hs=...) which cause
      Google to redirect or fail when account indices don't match the bot's session.
    """
    if not raw_url:
        return ""
    text = raw_url.strip()
    # 1. Standard 3-4-3 or 3-3-3 meeting code pattern
    m = re.search(r'([a-zA-Z]{3}-[a-zA-Z]{3,4}-[a-zA-Z]{3})', text)
    if m:
        return f"https://meet.google.com/{m.group(1).lower()}"
    # 2. lookup/xyz pattern
    m_lookup = re.search(r'meet\.google\.com/lookup/([a-zA-Z0-9_-]+)', text, re.IGNORECASE)
    if m_lookup:
        return f"https://meet.google.com/lookup/{m_lookup.group(1)}"
    # 3. General meet url cleanup
    try:
        t = text if text.startswith("http") else "https://" + text
        parsed = urlparse(t)
        if "meet.google.com" in parsed.netloc:
            path = parsed.path.strip("/")
            if path.startswith("meet/"):
                path = path[5:]
            return f"https://meet.google.com/{path}"
    except Exception:
        pass
    return text

# Environment & Settings
MEET_URL = clean_meet_url(os.getenv("MEET_URL", "").strip())
STUDENT_NAME = os.getenv("STUDENT_NAME", "hazem").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8971961233:AAGCdv5ODVakOx0Js5dd8DHQS1u7HjOQh-4").strip()

AUTO_REPLY_MESSAGE = os.getenv(
    "AUTO_REPLY_MESSAGE", 
    "عذراً أستاذ، المايك معطل لدي اليوم وأنا حاضر ومتابع معكم في الحصة."
).strip()
TRIGGER_KEYWORDS_RAW = os.getenv("TRIGGER_KEYWORDS", "hazem,حازم,حاضر,طالب,levi")
TRIGGER_KEYWORDS: List[str] = [k.strip().lower() for k in TRIGGER_KEYWORDS_RAW.split(",") if k.strip()]
if STUDENT_NAME.lower() not in TRIGGER_KEYWORDS:
    TRIGGER_KEYWORDS.append(STUDENT_NAME.lower())

AUTO_REPLY_COOLDOWN = int(os.getenv("AUTO_REPLY_COOLDOWN_SECONDS", "300"))
MAX_DURATION_MINUTES = int(os.getenv("MAX_DURATION_MINUTES", "90"))
ADMISSION_TIMEOUT_SECONDS = int(os.getenv("ADMISSION_TIMEOUT_SECONDS", "1800"))
HEADLESS = os.getenv("HEADLESS", "1").lower() in ("1", "true", "yes")
BROWSER_LOCALE = os.getenv("BROWSER_LOCALE", "ar")
GOOGLE_STORAGE_STATE_RAW = os.getenv("GOOGLE_STORAGE_STATE", "").strip()

# Modern Chrome User Agent
MODERN_CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"

# Global state for web status/screenshot endpoints & Telegram
bot_state = {
    "status": "Starting up...",
    "current_task": "المهمة 1: إعداد المتصفح",
    "problem_diagnosis": "لا توجد مشكلة مرصودة حتى الآن",
    "problem_type": "NORMAL",
    "page_title": "",
    "current_url": "",
    "student_name": STUDENT_NAME,
    "meet_url": MEET_URL,
    "memory_mb": 0.0,
    "last_screenshot": b"",
    "telegram_subscriber": None,
    "gmail_monitor_enabled": os.getenv("GMAIL_MONITOR_ENABLED", "1").lower() in ("1", "true", "yes"),
    "gmail_last_check": "لم يتم الفحص بعد",
    "gmail_last_detected_url": "",
    "google_account_info": "في انتظار الفحص"
}

current_page_ref: Optional[Page] = None
current_context_ref: Optional[BrowserContext] = None
global_browser_ref = None
async_loop: Optional[asyncio.AbstractEventLoop] = None
task_history: List[dict] = []

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUBSCRIBER_FILE = os.path.join(BASE_DIR, "telegram_subscriber.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.json")
RENDER_TXT_FILE = os.path.join(BASE_DIR, "render_secret_base64.txt")

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

def load_telegram_subscriber() -> Optional[int | str]:
    """Loads previously saved subscriber chat ID from disk."""
    if os.path.exists(SUBSCRIBER_FILE):
        try:
            with open(SUBSCRIBER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("chat_id")
        except Exception:
            pass
    return None

def save_telegram_subscriber(chat_id: int | str):
    """Persists subscriber chat ID to disk and updates global state."""
    global telegram_subscriber_chat_id
    telegram_subscriber_chat_id = chat_id
    bot_state["telegram_subscriber"] = chat_id
    try:
        with open(SUBSCRIBER_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "chat_id": chat_id,
                "saved_at": time.time(),
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S")
            }, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not persist subscriber: {e}")

telegram_subscriber_chat_id: Optional[int | str] = load_telegram_subscriber()
if telegram_subscriber_chat_id:
    bot_state["telegram_subscriber"] = telegram_subscriber_chat_id

last_periodic_screenshot_time: float = 0.0

# Dashboard Auth Config
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "hazem")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "1212")
SESSION_SALT = "hazem-meet-secret-salt-2026"
AUTH_TOKEN = hashlib.sha256(f"{DASHBOARD_USER}:{DASHBOARD_PASS}:{SESSION_SALT}".encode()).hexdigest()

new_meeting_event: Optional[asyncio.Event] = None
stop_requested: bool = False

def record_task(step: str, status: str, summary: str, details: str = "", problem_type: str = ""):
    """Records a task step into in-memory chronological log and updates global dashboard state."""
    timestamp = time.strftime("%H:%M:%S")
    entry = {
        "time": timestamp,
        "step": step,
        "status": status,  # "success", "warning", "error", "info"
        "summary": summary,
        "details": details,
        "problem_type": problem_type
    }
    task_history.append(entry)
    if len(task_history) > 150:
        task_history.pop(0)

    bot_state["current_task"] = step
    if summary:
        bot_state["status"] = summary
    if problem_type:
        bot_state["problem_type"] = problem_type
        bot_state["problem_diagnosis"] = f"[{problem_type}] {summary}"

    icon = "✅" if status == "success" else ("⚠️" if status == "warning" else ("❌" if status == "error" else "ℹ️"))
    logger.info(f"{icon} [{timestamp}] {step} -> {summary} ({details})")

def start_or_restart_meeting(url: str, student_name: str = "hazem"):
    """Triggers starting or restarting a meeting with a new URL."""
    global MEET_URL, STUDENT_NAME, stop_requested, current_page_ref, new_meeting_event
    url = clean_meet_url(url)
    MEET_URL = url
    STUDENT_NAME = student_name
    bot_state["meet_url"] = url
    bot_state["student_name"] = student_name
    stop_requested = False
    
    if current_page_ref and not current_page_ref.is_closed():
        logger.info(f"Closing previous meeting page to switch to: {url}")
        try:
            asyncio.create_task(current_page_ref.close())
        except Exception:
            pass

    if new_meeting_event:
        new_meeting_event.set()

def stop_current_meeting():
    """Stops the current meeting session."""
    global stop_requested, current_page_ref
    stop_requested = True
    bot_state["status"] = "تم إيقاف المكالمة من قبل المستخدم."
    bot_state["current_task"] = "متوقف"
    if current_page_ref and not current_page_ref.is_closed():
        try:
            asyncio.create_task(current_page_ref.close())
        except Exception:
            pass

# Telegram Async Integration
async def send_telegram_message(chat_id: int | str, text: str) -> bool:
    """Sends a formatted text message to Telegram with fallback to plain text if HTML parsing fails."""
    if not TELEGRAM_BOT_TOKEN or not httpx:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            if resp.status_code == 200:
                return True
            logger.warning(f"Telegram sendMessage failed ({resp.status_code}): {resp.text}. Retrying with plain text...")
            plain_text = re.sub(r'<[^>]+>', '', text)
            resp2 = await client.post(url, json={"chat_id": chat_id, "text": plain_text})
            return resp2.status_code == 200
    except Exception as e:
        logger.warning(f"Telegram sendMessage error: {e}")
        return False

async def send_telegram_photo(chat_id: int | str, photo_bytes: bytes, caption: str = "") -> bool:
    """Sends a photo with caption to Telegram with fallback to plain text if HTML parsing fails."""
    if not TELEGRAM_BOT_TOKEN or not photo_bytes or not httpx:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        async with httpx.AsyncClient(timeout=35) as client:
            files = {"photo": ("screenshot.png", photo_bytes, "image/png")}
            data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
            resp = await client.post(url, data=data, files=files)
            if resp.status_code == 200:
                return True
            logger.warning(f"Telegram sendPhoto failed ({resp.status_code}): {resp.text}. Retrying with plain text caption...")
            plain_caption = re.sub(r'<[^>]+>', '', caption)
            data_plain = {"chat_id": chat_id, "caption": plain_caption}
            files_plain = {"photo": ("screenshot.png", photo_bytes, "image/png")}
            resp2 = await client.post(url, data=data_plain, files=files_plain)
            if resp2.status_code == 200:
                return True
            logger.error(f"Telegram sendPhoto plain retry failed: {resp2.text}")
            return False
    except Exception as e:
        logger.warning(f"Telegram sendPhoto error: {e}")
        return False

async def periodic_telegram_screenshot_dispatcher():
    """
    Dedicated independent background task:
    Every 5 minutes (300 seconds), as long as a browser page is active,
    automatically captures a screenshot and delivers it to the Telegram subscriber.
    """
    global last_periodic_screenshot_time, current_page_ref, telegram_subscriber_chat_id, bot_state
    logger.info("Independent 5-Minute Telegram Periodic Screenshot Dispatcher running...")
    last_periodic_screenshot_time = time.time()
    
    while True:
        await asyncio.sleep(5)  # Check every 5 seconds
        try:
            if not telegram_subscriber_chat_id:
                telegram_subscriber_chat_id = load_telegram_subscriber()
                if telegram_subscriber_chat_id:
                    bot_state["telegram_subscriber"] = telegram_subscriber_chat_id

            if not telegram_subscriber_chat_id:
                continue

            # Only dispatch if a browser page is actually open and alive
            if not current_page_ref or current_page_ref.is_closed():
                continue

            now = time.time()
            if now - last_periodic_screenshot_time >= 300: # 5 minutes (300s)
                logger.info(f"5 minutes elapsed. Capturing periodic screenshot for Telegram chat {telegram_subscriber_chat_id}...")
                try:
                    shot = await current_page_ref.screenshot(type="png", timeout=3500)
                    bot_state["last_screenshot"] = shot

                    in_call = False
                    try:
                        in_call = await is_in_call(current_page_ref)
                    except Exception:
                        pass

                    status_str = "حاضر ومستقر داخل الحصة" if in_call else bot_state.get("current_task", "مستمر في الاتصال")
                    caption = (
                        "📸 <b>لقطة شاشة دورية (كل 5 دقائق)</b>\n\n"
                        f"⏱️ <b>الوقت:</b> {time.strftime('%H:%M:%S')}\n"
                        f"👤 <b>اسم الطالب:</b> {bot_state.get('student_name', 'hazem')}\n"
                        f"📊 <b>الحالة:</b> {status_str}\n"
                        f"🔗 <b>الرابط:</b> {bot_state.get('meet_url', '')}"
                    )
                    sent = await send_telegram_photo(telegram_subscriber_chat_id, shot, caption)
                    if sent:
                        last_periodic_screenshot_time = now
                        record_task(
                            "إرسال صورة دورية (تيليجرام)", 
                            "success", 
                            "✅ تم إرسال لقطة شاشة دورية (5 دقائق) إلى تيليجرام بنجاح.",
                            f"المستلم: {telegram_subscriber_chat_id}"
                        )
                        logger.info("✅ 5-minute periodic screenshot delivered to Telegram!")
                except Exception as shot_err:
                    logger.warning(f"Error during periodic screenshot capture/send: {shot_err}")
        except Exception as loop_err:
            logger.warning(f"Error in periodic_telegram_screenshot_dispatcher: {loop_err}")

async def telegram_polling_loop():
    """Background polling loop for @meethazem_bot."""
    global telegram_subscriber_chat_id, MEET_URL, STUDENT_NAME, stop_requested, current_page_ref, last_periodic_screenshot_time
    if not TELEGRAM_BOT_TOKEN or not httpx:
        logger.info("Telegram Bot Token not configured or httpx unavailable. Skipping Telegram polling.")
        return

    logger.info("Starting Telegram Bot listener for @meethazem_bot...")
    offset = 0
    meet_regex = re.compile(r"(?:https?://)?meet\.google\.com/[a-zA-Z0-9_\-\?=&/]+|[a-zA-Z]{3}-[a-zA-Z]{3,4}-[a-zA-Z]{3}")

    async with httpx.AsyncClient(timeout=35) as client:
        while True:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=25"
                resp = await client.get(url)
                if resp.status_code != 200:
                    if resp.status_code == 409:
                        logger.warning("Telegram polling 409 Conflict: another instance is active. Retrying in 10s...")
                        await asyncio.sleep(10)
                    else:
                        await asyncio.sleep(5)
                    continue

                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        message = update.get("message") or update.get("edited_message")
                        if not message:
                            continue
                        chat_id = message["chat"]["id"]
                        text = (message.get("text") or "").strip()

                        if not text:
                            continue

                        # Always save this chat as the verified subscriber
                        save_telegram_subscriber(chat_id)

                        # Password authentication (1212)
                        if "1212" in text or text in ("/login", "hazem 1212"):
                            auth_reply = (
                                "✅ <b>تم التحقق وتوثيق حسابك بنجاح! (كلمة المرور: 1212)</b>\n\n"
                                "أهلاً بك <b>hazem</b>، تم اعتماد حسابك كمشترك رئيسي.\n"
                                "📸 سأرسل لك لقطات شاشة حية للحصة <b>كل 5 دقائق تلقائياً</b> فور بدء أي اجتماع.\n\n"
                                "🔗 يمكنك إرسال أي رابط Google Meet هنا في أي وقت وسأقوم بالانضمام فوراً بصمت تام!"
                            )
                            await send_telegram_message(chat_id, auth_reply)
                            continue

                        # Command: /start
                        if text == "/start":
                            welcome = (
                                "👋 <b>مرحباً بك في بوت Google Meet المساعد (@meethazem_bot)!</b>\n\n"
                                "👤 <b>اسم الطالب:</b> hazem (كلمة المرور: 1212)\n"
                                "✅ <b>تم ربط حسابك بنجاح كمستلم للصور الدورية.</b>\n\n"
                                "📌 <b>طرق الاستخدام المتاحة:</b>\n"
                                "1️⃣ أرسل رابط أي اجتماع Google Meet هنا مباشرة وسأنضم فوراً.\n"
                                "2️⃣ مراقبة بريد Gmail تلقائياً: إذا وصلك أي رابط Meet في إيميل المدرسة، سأنضم له تلقائياً!\n\n"
                                "📸 <b>الميزة التلقائية:</b>\n"
                                "سأرسل لك لقطة شاشة حية للحصة <b>كل 5 دقائق تلقائياً</b> دون الحاجة لطلبها يدوياً!\n\n"
                                "⚙️ <b>الأوامر المتاحة:</b>\n"
                                "▫️ /status - عرض حالة البوت الحالية\n"
                                "▫️ /gmail - فحص حالة مراقبة بريد Gmail\n"
                                "▫️ /screenshot - طلب لقطة شاشة فورية الآن\n"
                                "▫️ /test - تجربة استقبال صورة للتأكد من الاتصال\n"
                                "▫️ /stop - إنهاء المكالمة ومغادرة الاجتماع"
                            )
                            await send_telegram_message(chat_id, welcome)
                            continue

                        # Command: /gmail
                        elif text in ("/gmail", "جمايل", "ايميل"):
                            g_status = "مفعلة 🟢" if bot_state.get("gmail_monitor_enabled", True) else "معطلة 🔴"
                            g_last = bot_state.get("gmail_last_check", "لم يتم الفحص بعد")
                            g_url = bot_state.get("gmail_last_detected_url") or "لا توجد روابط جديدة"
                            msg = (
                                f"📧 <b>مراقبة بريد Gmail الدراسي:</b> {g_status}\n\n"
                                f"⏱️ <b>آخر فحص للبريد:</b> {g_last}\n"
                                f"🔗 <b>آخر رابط ملتقط:</b> {g_url}\n\n"
                                "💡 <i>البوت يفحص بريدك المدرسي كل 30 ثانية في الخلفية، وفور إرسال الشيخ للرابط ينضم تلقائياً!</i>"
                            )
                            await send_telegram_message(chat_id, msg)
                            continue

                        # Command: /status
                        elif text == "/status":
                            in_call_str = "نعم (حاضر ومستقر داخل الحصة)" if (current_page_ref and not current_page_ref.is_closed()) else "في الانتظار"
                            status_msg = (
                                f"🤖 <b>حالة البوت:</b> {bot_state.get('status', 'غير متوفر')}\n"
                                f"👤 <b>اسم الطالب:</b> {bot_state.get('student_name', STUDENT_NAME)}\n"
                                f"🔗 <b>الرابط:</b> {bot_state.get('meet_url') or 'لا يوجد اجتماع نشط'}\n"
                                f"📞 <b>داخل المكالمة:</b> {in_call_str}\n"
                                f"⏱️ <b>المهمة:</b> {bot_state.get('current_task')}\n"
                                f"📊 <b>التشخيص:</b> {bot_state.get('problem_diagnosis')}\n"
                                f"📸 <b>إرسال دوري كل 5 دقائق:</b> مفعل ومربوط بحسابك"
                            )
                            await send_telegram_message(chat_id, status_msg)
                            continue

                        # Command: /screenshot or /test
                        elif text in ("/screenshot", "/test", "صورة"):
                            if current_page_ref and not current_page_ref.is_closed():
                                try:
                                    shot = await current_page_ref.screenshot(type="png", timeout=3500)
                                    bot_state["last_screenshot"] = shot
                                    cap = (
                                        f"📸 <b>لقطة شاشة فورية بطلب منك</b>\n"
                                        f"⏱️ <b>الوقت:</b> {time.strftime('%H:%M:%S')}\n"
                                        f"🔗 {bot_state.get('meet_url')}\n"
                                        "✅ <i>سيرسل لك البوت أيضاً لقطة شاشة تلقائياً كل 5 دقائق.</i>"
                                    )
                                    await send_telegram_photo(chat_id, shot, cap)
                                except Exception as se:
                                    await send_telegram_message(chat_id, f"⚠️ تعذر التقاط الصورة: {se}")
                            else:
                                await send_telegram_message(
                                    chat_id, 
                                    "⚠️ لا توجد مكالمة جارية حالياً لالتقاط شاشتها.\nأرسل رابط اجتماع Google Meet للبدء فوراً!"
                                )
                            continue

                        # Command: /stop
                        elif text == "/stop":
                            stop_current_meeting()
                            await send_telegram_message(chat_id, "🛑 تم استلام أمر الإيقاف. تم إغلاق المتصفح ومغادرة الاجتماع بنجاح.")
                            continue

                        # Detect Google Meet link in text
                        match = meet_regex.search(text)
                        if match:
                            found_url = clean_meet_url(match.group(0))
                            if not found_url.startswith("http"):
                                found_url = "https://" + found_url

                            reply = (
                                "✅ <b>تم استلام رابط الاجتماع بنجاح!</b>\n\n"
                                f"🔗 <b>الرابط:</b> {found_url}\n"
                                "👤 <b>اسم الطالب:</b> hazem\n"
                                "⏳ <b>جاري إطلاق المتصفح والدخول إلى الحصة...</b>\n\n"
                                "📸 <i>سأرسل لك لقطة شاشة أولى فور الدخول، ثم صورة دورية تلقائياً كل 5 دقائق.</i>"
                            )
                            await send_telegram_message(chat_id, reply)

                            # Reset timer for periodic dispatch
                            last_periodic_screenshot_time = time.time()

                            # Trigger meeting
                            start_or_restart_meeting(found_url, "hazem")
                        else:
                            await send_telegram_message(
                                chat_id, 
                                "❓ لم أتعرف على الأمر.\n- أرسل رابط Google Meet للانضمام فوراً.\n- أو أرسل كلمة المرور 1212 للتوثيق.\n- أو أرسل /screenshot للحصول على صورة شاشة."
                            )
            except Exception as e:
                logger.warning(f"Telegram polling exception: {e}")
                await asyncio.sleep(4)

class HealthHandler(BaseHTTPRequestHandler):
    """
    Authenticated HTTP handler serving:
    - /login: Login page with credentials (hazem / 1212)
    - /logout: Clears session cookie and redirects to /login
    - / or /status: Protected status dashboard (Case 1 & Case 2)
    - /api/join: Protected POST endpoint to start meeting
    - /api/stop: Protected POST endpoint to stop current meeting
    - /api/test-telegram: Protected POST endpoint to test sending a screenshot/message to Telegram
    - /screenshot: Protected live PNG screenshot
    - /logs: Protected plain-text forensic log
    - /health, /healthz: Unprotected health check for cloud uptime monitors
    """
    def is_authenticated(self) -> bool:
        """Checks if the client provides a valid meet_auth session cookie."""
        cookies_hdr = self.headers.get("Cookie", "")
        for item in cookies_hdr.split(";"):
            item = item.strip()
            if item.startswith("meet_auth="):
                token = item.split("=", 1)[1].strip()
                if token == AUTH_TOKEN:
                    return True
        return False

    def render_login_page(self, error: str = ""):
        """Renders a sleek dark-themed Arabic login page."""
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

        error_block = f"""
        <div style="background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; color: #fca5a5; padding: 12px; border-radius: 8px; font-size: 14px; margin-bottom: 20px; font-weight: bold;">
            ❌ {error}
        </div>
        """ if error else ""

        html = f"""
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>تسجيل الدخول | Google Meet Bot</title>
            <style>
                * {{ box-sizing: border-box; }}
                body {{
                    font-family: system-ui, -apple-system, sans-serif;
                    background: #0b1120;
                    color: #f8fafc;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 20px;
                }}
                .login-card {{
                    background: #1e293b;
                    border: 1px solid #334155;
                    border-radius: 16px;
                    padding: 36px 32px;
                    width: 100%;
                    max-width: 420px;
                    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
                    text-align: center;
                }}
                .logo-icon {{
                    font-size: 44px;
                    margin-bottom: 12px;
                    display: inline-block;
                }}
                h2 {{
                    color: #38bdf8;
                    margin: 0 0 6px 0;
                    font-size: 22px;
                }}
                p.subtext {{
                    color: #94a3b8;
                    font-size: 14px;
                    margin: 0 0 24px 0;
                }}
                .form-group {{
                    margin-bottom: 18px;
                    text-align: right;
                }}
                label {{
                    display: block;
                    font-size: 13px;
                    color: #cbd5e1;
                    margin-bottom: 6px;
                    font-weight: 600;
                }}
                input[type="text"], input[type="password"] {{
                    width: 100%;
                    background: #0f172a;
                    border: 1px solid #475569;
                    color: #f8fafc;
                    padding: 13px 15px;
                    border-radius: 8px;
                    font-size: 15px;
                    outline: none;
                    transition: border-color 0.2s, box-shadow 0.2s;
                }}
                input:focus {{
                    border-color: #38bdf8;
                    box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.2);
                }}
                .btn-submit {{
                    width: 100%;
                    background: #0284c7;
                    color: white;
                    padding: 14px;
                    border-radius: 8px;
                    font-size: 16px;
                    font-weight: bold;
                    border: none;
                    cursor: pointer;
                    margin-top: 10px;
                    transition: background 0.2s;
                }}
                .btn-submit:hover {{
                    background: #0369a1;
                }}
                .hint {{
                    margin-top: 20px;
                    font-size: 12px;
                    color: #64748b;
                }}
            </style>
        </head>
        <body>
            <div class="login-card">
                <div class="logo-icon">🤖</div>
                <h2>تسجيل الدخول للوحة التحكم</h2>
                <p class="subtext">Google Meet Attendance & Forensic Bot</p>
                {error_block}
                <form method="POST" action="/login">
                    <div class="form-group">
                        <label>👤 اسم المستخدم:</label>
                        <input type="text" name="username" value="hazem" placeholder="hazem" required autofocus>
                    </div>
                    <div class="form-group">
                        <label>🔒 كلمة المرور:</label>
                        <div style="position: relative;">
                            <input type="password" id="pwd_field" name="password" value="1212" placeholder="1212" required style="padding-left: 40px;">
                            <span onclick="togglePwd()" style="position: absolute; left: 10px; top: 50%; transform: translateY(-50%); cursor: pointer; color: #94a3b8; font-size: 16px;">👁️</span>
                        </div>
                    </div>
                    <button type="submit" class="btn-submit">تسجيل الدخول 🚀</button>
                </form>
                <div class="hint" style="background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 8px; padding: 10px; margin-top: 20px; font-size: 13px; color: #7dd3fc;">
                    🔑 <b>بيانات الدخول المعتمدة:</b><br>
                    اسم المستخدم: <b style="color: #fff;">hazem</b> | كلمة المرور: <b style="color: #fff;">1212</b>
                </div>
                <script>
                    function togglePwd() {{
                        var f = document.getElementById('pwd_field');
                        f.type = f.type === 'password' ? 'text' : 'password';
                    }}
                </script>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))

    def do_POST(self):
        global MEET_URL, STUDENT_NAME, async_loop, telegram_subscriber_chat_id, current_page_ref

        # Handle login submission
        if self.path in ("/login", "/api/login"):
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8")
            is_json = "application/json" in self.headers.get("Content-Type", "")
            user = ""
            pwd = ""
            if is_json:
                try:
                    data = json.loads(post_body)
                    user = (data.get("username") or data.get("user") or "").strip()
                    pwd = (data.get("password") or data.get("pass") or "").strip()
                except Exception:
                    pass
            else:
                params = parse_qs(post_body)
                user = params.get("username", [""])[0] or params.get("user", [""])[0]
                pwd = params.get("password", [""])[0] or params.get("pass", [""])[0]
                user = user.strip()
                pwd = pwd.strip()

            def normalize_cred(s: str) -> str:
                if not s:
                    return ""
                # Convert Arabic-Indic numerals (٠١٢٣٤٥٦٧٨٩) to standard ASCII (0123456789)
                s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
                return s.strip()

            clean_user = normalize_cred(user).lower()
            clean_pwd = normalize_cred(pwd)

            valid_users = {DASHBOARD_USER.lower(), "hazem", "admin", "h.badache@hns-re2sd.dz", "h.badache"}
            valid_passwords = {DASHBOARD_PASS, "1212", "hazem", "admin"}

            if clean_user in valid_users and clean_pwd in valid_passwords:
                if is_json:
                    self.send_response(200)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", f"meet_auth={AUTH_TOKEN}; Path=/; HttpOnly; Max-Age=2592000; SameSite=Lax")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
                else:
                    self.send_response(302)
                    self.send_header("Set-Cookie", f"meet_auth={AUTH_TOKEN}; Path=/; HttpOnly; Max-Age=2592000; SameSite=Lax")
                    self.send_header("Location", "/")
                    self.end_headers()
                return
            else:
                if is_json:
                    self.send_response(401)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": "اسم المستخدم أو كلمة المرور غير صحيحة"}).encode("utf-8"))
                else:
                    self.render_login_page(error="اسم المستخدم أو كلمة المرور غير صحيحة!")
                return

        # Check authentication for all other POST actions
        if not self.is_authenticated():
            self.send_response(401)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "غير مصرح لك. يرجى تسجيل الدخول أولاً."}).encode("utf-8"))
            return

        if self.path == "/api/join":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8")
            try:
                data = json.loads(post_body)
                new_url = clean_meet_url(data.get("meet_url", "").strip())
                new_name = data.get("student_name", "").strip() or "hazem"
                if not new_url:
                    self.send_response(400)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": "رابط الاجتماع مطلوب!"}).encode("utf-8"))
                    return

                if not new_url.startswith("http"):
                    new_url = "https://" + new_url

                if async_loop:
                    async_loop.call_soon_threadsafe(start_or_restart_meeting, new_url, new_name)

                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True, 
                    "message": f"تم استلام الرابط بنجاح! جاري الانضمام باسم {new_name}..."
                }).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
                return

        if self.path == "/api/stop":
            if async_loop:
                async_loop.call_soon_threadsafe(stop_current_meeting)
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "message": "تم إيقاف المكالمة بنجاح."}).encode("utf-8"))
            return

        if self.path == "/api/test-telegram":
            sub_id = telegram_subscriber_chat_id or load_telegram_subscriber()
            if not sub_id:
                self.send_response(400)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": False, 
                    "error": "لم يتم ربط أي مستخدم تيليجرام بعد! افتح @meethazem_bot واضغط /start أولاً."
                }).encode("utf-8"))
                return

            if async_loop:
                async def do_test_tg():
                    if current_page_ref and not current_page_ref.is_closed():
                        try:
                            shot = await current_page_ref.screenshot(type="png", timeout=3500)
                            bot_state["last_screenshot"] = shot
                            cap = (
                                "📸 <b>تجربة إرسال لقطة شاشة من لوحة التحكم</b>\n\n"
                                f"⏱️ <b>الوقت:</b> {time.strftime('%H:%M:%S')}\n"
                                f"👤 <b>اسم الطالب:</b> {bot_state.get('student_name', 'hazem')}\n"
                                f"🔗 {bot_state.get('meet_url', '')}\n"
                                "✅ <i>تم التحقق: الاتصال والإرسال التلقائي كل 5 دقائق يعمل بكفاءة!</i>"
                            )
                            return await send_telegram_photo(sub_id, shot, cap)
                        except Exception:
                            pass
                    # If no call is currently active, send test text notification
                    text = (
                        "🔔 <b>إشعار تجريبي من لوحة التحكم!</b>\n\n"
                        "✅ <b>تم التحقق:</b> البوت متصل بحسابك بنجاح.\n"
                        "📸 بمجرد بدء أي اجتماع، ستصلك لقطة شاشة تلقائياً كل 5 دقائق."
                    )
                    return await send_telegram_message(sub_id, text)

                future = asyncio.run_coroutine_threadsafe(do_test_tg(), async_loop)
                try:
                    success = future.result(timeout=10)
                    if success:
                        self.send_response(200)
                        self.send_header("Content-type", "application/json; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True, "message": "تم إرسال إشعار تجريبي إلى حساب التيليجرام بنجاح!"}).encode("utf-8"))
                        return
                except Exception as te:
                    pass

            self.send_response(500)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "تعذر إرسال الإشعار لتيليجرام. تأكد من تشغيل البوت."}).encode("utf-8"))
            return

        if self.path == "/api/import-cookies":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8")
            try:
                data = json.loads(post_body)
                raw_cookies_str = data.get("cookies_json", "").strip()
                if not raw_cookies_str:
                    self.send_response(400)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": "يرجى لصق كود الكوكيز (JSON أو Base64) أولاً!"}).encode("utf-8"))
                    return

                if not raw_cookies_str.startswith("[") and not raw_cookies_str.startswith("{"):
                    try:
                        pad = (4 - (len(raw_cookies_str) % 4)) % 4
                        decoded_str = base64.b64decode(raw_cookies_str + "=" * pad).decode("utf-8")
                        parsed = json.loads(decoded_str)
                    except Exception:
                        parsed = json.loads(raw_cookies_str)
                else:
                    parsed = json.loads(raw_cookies_str)
                if isinstance(parsed, list):
                    state_dict = {"cookies": format_cookies_for_playwright(parsed), "origins": []}
                elif isinstance(parsed, dict) and "cookies" in parsed:
                    state_dict = {
                        "cookies": format_cookies_for_playwright(parsed.get("cookies", [])),
                        "origins": parsed.get("origins", [])
                    }
                else:
                    self.send_response(400)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": "صيغة الكوكيز غير صالحة. تأكد من نسخ Export as JSON من إضافة Cookie-Editor."}).encode("utf-8"))
                    return

                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(state_dict, f, indent=2)

                # Also save Base64 file for Render
                try:
                    raw_compact = json.dumps(state_dict, separators=(",", ":"))
                    b64_compact = base64.b64encode(raw_compact.encode("utf-8")).decode("utf-8")
                    with open(RENDER_TXT_FILE, "w", encoding="utf-8") as rf:
                        rf.write(b64_compact)
                except Exception:
                    pass

                num_cookies = len(state_dict.get("cookies", []))
                bot_state["google_account_info"] = f"✅ تم حفظ {num_cookies} كوكيز لحساب المدرسة"
                record_task("استيراد كوكيز Google", "success", f"✅ تم حفظ {num_cookies} كوكيز في state.json", "الحساب المدرسي جاهز للدخول بدون طلب إذن")

                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True, 
                    "message": f"✅ تم حفظ {num_cookies} كوكيز واعتماد حساب المدرسة بنجاح! جاهز الآن للدخول في حصة الشيخ."
                }).encode("utf-8"))
                return
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "النص الملصق ليس كود JSON صحيح! يرجى نسخه بالكامل من إضافة Cookie-Editor."}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
                return

        if self.path == "/api/check-google-session":
            if not os.path.exists(STATE_FILE):
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": False, 
                    "message": "⚠️ لا يوجد ملف جلسة (state.json) محفوظ حالياً. يرجى استيراد الكوكيز أولاً."
                }).encode("utf-8"))
                return

            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    sd = json.load(f)
                cookies = sd.get("cookies", [])
                count = len(cookies)
                c_names = set(c.get("name", "") for c in cookies if isinstance(c, dict))
                has_sid = "SID" in c_names or "__Secure-1PSID" in c_names or "__Secure-3PSID" in c_names
                has_hsid = "HSID" in c_names

                now_ts = time.time()
                is_expired = False
                for c in cookies:
                    if isinstance(c, dict) and c.get("name") in ("SID", "__Secure-1PSID"):
                        exp = c.get("expires", -1)
                        if 0 < exp < now_ts:
                            is_expired = True

                if is_expired:
                    msg = "❌ رموز الجلسة (SID) منتهية الصلاحية وفقاً لتاريخ انتهائها لدى Google. يرجى نسخ كوكيز جديدة."
                    self.send_response(200)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "message": msg}).encode("utf-8"))
                    return

                if not has_sid:
                    msg = "⚠️ تنبيه: رمز الجلسة الأساسي (SID) غير موجود في الكوكيز المستوردة!"
                    self.send_response(200)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "message": msg}).encode("utf-8"))
                    return

                bot_state["google_account_info"] = f"✅ حساب المدرسة متصل ({count} كوكيز نشطة)"
                msg = f"🎉 ممتاز! تم التحقق: ملف state.json مجهز وصالح ويحتوي على {count} كوكيز نشطة (SID/HSID معتمدة لحساب المدرسة)."
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "message": msg}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "message": f"خطأ أثناء فحص الملف: {e}"}).encode("utf-8"))
                return
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
                return

        if self.path == "/api/toggle-gmail":
            current_state = bot_state.get("gmail_monitor_enabled", True)
            bot_state["gmail_monitor_enabled"] = not current_state
            status_text = "مفعلة 🟢" if bot_state["gmail_monitor_enabled"] else "معطلة مؤقتاً 🔴"
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True, 
                "enabled": bot_state["gmail_monitor_enabled"],
                "message": f"تم تغيير حالة مراقبة بريد Gmail إلى: {status_text}"
            }).encode("utf-8"))
            return

        if self.path == "/api/check-gmail-now":
            if async_loop and global_browser_ref:
                future = asyncio.run_coroutine_threadsafe(check_gmail_for_meet_links(global_browser_ref), async_loop)
                try:
                    is_ok, msg, found_url = future.result(timeout=35)
                    if found_url:
                        start_or_restart_meeting(found_url, STUDENT_NAME or "hazem")
                    self.send_response(200)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": is_ok, "message": msg, "found_url": found_url or ""}).encode("utf-8"))
                    return
                except Exception as ex:
                    self.send_response(500)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": f"خطأ أثناء فحص البريد: {ex}"}).encode("utf-8"))
                    return
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "المتصفح قيد التهيئة حالياً، جرب بعد ثوانٍ."}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        global current_page_ref, async_loop, telegram_subscriber_chat_id

        # Cloud health check endpoints (bypass auth)
        if self.path in ("/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"healthy")
            return

        # Handle login page
        if self.path == "/login":
            if self.is_authenticated():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
            else:
                self.render_login_page()
            return

        # Handle logout
        if self.path == "/logout":
            self.send_response(302)
            self.send_header("Set-Cookie", "meet_auth=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT")
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # Protect all remaining routes
        if not self.is_authenticated():
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        if self.path.startswith("/screenshot"):
            now = time.time()
            if current_page_ref and async_loop and not current_page_ref.is_closed():
                # Cache screenshots for 10 seconds to avoid overloading Chromium & WebRTC
                if now - bot_state.get("last_screenshot_time", 0) > 10:
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            current_page_ref.screenshot(type="png", timeout=3000), 
                            async_loop
                        )
                        shot = future.result(timeout=4)
                        if shot:
                            bot_state["last_screenshot"] = shot
                            bot_state["last_screenshot_time"] = now
                    except Exception:
                        pass
            screenshot_bytes = bot_state.get("last_screenshot")

            if screenshot_bytes:
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(screenshot_bytes)
                return
            else:
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h3>No screenshot available yet. The browser is initializing or idle.</h3>")
                return

        if self.path.startswith("/logs"):
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            log_lines = [
                "===========================================================",
                f" GOOGLE MEET BOT FORENSIC LOGS - {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f" Current Target URL: {bot_state.get('meet_url')}",
                f" Active Page URL: {bot_state.get('current_url')}",
                f" Student Name: {bot_state.get('student_name')}",
                f" Authenticated User: {DASHBOARD_USER}",
                f" Diagnosed Problem: {bot_state.get('problem_diagnosis')}",
                f" Telegram Subscriber: {telegram_subscriber_chat_id or 'Not Registered'}",
                "===========================================================\n"
            ]
            for t in reversed(task_history):
                log_lines.append(f"[{t['time']}] [{t['step']}] ({t['status'].upper()})")
                log_lines.append(f"   -> النتيجة: {t['summary']}")
                if t['details']:
                    log_lines.append(f"   -> التفاصيل: {t['details']}")
                if t['problem_type']:
                    log_lines.append(f"   -> تصنيف العطل: [{t['problem_type']}]")
                log_lines.append("")
            self.wfile.write("\n".join(log_lines).encode("utf-8"))
            return

        # Dashboard HTML
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

        # Build task rows
        rows_html = ""
        for t in reversed(task_history[:25]):
            badge_color = "#10b981" if t["status"] == "success" else ("#f59e0b" if t["status"] == "warning" else ("#ef4444" if t["status"] == "error" else "#38bdf8"))
            rows_html += f"""
            <div style="border-right: 4px solid {badge_color}; background: #0f172a; padding: 10px 14px; margin-bottom: 8px; border-radius: 6px;">
                <div style="display: flex; justify-content: space-between; font-size: 13px; color: #94a3b8; margin-bottom: 4px;">
                    <strong>{t['step']}</strong>
                    <span>{t['time']}</span>
                </div>
                <div style="color: #f8fafc; font-weight: 500;">{t['summary']}</div>
                {f'<div style="font-size: 12px; color: #cbd5e1; margin-top: 4px; background: #1e293b; padding: 4px 8px; border-radius: 4px;">{t["details"]}</div>' if t["details"] else ''}
            </div>
            """

        prob_bg = "#ef4444" if bot_state["problem_type"] in ("DATACENTER_IP_BLOCKED", "SESSION_EXPIRED", "DENIED_BY_HOST") else ("#10b981" if bot_state["problem_type"] == "IN_CALL" else "#f59e0b")

        sub_id = telegram_subscriber_chat_id or load_telegram_subscriber()
        tg_subscriber_info = f"متصل ومعتمد (ID: {sub_id})" if sub_id else "في انتظار إرسال /start أو الرابط من تيليجرام"

        gmail_enabled = bot_state.get("gmail_monitor_enabled", True)
        gmail_badge_color = "#10b981" if gmail_enabled else "#ef4444"
        gmail_status_text = "مفعلة 🟢 (تفحص كل 30 ثانية)" if gmail_enabled else "معطلة مؤقتاً 🔴"

        has_state = os.path.exists(STATE_FILE)
        cookie_count = 0
        if has_state:
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    cookie_count = len(json.load(f).get("cookies", []))
            except Exception:
                pass
        state_badge_color = "#10b981" if has_state else "#f59e0b"
        state_status_text = f"محفوظة على السيرفر ({cookie_count} كوكيز) ✅" if has_state else "لم يتم الاستيراد بعد ⚠️"

        html = f"""
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="utf-8">
            <meta http-equiv="refresh" content="15">
            <title>لوحة تحكم بوت Google Meet</title>
            <style>
                body {{ font-family: system-ui, -apple-system, sans-serif; background: #0b1120; color: #f8fafc; padding: 16px; margin: 0; }}
                .container {{ max-width: 850px; margin: auto; }}
                .card {{ background: #1e293b; border-radius: 12px; padding: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); margin-bottom: 20px; }}
                h2 {{ color: #38bdf8; margin-top: 0; display: flex; align-items: center; justify-content: space-between; }}
                .diag-box {{ background: {prob_bg}; color: white; padding: 12px 16px; border-radius: 8px; font-weight: bold; margin-bottom: 16px; font-size: 15px; }}
                .item {{ margin: 10px 0; border-bottom: 1px solid #334155; padding-bottom: 6px; font-size: 14px; }}
                .btn {{ display: inline-block; background: #0284c7; color: white; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 14px; cursor: pointer; border: none; margin-left: 8px; }}
                .btn:hover {{ opacity: 0.9; }}
                .btn-copy {{ background: #6366f1; }}
                .preview {{ margin-top: 15px; text-align: center; }}
                .preview img {{ max-width: 100%; border-radius: 8px; border: 1px solid #475569; }}
                .log-scroll {{ max-height: 420px; overflow-y: auto; padding-left: 6px; }}
            </style>
            <script>
                function submitMeeting(e) {{
                    e.preventDefault();
                    const url = document.getElementById('meetUrlInput').value.trim();
                    const name = document.getElementById('studentNameInput').value.trim() || 'hazem';
                    const msg = document.getElementById('joinMsg');
                    msg.style.color = '#38bdf8';
                    msg.innerText = '⏳ جاري إرسال الرابط وتشغيل البوت...';
                    fetch('/api/join', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{meet_url: url, student_name: name}})
                    }})
                    .then(r => r.json())
                    .then(data => {{
                        if(data.ok) {{
                            msg.style.color = '#10b981';
                            msg.innerText = '✅ ' + data.message;
                            setTimeout(() => location.reload(), 2000);
                        }} else {{
                            msg.style.color = '#ef4444';
                            msg.innerText = '❌ ' + (data.error || 'حدث خطأ');
                        }}
                    }})
                    .catch(err => {{
                        msg.style.color = '#ef4444';
                        msg.innerText = '❌ فشل الاتصال: ' + err;
                    }});
                }}

                function stopMeeting() {{
                    if(!confirm('هل تريد بالتأكيد إنهاء المكالمة وإيقاف البوت؟')) return;
                    const msg = document.getElementById('joinMsg');
                    msg.style.color = '#f59e0b';
                    msg.innerText = '⏳ جاري إيقاف البوت والمغادرة...';
                    fetch('/api/stop', {{method: 'POST'}})
                    .then(r => r.json())
                    .then(data => {{
                        msg.style.color = '#10b981';
                        msg.innerText = '🛑 تم إنهاء المكالمة بنجاح.';
                        setTimeout(() => location.reload(), 2000);
                    }});
                }}

                function testTelegram() {{
                    const msg = document.getElementById('tgTestMsg');
                    msg.style.color = '#38bdf8';
                    msg.innerText = '⏳ جاري إرسال إشعار تجريبي لتيليجرام...';
                    fetch('/api/test-telegram', {{method: 'POST'}})
                    .then(r => r.json())
                    .then(data => {{
                        if(data.ok) {{
                            msg.style.color = '#10b981';
                            msg.innerText = '✅ ' + data.message;
                        }} else {{
                            msg.style.color = '#ef4444';
                            msg.innerText = '❌ ' + (data.error || 'فشل الإرسال');
                        }}
                    }})
                    .catch(err => {{
                        msg.style.color = '#ef4444';
                        msg.innerText = '❌ خطأ: ' + err;
                    }});
                }}

                function copyFullLogs() {{
                    fetch('/logs')
                        .then(r => r.text())
                        .then(text => {{
                            navigator.clipboard.writeText(text);
                            alert("✅ تم نسخ سجل المهام بالكامل إلى الحافظة!");
                        }})
                        .catch(err => alert("فشل النسخ: " + err));
                }}

                function saveCookies() {{
                    const raw = document.getElementById('cookiesJsonInput').value.trim();
                    const msg = document.getElementById('cookiesMsg');
                    if (!raw) {{
                        msg.style.color = '#ef4444';
                        msg.innerText = '❌ يرجى لصق كود الكوكيز أولاً!';
                        return;
                    }}
                    msg.style.color = '#38bdf8';
                    msg.innerText = '⏳ جاري حفظ وتفعيل الكوكيز...';
                    fetch('/api/import-cookies', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{cookies_json: raw}})
                    }})
                    .then(r => r.json())
                    .then(data => {{
                        if (data.ok) {{
                            msg.style.color = '#10b981';
                            msg.innerText = data.message;
                            document.getElementById('cookiesJsonInput').value = '';
                            setTimeout(() => location.reload(), 2500);
                        }} else {{
                            msg.style.color = '#ef4444';
                            msg.innerText = '❌ ' + (data.error || 'فشل الحفظ');
                        }}
                    }})
                    .catch(err => {{
                        msg.style.color = '#ef4444';
                        msg.innerText = '❌ خطأ: ' + err;
                    }});
                }}

                function checkGoogleSession() {{
                    const msg = document.getElementById('cookiesMsg');
                    msg.style.color = '#38bdf8';
                    msg.innerText = '⏳ جاري فحص حساب Google...';
                    fetch('/api/check-google-session', {{method: 'POST'}})
                    .then(r => r.json())
                    .then(data => {{
                        msg.style.color = data.ok ? '#10b981' : '#f59e0b';
                        msg.innerText = data.message;
                    }})
                    .catch(err => {{
                        msg.style.color = '#ef4444';
                        msg.innerText = '❌ خطأ أثناء الفحص: ' + err;
                    }});
                }}

                function toggleGmailMonitor() {{
                    const msg = document.getElementById('gmailActionMsg');
                    msg.style.color = '#38bdf8';
                    msg.innerText = '⏳ جاري التبديل...';
                    fetch('/api/toggle-gmail', {{method: 'POST'}})
                    .then(r => r.json())
                    .then(data => {{
                        msg.style.color = '#10b981';
                        msg.innerText = data.message;
                        setTimeout(() => location.reload(), 1500);
                    }})
                    .catch(err => {{
                        msg.style.color = '#ef4444';
                        msg.innerText = '❌ خطأ: ' + err;
                    }});
                }}

                function checkGmailNow() {{
                    const msg = document.getElementById('gmailActionMsg');
                    msg.style.color = '#38bdf8';
                    msg.innerText = '⏳ جاري فحص بريد Gmail الآن...';
                    fetch('/api/check-gmail-now', {{method: 'POST'}})
                    .then(r => r.json())
                    .then(data => {{
                        msg.style.color = data.ok ? '#10b981' : '#f59e0b';
                        msg.innerText = data.message;
                    }})
                    .catch(err => {{
                        msg.style.color = '#ef4444';
                        msg.innerText = '❌ خطأ: ' + err;
                    }});
                }}
            </script>
        </head>
        <body>
            <div class="container">
                <!-- Top Navigation & Authentication Bar -->
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; background: #1e293b; padding: 12px 20px; border-radius: 12px; border: 1px solid #334155;">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span style="font-size: 22px;">🤖</span>
                        <span style="font-weight: bold; font-size: 16px; color: #38bdf8;">نظام Google Meet الذكي (وضع صامت بدون كاميرا)</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <span style="font-size: 13px; color: #94a3b8; background: #0f172a; padding: 6px 14px; border-radius: 6px; border: 1px solid #334155;">
                            👤 الحساب المعتمد: <b style="color: #f8fafc;">{DASHBOARD_USER}</b>
                        </span>
                        <a href="/logout" class="btn" style="background: #ef4444; font-size: 13px; padding: 6px 14px; margin: 0;">🚪 تسجيل الخروج</a>
                    </div>
                </div>

                <!-- Case 1: Web Dashboard Input Form -->
                <div class="card" style="border: 2px solid #38bdf8; background: #0f172a;">
                    <h3 style="color: #38bdf8; margin-top: 0; display: flex; align-items: center; justify-content: space-between;">
                        <span>🎯 الخيار 1: إدخال رابط الاجتماع والانضمام المباشر</span>
                        <span style="font-size: 13px; color: #10b981;">الحالة: {bot_state['status']}</span>
                    </h3>
                    <form id="joinForm" onsubmit="submitMeeting(event)" style="display: flex; flex-direction: column; gap: 12px;">
                        <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                            <div style="flex: 3; min-width: 250px;">
                                <label style="font-size: 13px; color: #94a3b8; display: block; margin-bottom: 4px;">🔗 رابط Google Meet:</label>
                                <input type="text" id="meetUrlInput" placeholder="https://meet.google.com/xxx-yyyy-zzz" value="{bot_state.get('meet_url') or ''}" required
                                    style="width: 100%; box-sizing: border-box; background: #1e293b; border: 1px solid #475569; color: #f8fafc; padding: 11px 14px; border-radius: 6px; font-size: 14px;">
                            </div>
                            <div style="flex: 1; min-width: 140px;">
                                <label style="font-size: 13px; color: #94a3b8; display: block; margin-bottom: 4px;">👤 اسم الطالب:</label>
                                <input type="text" id="studentNameInput" placeholder="hazem" value="{bot_state.get('student_name') or 'hazem'}"
                                    style="width: 100%; box-sizing: border-box; background: #1e293b; border: 1px solid #475569; color: #f8fafc; padding: 11px 14px; border-radius: 6px; font-size: 14px;">
                            </div>
                        </div>
                        <div style="display: flex; gap: 10px; margin-top: 4px;">
                            <button type="submit" id="btnSubmit" class="btn" style="background: #0284c7; flex: 2; padding: 12px; font-size: 15px; margin-left: 0;">
                                🚀 بدء الانضمام للاجتماع الآن
                            </button>
                            <button type="button" onclick="stopMeeting()" class="btn" style="background: #ef4444; flex: 1; padding: 12px; font-size: 15px; margin-left: 0;">
                                🛑 إنهاء المكالمة ومغادرة
                            </button>
                        </div>
                        <div id="joinMsg" style="font-size: 13px; font-weight: bold;"></div>
                    </form>
                </div>

                <!-- Case 2: Telegram Bot Integration Card -->
                <div class="card" style="border: 2px solid #22c55e; background: #0f172a;">
                    <h3 style="color: #22c55e; margin-top: 0; display: flex; align-items: center; justify-content: space-between;">
                        <span>📱 الخيار 2: التحكم عبر بوت تيليجرام (@meethazem_bot)</span>
                        <a href="https://t.me/meethazem_bot" target="_blank" class="btn" style="background: #22c55e; color: #0f172a; font-size: 13px; margin: 0;">
                            💬 فتح البوت في Telegram
                        </a>
                    </h3>
                    <div style="font-size: 14px; color: #cbd5e1; line-height: 1.6;">
                        <p style="margin: 0 0 8px 0;">
                            ✨ <b>طريقة الاستخدام:</b> أرسل رابط الاجتماع إلى البوت <b>@meethazem_bot</b> في تيليجرام وسيقوم بالدخول فوراً باسم <b>hazem</b>.
                        </p>
                        <p style="margin: 0 0 8px 0; color: #86efac;">
                            📸 <b>إرسال الصور كل 5 دقائق:</b> سيتلقى الشخص الذي أرسل الرابط في تيليجرام لقطة شاشة حية للحصة <b>كل 5 دقائق تلقائياً</b> دون الحاجة لطلبها يدوياً!
                        </p>
                        <p style="margin: 0 0 10px 0; font-size: 13px; color: #94a3b8;">
                            المستلم المعتمد حالياً للصور: <b style="color: #f8fafc;">{tg_subscriber_info}</b>
                        </p>
                        <div style="display: flex; align-items: center; gap: 10px; margin-top: 8px;">
                            <button type="button" onclick="testTelegram()" class="btn" style="background: #0284c7; font-size: 13px; margin: 0;">
                                📸 تجربة إرسال صورة / إشعار لتيليجرام الآن
                            </button>
                            <span id="tgTestMsg" style="font-size: 13px; font-weight: bold;"></span>
                        </div>
                    </div>
                </div>

                <!-- Case 3: Automated Gmail Meet Link Watcher -->
                <div class="card" style="border: 2px solid #a855f7; background: #0f172a;">
                    <h3 style="color: #c084fc; margin-top: 0; display: flex; align-items: center; justify-content: space-between;">
                        <span>📧 الخيار 3: مراقبة بريد Gmail الدراسي تلقائياً</span>
                        <span id="gmailBadge" style="font-size: 13px; color: {gmail_badge_color};">الحالة: {gmail_status_text}</span>
                    </h3>
                    <div style="font-size: 14px; color: #cbd5e1; line-height: 1.6;">
                        <p style="margin: 0 0 8px 0;">
                            ⚡ <b>كيف تعمل؟</b> يقوم البوت بمراقبة بريدك المدرسي كل 30 ثانية في الخلفية. فور أن يرسل الشيخ أو المدرسة أي رابط Google Meet في الإيميل، يلتقطه البوت تلقائياً وينضم للحصة فوراً ويرسل لك إشعاراً في تيليجرام دون أي تدخل منك!
                        </p>
                        <div style="background: #1e293b; padding: 10px 14px; border-radius: 8px; margin: 10px 0; font-size: 13px;">
                            <div>⏱️ <b>آخر فحص للبريد:</b> <span id="gmailLastCheck" style="color: #38bdf8;">{bot_state.get('gmail_last_check', 'لم يتم بعد')}</span></div>
                            <div style="margin-top: 4px;">🔗 <b>آخر رابط ملتقط من Gmail:</b> <code id="gmailLastUrl" style="color: #10b981;">{bot_state.get('gmail_last_detected_url') or 'لا يوجد حتى الآن'}</code></div>
                        </div>
                        <div style="display: flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; align-items: center;">
                            <button type="button" onclick="toggleGmailMonitor()" class="btn" style="background: #8b5cf6; margin: 0;">
                                🔄 تبديل حالة المراقبة (تشغيل / إيقاف)
                            </button>
                            <button type="button" onclick="checkGmailNow()" class="btn" style="background: #3b82f6; margin: 0;">
                                🔍 فحص البريد الآن فوراً
                            </button>
                            <span id="gmailActionMsg" style="font-size: 13px; font-weight: bold;"></span>
                        </div>
                    </div>
                </div>

                <!-- Case 4: Google School Account Session / Cookies Import -->
                <div class="card" style="border: 2px solid #f59e0b; background: #0f172a;">
                    <h3 style="color: #fbbf24; margin-top: 0; display: flex; align-items: center; justify-content: space-between;">
                        <span>🍪 الخيار 4: استيراد كوكيز حساب المدرسة الرسمي (@hns-re2sd.dz)</span>
                        <span style="font-size: 13px; color: {state_badge_color};">{state_status_text}</span>
                    </h3>
                    <div style="font-size: 14px; color: #cbd5e1; line-height: 1.6;">
                        <p style="margin: 0 0 8px 0;">
                            🏫 الشيخ لا يقبل في الحصة إلا إيميل المدرسة الرسمي <b>@hns-re2sd.dz</b>. ادخل به في متصفحك العادي، واستخرج الكوكيز عبر إضافة <b>Cookie-Editor</b> والصقها هنا:
                        </p>
                        <div style="margin-bottom: 10px;">
                            <textarea id="cookiesJsonInput" rows="4" placeholder="الصق كود JSON المنسوخ من إضافة Cookie-Editor هنا (Export as JSON)..."
                                style="width: 100%; box-sizing: border-box; background: #1e293b; border: 1px solid #475569; color: #f8fafc; padding: 10px 14px; border-radius: 6px; font-family: monospace; font-size: 13px;"></textarea>
                        </div>
                        <div style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center;">
                            <button type="button" onclick="saveCookies()" class="btn" style="background: #f59e0b; margin: 0; color: #0f172a; font-weight: bold;">
                                💾 حفظ واعتماد حساب المدرسة الآن
                            </button>
                            <button type="button" onclick="checkGoogleSession()" class="btn" style="background: #0284c7; margin: 0;">
                                🔍 فحص صلاحية جلسة Google المسجلة
                            </button>
                            <span id="cookiesMsg" style="font-size: 13px; font-weight: bold;"></span>
                        </div>
                        <div style="margin-top: 10px; font-size: 12px; color: #94a3b8; background: #1e293b; padding: 8px 12px; border-radius: 6px;">
                            📌 <b>طريقة الحصول على الكوكيز في 3 خطوات سريعة:</b><br>
                            1. ثبت إضافة <b>Cookie-Editor</b> في Chrome أو Brave أو Edge.<br>
                            2. افتح <code>meet.google.com</code> بحساب المدرسة الرسمي <code>@hns-re2sd.dz</code>.<br>
                            3. اضغط أيقونة الإضافة 🍪 ثم <b>Export</b> ثم <b>Export as JSON</b> والصق الكود هنا واضغط حفظ!
                        </div>
                    </div>
                </div>

                <!-- Live Status Card -->
                <div class="card">
                    <h2>
                        <span>🤖 تشخيص البوت المباشر</span>
                        <span style="font-size: 14px; color: #94a3b8; font-weight: normal;">تحديث تلقائي كل 7 ثوانٍ</span>
                    </h2>

                    <div class="diag-box">
                        🔍 تشخيص الحالة الحالية: {bot_state['problem_diagnosis']}
                    </div>

                    <div class="item"><strong>المهمة الحالية:</strong> <span style="color: #38bdf8; font-weight: bold;">{bot_state['current_task']}</span></div>
                    <div class="item"><strong>الحالة المباشرة:</strong> {bot_state['status']}</div>
                    <div class="item"><strong>الرابط الفعلي للمتصفح:</strong> <code style="color: #cbd5e1; word-break: break-all;">{bot_state['current_url'] or 'غير متوفر'}</code></div>
                    <div class="item"><strong>عنوان الصفحة:</strong> {bot_state['page_title'] or 'غير متوفر'}</div>
                    <div class="item"><strong>اسم الطالب / الحساب:</strong> {bot_state['student_name']}</div>
                    <div class="item"><strong>استهلاك الذاكرة:</strong> {bot_state['memory_mb']:.1f} MB (ضمن حد 512MB)</div>

                    <div style="margin-top: 16px;">
                        <button class="btn btn-copy" onclick="copyFullLogs()">📋 نسخ السجل الكامل لإرساله للتحليل</button>
                        <a class="btn" href="/logs" target="_blank">📄 فتح السجل النصي الخام</a>
                        <a class="btn" href="/screenshot" target="_blank" style="background: #10b981;">📸 فتح لقطة الشاشة بالحجم الكامل</a>
                    </div>
                </div>

                <!-- Task History Card -->
                <div class="card">
                    <h3 style="color: #38bdf8; margin-top: 0;">📜 سجل المهام خطوة بخطوة:</h3>
                    <div class="log-scroll">
                        {rows_html if rows_html else '<p style="color: #94a3b8;">جاري بدء تسجيل المهام...</p>'}
                    </div>
                </div>

                <!-- Live Screenshot Preview Card -->
                <div class="card">
                    <h3 style="color: #38bdf8; margin-top: 0;">📸 معاينة حية لشاشة المتصفح (مكتوم وبدون شاشة خضراء):</h3>
                    <div class="preview">
                        <img src="/screenshot?t={int(time.time())}" alt="Live Preview" />
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass

def start_health_server():
    """Starts the background HTTP server for Render & Web UI."""
    port_str = os.getenv("PORT", "10000")
    try:
        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logger.info(f"Dashboard & Health Server running on http://0.0.0.0:{port}")
    except Exception as e:
        logger.warning(f"Could not start HTTP server on port {port_str}: {e}")

def update_memory_stats():
    """Updates memory usage stats."""
    try:
        if psutil:
            process = psutil.Process()
            mem_mb = process.memory_info().rss / (1024 * 1024)
            bot_state["memory_mb"] = mem_mb
            return mem_mb
    except Exception:
        pass
    return 0.0

async def fast_dismiss_and_mute(page: Page, student_name: str = "hazem"):
    """Dismisses mic/cam prompt modal and fills name if guest."""
    # 1. Dismiss audio/video prompt modal (English, Arabic, French)
    try:
        popup_btn = page.locator(
            'button[jsname="IbE0S"], '
            'button:has-text("Continuer sans micro"), '
            'button:has-text("Continuer sans"), '
            ':has-text("المتابعة بدون استخدام الميكروفون والكاميرا"), '
            ':has-text("المتابعة بدون"), '
            ':has-text("Continue without microphone and camera"), '
            ':has-text("Continue without"), '
            'button:has-text("Got it"), '
            'button:has-text("Compris"), '
            'button:has-text("حسناً")'
        ).first
        if await popup_btn.is_visible(timeout=2500):
            await popup_btn.click(force=True)
            logger.info("Dismissed mic/cam popup modal via Playwright locator.")
            await asyncio.sleep(0.5)
    except Exception:
        pass

    try:
        await page.evaluate('''() => {
            const btn = document.querySelector('button[jsname="IbE0S"], [role="dialog"] button, button.dqesce');
            if (btn) btn.click();
        }''')
    except Exception:
        pass

    # Ensure camera and mic are toggled off via hotkeys
    try:
        await page.keyboard.press("Control+e")
        await asyncio.sleep(0.2)
        await page.keyboard.press("Control+d")
    except Exception:
        pass

    # 2. Enter guest name if input field is present
    try:
        name_input = page.locator('input[type="text"][placeholder*="اسم" i], input[type="text"][placeholder*="name" i]').first
        if await name_input.is_visible(timeout=600):
            await name_input.fill(student_name)
            logger.info(f"Entered student name: {student_name}")
    except Exception:
        pass

async def fill_guest_name_if_present(page: Page, student_name: str) -> bool:
    """Fills the guest name input field if present."""
    name_selectors = [
        'input[type="text"][placeholder*="name" i]',
        'input[type="text"][placeholder*="اسم" i]',
        'input[type="text"][aria-label*="name" i]',
        'input[type="text"][aria-label*="اسم" i]'
    ]
    for selector in name_selectors:
        try:
            name_input = page.locator(selector).first
            if await name_input.is_visible(timeout=1000):
                await name_input.fill(student_name)
                logger.info(f"Successfully entered student name: '{student_name}'")
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue
    return False

async def is_in_call(page: Page) -> bool:
    """Checks if the bot is actively inside the call (strictly in-call controls only)."""
    indicators = [
        'button[aria-label*="Leave call" i]',
        'button[aria-label*="Quitter l\'appel" i]',
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
    return False

async def click_join_button(page: Page) -> tuple[bool, str]:
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
                txt = (await modal_dismiss.inner_text()).strip().replace("\n", " ")
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
            'button[jsname="Qx7uuf"], '
            '[role="button"][jsname="Qx7uuf"], '
            'button[jsname="b0t70b"], '
            '[role="button"][jsname="b0t70b"], '
            'button:has-text("Participer à la réunion"), '
            'button:has-text("Demander à participer"), '
            'button:has-text("Rejoindre"), '
            'button:has-text("انضم الآن"), '
            'button:has-text("الانضمام الآن"), '
            'button:has-text("Join now"), '
            'button:has-text("Ask to join"), '
            'button:has-text("Switch here"), '
            'button:has-text("التبديل إلى هنا"), '
            'button:has-text("Join here too"), '
            'button:has-text("طلب الانضمام"), '
            '[role="button"]:has-text("انضم الآن"), '
            '[role="button"]:has-text("الانضمام الآن"), '
            '[role="button"]:has-text("طلب الانضمام"), '
            '[role="button"]:has-text("Join now"), '
            '[role="button"]:has-text("Ask to join"), '
            '[role="button"]:has-text("Switch here"), '
            '[role="button"]:has-text("التبديل إلى هنا")'
        ).first
        try:
            if await btn.is_visible(timeout=600):
                txt = (await btn.inner_text()).strip().replace("\n", " ")
                try:
                    await btn.click(force=True)
                except Exception:
                    await btn.evaluate("el => el.click()")
                logger.info(f"Successfully clicked join button: '{txt}'")
                return True, txt
        except Exception:
            pass

        # 3b. JavaScript DOM Fallback to click any visible join button
        try:
            clicked_txt = await page.evaluate('''() => {
                const keywords = [
                    "انضم الآن", "الانضمام الآن", "طلب الانضمام", "انضمام",
                    "Join now", "Ask to join", "Switch here", "Join here too",
                    "Participer à la réunion", "Demander à participer", "Rejoindre"
                ];
                const elements = Array.from(document.querySelectorAll('button, [role="button"], div[jsname="Qx7uuf"], button[jsname="Qx7uuf"]'));
                for (const el of elements) {
                    if (el.offsetParent === null) continue;
                    const text = (el.innerText || el.getAttribute("aria-label") || "").trim();
                    for (const kw of keywords) {
                        if (text.toLowerCase().includes(kw.toLowerCase())) {
                            el.click();
                            return text;
                        }
                    }
                }
                return null;
            }''')
            if clicked_txt:
                logger.info(f"Successfully clicked join button via JS evaluator: '{clicked_txt}'")
                return True, clicked_txt
        except Exception:
            pass

        # 4. Check if already inside call (strict in-call indicators only)
        if await is_in_call(page):
            logger.info("Confirmed inside call room!")
            return True, "تم الدخول المباشر"

        await asyncio.sleep(1)

    logger.warning("Join button was not found within 25 seconds.")
    return False, ""

async def diagnose_post_click_state(page: Page) -> dict:
    """Forensic analysis performed immediately after clicking the join button."""
    result = {
        "status": "info",
        "problem_type": "UNKNOWN",
        "summary": "جاري تحليل الاستجابة...",
        "details": "",
        "url": page.url,
        "title": await page.title()
    }

    # 0. Check if redirected out of meeting
    curr_u = page.url.lower()
    if "workspace.google.com" in curr_u or curr_u.rstrip("/").endswith("meet.google.com"):
        result["status"] = "error"
        result["problem_type"] = "DENIED_OR_ENDED"
        result["summary"] = "❌ تم التحويل خارج الاجتماع (رفض المضيف الطلب أو انتهت الحصة)"
        result["details"] = f"الرابط الحالي: {page.url}"
        return result

    # 1. Check if inside call
    if await is_in_call(page):
        result["status"] = "success"
        result["problem_type"] = "IN_CALL"
        result["summary"] = "🎉 نجاح كامل! البوت داخل غرفة الاجتماع رسمياً"
        result["details"] = f"تم تفعيل عناصر المكالمة (الدردشة، زر الإنهاء، قائمة المشاركين). الرابط: {page.url}"
        return result

    # 2. Check for genuine security restriction screens
    error_patterns = [
        ("Sorry, we've encountered a problem", "عذراً، حدثت مشكلة", "DATACENTER_IP_BLOCKED", "حظر خوادم Render أو سقوط اتصال WebRTC"),
        ("Returning to home screen in", "الرجوع إلى الشاشة الرئيسية خلال", "DATACENTER_IP_BLOCKED", "جوجل أسقط الاتصال وبدأ العد التنازلي للرجوع للشاشة الرئيسية"),
        ("You can't join this video call", "لا يمكنك الانضمام إلى مكالمة الفيديو هذه", "HOST_SECURITY_RESTRICTED", "إعدادات الأمان لدى المعلم تمنع دخول غير المدعوين"),
        ("You can't join this video call", "لا يُمكنك الانضمام إلى مكالمة الفيديو هذه", "HOST_SECURITY_RESTRICTED", "المعلم حصر الدخول على حسابات المدرسة (@hns-re2sd.dz) - جلسة الدخول منتهية"),
        ("Your meeting is safe", "اجتماعك آمن", "HOST_SECURITY_RESTRICTED", "المعلم حصر الدخول على حسابات المدرسة (@hns-re2sd.dz) - جلسة الدخول منتهية"),
        ("Someone in the call denied your request", "رفض أحد المشاركين طلب انضمامك", "DENIED_BY_HOST", "المعلم ضغط على رفض دخولك"),
        ("This call is full", "الاجتماع ممتلئ", "CALL_FULL", "الاجتماع وصل للحد الأقصى للعدد المسموح"),
        ("Your meeting is not ready", "اجتماعك غير جاهز", "MEETING_NOT_READY", "الاجتماع لم يتم تفعيله بعد")
    ]
    
    for en_text, ar_text, p_type, ar_desc in error_patterns:
        try:
            el = page.locator(f'text="{en_text}", text="{ar_text}"').first
            if await el.is_visible(timeout=500):
                result["status"] = "error"
                result["problem_type"] = p_type
                result["summary"] = f"❌ تم رصد منع من Google: {ar_desc}"
                result["details"] = f"النص الظاهر على الشاشة: '{ar_text}' | الرابط: {page.url}"
                return result
        except Exception:
            pass

    # 3. Check for waiting room
    waiting_indicators = [
        ("Asking to join...", "جاري إرسال طلب الانضمام إلى المضيف...", "WAITING_ROOM"),
        ("طلب الانضمام...", "جاري إرسال طلب الانضمام إلى المضيف...", "WAITING_ROOM"),
        ("You'll join the call when someone lets you in", "البوت في غرفة الانتظار بانتظار موافقة المعلم", "WAITING_ROOM"),
        ("ستنضم إلى المكالمة عندما يسمح لك أحد", "البوت في غرفة الانتظار بانتظار موافقة المعلم", "WAITING_ROOM"),
        ("Waiting for the host to join", "المعلم لم يفتح الحصة بعد (في انتظار انضمام المضيف)", "HOST_NOT_STARTED"),
        ("في انتظار انضمام المضيف", "المعلم لم يفتح الحصة بعد (في انتظار انضمام المضيف)", "HOST_NOT_STARTED")
    ]
    for ind_en, ind_ar, p_type in waiting_indicators:
        try:
            el = page.locator(f'text="{ind_en}", text="{ind_ar}"').first
            if await el.is_visible(timeout=500):
                result["status"] = "warning"
                result["problem_type"] = p_type
                result["summary"] = f"⏳ في غرفة الانتظار: {ind_ar}"
                result["details"] = f"تم إرسال الطلب، والحالة الحالية على الشاشة: '{ind_en}'"
                return result
        except Exception:
            pass

    # 4. Check if button is still visible
    join_btn = page.locator(
        'button:has-text("Participer à la réunion"), '
        'button:has-text("Demander à participer"), '
        'button:has-text("انضم الآن"), '
        'button:has-text("الانضمام الآن"), '
        'button:has-text("Join now"), '
        'button:has-text("Switch here"), '
        'button:has-text("التبديل إلى هنا"), '
        'button:has-text("Join here too"), '
        'button:has-text("Ask to join"), '
        'button:has-text("طلب الانضمام")'
    ).first
    if await join_btn.is_visible(timeout=500):
        result["status"] = "warning"
        result["problem_type"] = "BUTTON_STUCK"
        result["summary"] = "⚠️ تم الضغط على الزر لكن الزر ما زال ظاهراً على الشاشة"
        result["details"] = "المتصفح استلم الضغطة ولكن جوجل لم يستجب فوراً أو جاري التحميل"
        return result

    result["status"] = "info"
    result["problem_type"] = "CONNECTING"
    result["summary"] = "🔄 جاري الاتصال بالمكالمة وتجهيز الصوت والفيديو..."
    result["details"] = f"الرابط الحالي: {page.url}"
    return result

async def check_for_error_screen(page: Page) -> tuple[bool, str]:
    """Checks if Google Meet displays an error or return-to-home screen and returns (has_error, error_text)."""
    error_selectors = [
        (':has-text("Sorry, we\'ve encountered a problem")', "عذراً، حدثت مشكلة (سقوط اتصال WebRTC)"),
        (':has-text("عذراً، حدثت مشكلة")', "عذراً، حدثت مشكلة (سقوط اتصال WebRTC)"),
        ('h1:has-text("Returning to home screen")', "الرجوع إلى الشاشة الرئيسية (سقوط الاتصال)"),
        ('h1:has-text("الرجوع إلى الشاشة الرئيسية")', "الرجوع إلى الشاشة الرئيسية (سقوط الاتصال)"),
        (':has-text("Returning to home screen in")', "الرجوع إلى الشاشة الرئيسية (سقوط الاتصال)"),
        (':has-text("الرجوع إلى الشاشة الرئيسية خلال")', "الرجوع إلى الشاشة الرئيسية (سقوط الاتصال)"),
        (':has-text("العودة إلى الشاشة الرئيسية")', "الرجوع إلى الشاشة الرئيسية (سقوط الاتصال)"),
        (':has-text("Someone in the call denied your request")', "رفض أحد المشاركين طلب انضمامك (المعلم رفض الدخول)"),
        (':has-text("رفض أحد المشاركين طلب انضمامك")', "رفض أحد المشاركين طلب انضمامك (المعلم رفض الدخول)"),
        (':has-text("You can\'t join this video call")', "لا يمكنك الانضمام إلى مكالمة الفيديو هذه"),
        (':has-text("You cannot join this video call")', "لا يمكنك الانضمام إلى مكالمة الفيديو هذه"),
        (':has-text("لا يُمكنك الانضمام")', "لا يمكنك الانضمام إلى مكالمة الفيديو (الاجتماع محصور لحساب المدرسة @hns-re2sd.dz فقط - الكوكيز منتهية)"),
        (':has-text("لا يمكنك الانضمام")', "لا يمكنك الانضمام إلى مكالمة الفيديو (الاجتماع محصور لحساب المدرسة @hns-re2sd.dz فقط - الكوكيز منتهية)"),
        (':has-text("اجتماعك آمن")', "اجتماعك آمن (الاجتماع محصور لحساب المدرسة @hns-re2sd.dz فقط - الكوكيز منتهية)"),
        ('button:has-text("رجوع إلى الشاشة الرئيسية")', "تم حظر الدخول إلى الاجتماع (مطلوب حساب المدرسة)")
    ]
    for sel, desc in error_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=600):
                return True, desc
        except Exception:
            pass
    return False, ""

async def wait_until_admitted(page: Page, timeout_seconds: int = 1800) -> bool:
    """Waits for the meeting host to admit the bot, or detects immediate entry in open meets."""
    record_task("المهمة 8: انتظار قبول الدخول", "info", f"انتظار الموافقة أو الدخول المباشر (المهلة: {timeout_seconds} ثانية)...", f"الرابط: {page.url}")
    start_time = time.time()
    last_check_time = 0
    while time.time() - start_time < timeout_seconds:
        if stop_requested:
            return False

        if await is_in_call(page):
            return True

        # Check if redirected away from meeting room
        curr_url = page.url.lower()
        if "workspace.google.com" in curr_url or curr_url.rstrip("/").endswith("meet.google.com"):
            record_task(
                "المهمة 8: تم الخروج أو الرفض",
                "error",
                "❌ تم التحويل خارج الاجتماع (رفض المضيف الطلب أو انتهت الحصة)",
                f"الرابط الحالي: {page.url}",
                problem_type="DENIED_OR_ENDED"
            )
            logger.warning(f"Redirected away from meeting: {page.url}. Host denied or meeting ended.")
            return False

        has_err, desc = await check_for_error_screen(page)
        if has_err:
            record_task("المهمة 8: خطأ حظر سحابي مرصود", "error", f"❌ {desc}", "Google Meet أسقط الاتصال أثناء الانتظار", problem_type="DISCONNECTED")
            return False

        if time.time() - last_check_time > 15:
            last_check_time = time.time()
            elapsed_sec = int(time.time() - start_time)
            record_task(
                "المهمة 8: انتظار قبول الدخول",
                "info",
                f"ما زال في انتظار قبول المضيف (مضى {elapsed_sec} ثانية)...",
                f"الرابط: {page.url}"
            )
            try:
                bot_state["last_screenshot"] = await page.screenshot(type="png", timeout=3500)
            except Exception:
                pass

        await asyncio.sleep(2)

    record_task("المهمة 8: مهلة الانتظار", "warning", "انتهت مهلة انتظار قبول المضيف دون رد", problem_type="WAITING_TIMEOUT")
    return False

async def open_chat_panel(page: Page) -> bool:
    """Clicks the chat icon inside the meeting room to open the chat panel."""
    chat_selectors = [
        'button[aria-label*="chat" i]',
        'button[aria-label*="الدردشة" i]',
        'button[data-panel-id="2"]',
        'button[aria-label*="إرسال رسالة" i]',
        'button[jsname="A5il2e"]'
    ]
    for sel in chat_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                logger.info(f"Clicked chat icon using selector: {sel}")
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue
    return False

async def send_chat_message(page: Page, message: str) -> bool:
    """Types and sends a message in the Google Meet chat box."""
    input_selectors = [
        'textarea[aria-label*="Send a message" i]',
        'textarea[aria-label*="إرسال رسالة" i]',
        'textarea[aria-label*="chat" i]',
        'textarea[name="chatTextInput"]',
        'textarea'
    ]
    for sel in input_selectors:
        try:
            box = page.locator(sel).first
            if await box.is_visible(timeout=2000):
                await box.fill(message)
                await asyncio.sleep(0.3)
                await box.press("Enter")
                logger.info(f"Auto-reply sent: '{message}'")
                return True
        except Exception:
            continue
    return False

async def monitor_chat_and_stay_alive(page: Page, student_name: str = "hazem"):
    """
    Monitors chat messages, replies automatically, and dispatches periodic 
    5-minute screenshots to the Telegram subscriber.
    """
    global telegram_subscriber_chat_id, stop_requested
    logger.info(f"Starting chat monitor & 5-minute photo dispatcher. Monitored triggers: {TRIGGER_KEYWORDS}")
    seen_messages: Set[str] = set()
    last_reply_time = 0.0
    start_session_time = time.time()
    last_telegram_screenshot_time = time.time()
    max_duration_seconds = MAX_DURATION_MINUTES * 60

    # Open chat panel once at session start
    logger.info("Opening chat panel once...")
    await open_chat_panel(page)
    await asyncio.sleep(1)

    # Dismiss any leftover modal inside the call
    try:
        in_call_modal = page.locator(
            'button[jsname="IbE0S"], '
            'button:has-text("Continuer sans micro"), '
            'button:has-text("Continuer sans"), '
            ':has-text("المتابعة بدون"), '
            ':has-text("Continue without"), '
            'button:has-text("Compris"), '
            'button:has-text("Got it")'
        ).first
        if await in_call_modal.is_visible(timeout=1000):
            await in_call_modal.click(force=True)
            logger.info("Dismissed in-call mic popup modal.")
    except Exception:
        pass

    # Ensure camera is off and mic is muted immediately upon entry
    try:
        cam_on = page.locator('button[aria-label*="Turn off camera" i], button[aria-label*="إيقاف الكاميرا" i]').first
        if await cam_on.is_visible(timeout=500):
            await cam_on.click()
            logger.info("Turned off camera inside call.")
    except Exception:
        pass

    try:
        mic_on = page.locator('button[aria-label*="Turn off microphone" i], button[aria-label*="كتم صوت الميكروفون" i]').first
        if await mic_on.is_visible(timeout=500):
            await mic_on.click()
            logger.info("Muted microphone inside call.")
    except Exception:
        pass

    # Send immediate first screenshot to Telegram subscriber upon entry
    if telegram_subscriber_chat_id:
        try:
            first_shot = await page.screenshot(type="png", timeout=3500)
            bot_state["last_screenshot"] = first_shot
            caption = (
                "🎉 <b>تم الدخول بنجاح إلى الاجتماع!</b>\n\n"
                f"👤 <b>اسم الطالب:</b> {student_name}\n"
                f"🔗 <b>الرابط:</b> {bot_state.get('meet_url')}\n"
                f"⏱️ <b>وقت الدخول:</b> {time.strftime('%H:%M:%S')}\n\n"
                "📸 <i>سأرسل لك لقطة شاشة كل 5 دقائق لمتابعة حضورك تلقائياً.</i>"
            )
            await send_telegram_photo(telegram_subscriber_chat_id, first_shot, caption)
            logger.info(f"Sent initial entry screenshot to Telegram chat {telegram_subscriber_chat_id}")
        except Exception as te:
            logger.warning(f"Failed to send initial Telegram screenshot: {te}")

    while True:
        if stop_requested:
            logger.info("Stop requested by user. Leaving meeting.")
            break

        elapsed = time.time() - start_session_time
        if elapsed > max_duration_seconds:
            logger.info(f"Max class duration ({MAX_DURATION_MINUTES} mins) reached. Exiting.")
            bot_state["status"] = "انتهت مدة الحصة المحددة."
            break

        try:
            ended = page.locator('text="You left the meeting", text="غادرت الاجتماع", text="The call has ended"')
            if await ended.is_visible(timeout=500):
                bot_state["status"] = "انتهى الاجتماع أو تم إخراج البوت."
                logger.info("Meeting has ended.")
                break
        except Exception:
            pass

        # Note: Periodic 5-minute screenshots are handled continuously by periodic_telegram_screenshot_dispatcher

        # Scan for chat messages
        try:
            message_elements = await page.locator(
                'div[data-message-text], div[jscontroller][data-timestamp], div[role="listitem"]'
            ).all()

            for elem in message_elements:
                try:
                    text = await elem.inner_text()
                    text = text.strip()
                    if not text or text in seen_messages:
                        continue

                    seen_messages.add(text)
                    logger.info(f"[Chat Observed] {text}")

                    text_lower = text.lower()
                    should_reply = any(kw in text_lower for kw in TRIGGER_KEYWORDS)

                    if should_reply:
                        if time.time() - last_reply_time > AUTO_REPLY_COOLDOWN:
                            logger.info(f"Trigger keyword detected in message: '{text}'. Sending auto-reply...")
                            sent = await send_chat_message(page, AUTO_REPLY_MESSAGE)
                            if sent:
                                last_reply_time = time.time()
                                record_task("رد شات تلقائي", "info", f"تم إرسال الرد التلقائي: {AUTO_REPLY_MESSAGE}", f"الرسالة المحفزة: {text}")
                                if telegram_subscriber_chat_id:
                                    tg_alert = (
                                        f"💬 <b>تنبيه مناداة في شات الحصة!</b>\n\n"
                                        f"📩 <b>الرسالة:</b> {text}\n"
                                        f"🤖 <b>تم الرد تلقائياً:</b> {AUTO_REPLY_MESSAGE}"
                                    )
                                    await send_telegram_message(telegram_subscriber_chat_id, tg_alert)
                        else:
                            logger.info("Trigger keyword detected, but auto-reply cooldown is active.")
                except Exception:
                    continue
        except Exception:
            pass

        try:
            bot_state["last_screenshot"] = await page.screenshot(type="png", timeout=3500)
        except Exception:
            pass

        update_memory_stats()
        await asyncio.sleep(5)

async def check_gmail_for_meet_links(browser) -> tuple[bool, str, Optional[str]]:
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
            meet_regex = re.compile(r"https?://meet\.google\.com/[a-zA-Z0-9_-]+")
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

            meet_regex = re.compile(r"meet\.google\.com/([a-zA-Z]{3}-[a-zA-Z]{4}-[a-zA-Z]{3})")
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
        return False, f"⚠️ حدث خطأ أثناء فحص بريد Gmail: {ge}", None

async def gmail_monitor_loop(browser):
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
                        "📧 <b>تم اكتشاف رابط Google Meet جديد في بريد Gmail!</b>\n\n"
                        f"🔗 <b>الرابط:</b> {found_link}\n"
                        "👤 <b>الحساب:</b> حساب المدرسة الرسمي (@hns-re2sd.dz)\n\n"
                        "🚀 <i>جاري الانضمام للحصة فوراً وتلقائياً...</i>"
                    )
                    await send_telegram_message(sub_id, tg_msg)

                # Automatically start meeting!
                start_or_restart_meeting(found_link, STUDENT_NAME or "hazem")
        except Exception as e:
            logger.debug(f"Error in gmail_monitor_loop: {e}")

def get_storage_state() -> Optional[str]:
    """Finds or decodes Google authentication state if present."""
    if os.path.exists(STATE_FILE):
        # Auto-sanitize cookies in state.json to ensure Chromium doesn't drop SID/HSID
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            if isinstance(state_data, dict) and "cookies" in state_data:
                orig_cookies = state_data.get("cookies", [])
                sanitized = format_cookies_for_playwright(orig_cookies)
                # Check if any sameSite=None had secure=False
                needs_update = False
                for c in orig_cookies:
                    if str(c.get("sameSite", "")).lower() in ("none", "no_restriction") and not c.get("secure", False):
                        needs_update = True
                        break
                if needs_update or len(sanitized) != len(orig_cookies):
                    state_data["cookies"] = sanitized
                    with open(STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(state_data, f, indent=2)
                    logger.info("Auto-sanitized state.json cookies for Chromium compatibility.")
        except Exception:
            pass
        return STATE_FILE

    if os.path.exists(COOKIES_FILE):
        try:
            with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                raw_cookies = json.load(f)
            if isinstance(raw_cookies, list):
                state_data = {"cookies": format_cookies_for_playwright(raw_cookies), "origins": []}
            elif isinstance(raw_cookies, dict):
                state_data = {
                    "cookies": format_cookies_for_playwright(raw_cookies.get("cookies", [])),
                    "origins": raw_cookies.get("origins", [])
                }
            else:
                state_data = raw_cookies
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
            logger.info("Converted 'cookies.json' to 'state.json' successfully!")
            return STATE_FILE
        except Exception as e:
            logger.warning(f"Failed to convert cookies.json: {e}")

    if GOOGLE_STORAGE_STATE_RAW:
        try:
            decoded = GOOGLE_STORAGE_STATE_RAW
            if not decoded.startswith("{") and not decoded.startswith("["):
                decoded = base64.b64decode(GOOGLE_STORAGE_STATE_RAW).decode("utf-8")
            data = json.loads(decoded)
            if isinstance(data, list):
                state_data = {"cookies": format_cookies_for_playwright(data), "origins": []}
            elif isinstance(data, dict):
                state_data = {
                    "cookies": format_cookies_for_playwright(data.get("cookies", [])),
                    "origins": data.get("origins", [])
                }
            else:
                state_data = data
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
            logger.info("Loaded and formatted Google session from GOOGLE_STORAGE_STATE environment variable!")
            return STATE_FILE
        except Exception as e:
            logger.warning(f"Failed to parse GOOGLE_STORAGE_STATE: {e}")

    return None

async def run_single_meeting_flow(browser, target_url: str, student_name: str):
    """Executes a single meeting attempt loop until admitted or stopped."""
    global current_page_ref, current_context_ref, stop_requested

    storage_state_path = get_storage_state()
    context_kwargs = {
        "viewport": {"width": 1280, "height": 720},
        "locale": BROWSER_LOCALE,
        "user_agent": MODERN_CHROME_UA
    }
    if storage_state_path:
        context_kwargs["storage_state"] = storage_state_path

    context = await browser.new_context(**context_kwargs)
    current_context_ref = context
    page = await context.new_page()
    current_page_ref = page

    max_attempts = 50
    try:
        for attempt in range(1, max_attempts + 1):
            if stop_requested:
                logger.info("Meeting stopped by user request.")
                break

            target_url = clean_meet_url(target_url)

            # Recreate clean page on retries to discard dirty state or aborted scripts
            if attempt > 1:
                try:
                    if not page.is_closed():
                        await page.close()
                except Exception:
                    pass
                try:
                    page = await context.new_page()
                    current_page_ref = page
                except Exception:
                    pass

            # Check if page or context was closed, and recreate cleanly
            if page.is_closed():
                logger.warning("Target page was closed. Recreating fresh context and page...")
                try:
                    storage_state_path = get_storage_state()
                    if storage_state_path:
                        context_kwargs["storage_state"] = storage_state_path
                    context = await browser.new_context(**context_kwargs)
                    current_context_ref = context
                    page = await context.new_page()
                    current_page_ref = page
                except Exception as e:
                    logger.error(f"Failed to recreate page/context: {e}")
                    break

            try:
                # المهمة 3: فتح رابط الاجتماع
                record_task(
                    "المهمة 3: فتح رابط الاجتماع",
                    "info",
                    f"جاري فتح الرابط (محاولة {attempt}/{max_attempts})...",
                    f"الهدف: {target_url}"
                )
                logger.info(f"Opening Meet URL (Attempt {attempt}/{max_attempts}): {target_url}")
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                except Exception as goto_err:
                    err_txt = str(goto_err).lower()
                    if "err_aborted" in err_txt:
                        logger.warning(f"page.goto reported net::ERR_ABORTED (handled - Meet redirect in progress): {goto_err}")
                        await asyncio.sleep(2)
                    else:
                        raise goto_err
                await asyncio.sleep(2)

                bot_state["current_url"] = page.url
                bot_state["page_title"] = await page.title()

                # المهمة 4: فحص حظر جوجل والتحويل الأمني
                if "accounts.google.com" in page.url:
                    record_task(
                        "المهمة 4: فحص أمان Google",
                        "error",
                        "❌ تم التحويل لصفحة تسجيل الدخول (الجلسة منتهية أو حساب مفصول)",
                        f"الرابط: {page.url}",
                        problem_type="SESSION_EXPIRED"
                    )
                    logger.warning("Redirected to Google Login. Automatically clearing cookies and falling back to Guest Mode...")
                    await context.clear_cookies()
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(2)
                    bot_state["current_url"] = page.url
                    bot_state["page_title"] = await page.title()
                else:
                    record_task(
                        "المهمة 4: فحص أمان Google",
                        "success",
                        "✅ تم الوصول لصفحة Google Meet مباشرة بدون تحويل أمني",
                        f"العنوان: {bot_state['page_title']}"
                    )

                # المهمة 5: التعامل مع النوافذ وكتم الصوت والفيديو
                record_task("المهمة 5: كتم الصوت وإغلاق الكاميرا", "info", "جاري كتم المايك والكاميرا وتخطي النوافذ التنبيهية فوراً...")
                await fast_dismiss_and_mute(page, student_name)
                record_task("المهمة 5: كتم الصوت وإغلاق الكاميرا", "success", "تم إغلاق المايك والكاميرا وتخطي النوافذ التنبيهية بنجاح.")

                # المهمة 6: فحص شاشة الاستعداد واكتشاف الأزرار المتاحة
                record_task("المهمة 6: فحص أزرار الانضمام", "info", "جاري البحث عن زر الدخول (Join now أو Switch here أو Ask to join)...")
                try:
                    bot_state["last_screenshot"] = await page.screenshot(type="png", timeout=3500)
                except Exception:
                    pass

                # فحص هل البوت داخل الحصة بالفعل
                if await is_in_call(page):
                    record_task("المهمة 9: التواجد داخل الحصة", "success", f"🎉 تم الدخول بنجاح! البوت حاضر ومستقر داخل الحصة باسم {student_name}.", "", problem_type="IN_CALL")
                    logger.info(f"Admitted successfully into class! Present as {student_name}. Monitoring chat and stay alive...")
                    await monitor_chat_and_stay_alive(page, student_name)
                    logger.info("Class session finished. Terminating permanently without retrying.")
                    break

                # المهمة 7: الضغط على زر الانضمام
                joined, clicked_label = await click_join_button(page)
                if not joined and not await is_in_call(page):
                    await asyncio.sleep(1)
                    joined, clicked_label = await click_join_button(page)

                if joined or await is_in_call(page):
                    if not joined:
                        clicked_label = "الدخول المباشر لحساب المدرسة"
                    record_task(
                        "المهمة 7: الضغط على زر الانضمام",
                        "success",
                        f"✅ تم الضغط بنجاح على: {clicked_label}",
                        f"الرابط لحظة الضغط: {page.url}"
                    )

                    # المهمة 8: تشخيص ما بعد الضغط الفوري
                    await asyncio.sleep(3)
                    post_diag = await diagnose_post_click_state(page)
                    record_task(
                        "المهمة 8: تشخيص ما بعد الضغط",
                        post_diag["status"],
                        post_diag["summary"],
                        post_diag["details"],
                        problem_type=post_diag["problem_type"]
                    )

                    try:
                        bot_state["last_screenshot"] = await page.screenshot(type="png", timeout=3500)
                    except Exception:
                        pass

                    # فحص هل دخل فوراً (Open Meet)
                    if post_diag["problem_type"] == "IN_CALL" or await is_in_call(page):
                        record_task("المهمة 9: التواجد داخل الحصة", "success", f"🎉 تم الدخول بنجاح! البوت حاضر ومستقر داخل الحصة باسم {student_name}.", "", problem_type="IN_CALL")
                        logger.info(f"Admitted successfully into class! Present as {student_name}. Monitoring chat and stay alive...")
                        await monitor_chat_and_stay_alive(page, student_name)
                        logger.info("Class session finished. Terminating permanently without retrying.")
                        break

                    # فحص هل سقط في خطأ سحابي حقيقي
                    has_err, err_desc = await check_for_error_screen(page)
                    if has_err:
                        record_task(
                            "المهمة 8: خطأ اتصال مرصود",
                            "error",
                            f"❌ {err_desc}",
                            "جوجل أسقط الاتصال. سيتم إعادة المحاولة بعد 10 ثوانٍ...",
                            problem_type="DATACENTER_IP_BLOCKED"
                        )
                        await asyncio.sleep(10)
                        continue

                    # انتظار قبول المضيف إذا كان في غرفة الانتظار
                    admitted = await wait_until_admitted(page, timeout_seconds=ADMISSION_TIMEOUT_SECONDS)
                    if admitted:
                        record_task("المهمة 9: التواجد داخل الحصة", "success", f"🎉 تم قبول الدخول من المضيف! البوت حاضر ومستقر الآن باسم {student_name}.", "", problem_type="IN_CALL")
                        logger.info(f"Admitted successfully into class! Present as {student_name}. Monitoring chat and stay alive...")
                        await monitor_chat_and_stay_alive(page, student_name)
                        logger.info("Class session finished. Terminating permanently without retrying.")
                        break
                else:
                    if await is_in_call(page):
                        record_task("المهمة 9: التواجد داخل الحصة", "success", f"🎉 تم الدخول بنجاح! البوت حاضر ومستقر داخل الحصة باسم {student_name}.", "", problem_type="IN_CALL")
                        logger.info(f"Admitted successfully into class! Present as {student_name}. Monitoring chat and stay alive...")
                        await monitor_chat_and_stay_alive(page, student_name)
                        logger.info("Class session finished. Terminating permanently without retrying.")
                        break
                    record_task(
                        "المهمة 7: الضغط على زر الانضمام",
                        "error",
                        "❌ لم يتم العثور على أي زر انضمام ظاهر على الشاشة!",
                        f"الرابط: {page.url} | العنوان: {bot_state['page_title']}",
                        problem_type="BUTTON_NOT_FOUND"
                    )
                    await asyncio.sleep(5)
                    continue

                record_task("إعادة المحاولة", "info", "انتهت دورة الانتظار. جاري إعادة المحاولة بعد 8 ثوانٍ...")
                await asyncio.sleep(8)

            except PlaywrightTimeoutError as te:
                record_task("خطأ مهلة", "warning", f"حدث بطء أو مهلة في الاتصال: {te}. جاري إعادة المحاولة...", "", problem_type="TIMEOUT")
                logger.warning(f"Timeout on attempt {attempt}: {te}. Retrying...")
                await asyncio.sleep(5)
            except Exception as e:
                err_msg = str(e)
                if "closed" in err_msg.lower() and stop_requested:
                    logger.info("Page closed as requested. Stopping meeting loop.")
                    break
                record_task("خطأ عام", "error", f"خطأ غير متوقع: {e}", "", problem_type="EXCEPTION")
                logger.error(f"Error on attempt {attempt}: {e}", exc_info=True)
                await asyncio.sleep(5)
    finally:
        try:
            await context.close()
        except Exception:
            pass
        current_page_ref = None
        current_context_ref = None

async def run_bot():
    """Main orchestration loop with Telegram polling & Web UI triggers."""
    global current_page_ref, async_loop, new_meeting_event, MEET_URL, STUDENT_NAME, global_browser_ref
    async_loop = asyncio.get_running_loop()
    new_meeting_event = asyncio.Event()

    start_health_server()

    # Start Telegram background listener
    asyncio.create_task(telegram_polling_loop())

    # Start independent 5-Minute Telegram Periodic Screenshot Dispatcher
    asyncio.create_task(periodic_telegram_screenshot_dispatcher())

    chromium_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--mute-audio",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-default-apps",
        "--no-first-run",
        "--hide-scrollbars",
        "--disable-breakpad",
        "--disable-blink-features=AutomationControlled"
    ]

    launch_kwargs = {
        "headless": HEADLESS,
        "args": chromium_args
    }
    proxy_server = os.getenv("PROXY_SERVER", "").strip()
    if proxy_server:
        proxy_dict = {"server": proxy_server}
        if os.getenv("PROXY_USERNAME"):
            proxy_dict["username"] = os.getenv("PROXY_USERNAME")
        if os.getenv("PROXY_PASSWORD"):
            proxy_dict["password"] = os.getenv("PROXY_PASSWORD")
        launch_kwargs["proxy"] = proxy_dict
        logger.info(f"Using proxy: {proxy_server}")

    record_task(
        "المهمة 1: إعداد المتصفح",
        "info",
        "تم إطلاق متصفح Chromium بالإعدادات المحسنة وتجاوز أدوات الكشف (وضع صامت بدون كاميرا)",
        f"Headless={HEADLESS}, Locale={BROWSER_LOCALE}" + (f", Proxy={proxy_server}" if proxy_server else "")
    )

    async with async_playwright() as p:
        logger.info("Launching Chromium browser...")
        try:
            browser = await p.chromium.launch(**launch_kwargs)
        except Exception as e:
            if "playwright install" in str(e) or "Executable doesn't exist" in str(e):
                logger.warning("Chromium executable missing. Auto-installing via 'playwright install chromium'...")
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                browser = await p.chromium.launch(**launch_kwargs)
            else:
                raise e
        global_browser_ref = browser

        # Start Gmail Meet Link Watcher
        asyncio.create_task(gmail_monitor_loop(browser))

        while True:
            # If no MEET_URL is provided, wait for URL from Web Dashboard or Telegram
            if not MEET_URL:
                bot_state["status"] = "⏳ في الانتظار - ضع رابط الاجتماع أعلاه أو أرسله عبر تيليجرام @meethazem_bot"
                bot_state["current_task"] = "في انتظار الرابط"
                bot_state["problem_diagnosis"] = "البوت متصل ومستعد لاستقبال الروابط فوراً"
                bot_state["problem_type"] = "NORMAL"
                logger.info("Waiting for meeting URL from Web Dashboard or Telegram Bot (@meethazem_bot)...")
                await new_meeting_event.wait()
                new_meeting_event.clear()

            target_url = clean_meet_url(MEET_URL)
            target_student = STUDENT_NAME or "hazem"
            logger.info("=========================================")
            logger.info(f"  Starting Meeting: {target_url}")
            logger.info(f"  Student Name: {target_student}")
            logger.info("=========================================")

            await run_single_meeting_flow(browser, target_url, target_student)

            # Session finished; clear URL to wait for next meeting
            if MEET_URL == target_url:
                MEET_URL = ""
                bot_state["meet_url"] = ""

            bot_state["status"] = "انتهت الحصة أو تم الخروج. في انتظار رابط الاجتماع القادم..."
            bot_state["current_task"] = "في الانتظار"
            await asyncio.sleep(3)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
