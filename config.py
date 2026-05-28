import os

# Telegram Bot Token - .env yoki muhit o'zgaruvchisidan olish
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# 2Captcha API kaliti
TWO_CAPTCHA_API_KEY = os.getenv("TWO_CAPTCHA_API_KEY", "YOUR_2CAPTCHA_API_KEY")

# Ma'lumotlar bazasi fayli joylashuvi
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot_database.db")

# Playwright brauzer sozlamalari
BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
BROWSER_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "30000"))  # milliseconds

# Maqsadli sayt URL (o'zgartirish mumkin)
TARGET_URL = os.getenv("TARGET_URL", "https://example.com")

# Persistent context uchun brauzer profil katalogi
BROWSER_PROFILES_DIR = os.getenv("BROWSER_PROFILES_DIR", "./browser_profiles")

# Kapcha turlari
CAPTCHA_TYPES = {
    "icon": "IconCaptcha",
    "recaptcha": "reCAPTCHA",
    "cloudflare": "Cloudflare Turnstile"
}

# Foydalanuvchi holatlari
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
