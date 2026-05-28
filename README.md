# 🤖 Playwright Automation Telegram Bot

Veb-ilovalarni avtomatlashtirilgan testdan o'tkazish uchun ko'p foydalanuvchili Telegram bot.

## 📁 Loyiha tuzilmasi

```
bot/
├── main.py            # Asosiy kirish nuqtasi
├── config.py          # Sozlamalar
├── database.py        # SQLite ma'lumotlar bazasi
├── handlers.py        # Telegram bot handlerlari (aiogram)
├── browser_manager.py # Playwright brauzer sessiyalari
├── captcha_solvers.py # Kapcha hal qiluvchilar
└── requirements.txt   # Python paketlari
```

## ⚙️ O'rnatish

```bash
# 1. Virtual muhit yaratish
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# 2. Paketlarni o'rnatish
pip install -r requirements.txt

# 3. Playwright brauzerini o'rnatish
playwright install chromium
playwright install-deps chromium   # Linux uchun tizim bog'liqliklarini o'rnatish

# 4. Token sozlash (.env fayl yoki muhit o'zgaruvchisi)
export BOT_TOKEN="your_telegram_bot_token"
export TARGET_URL="https://your-target-website.com"

# 5. Botni ishga tushirish
python main.py
```

## 🔧 Sozlash (config.py)

| O'zgaruvchi | Standart | Tavsif |
|---|---|---|
| `BOT_TOKEN` | — | @BotFather dan olingan token |
| `TARGET_URL` | `https://example.com` | Avtomatlashtirilishi kerak bo'lgan sayt |
| `BROWSER_HEADLESS` | `true` | Headless rejim (false = ko'rinadigan brauzer) |
| `BROWSER_TIMEOUT` | `30000` | Brauzer kutish vaqti (ms) |
| `BROWSER_PROFILES_DIR` | `./browser_profiles` | Brauzer profillari katalogi |

## 🗄️ Ma'lumotlar bazasi sxemasi

```sql
CREATE TABLE users (
    user_id      INTEGER PRIMARY KEY,
    cookies      TEXT    DEFAULT NULL,        -- JSON formatdagi cookie-lar
    captcha_type TEXT    DEFAULT 'recaptcha', -- icon | recaptcha | cloudflare
    status       TEXT    DEFAULT 'inactive',  -- active | inactive
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## 📖 Foydalanish

1. `/start` — Botni ishga tushirish
2. **Cookie kiritish** — Playwright formatidagi JSON cookie-larni kiritish
3. **Kapcha turi** — Kapcha turini tanlash (IconCaptcha / reCAPTCHA / Cloudflare)
4. **Start** — Avtomatlashtirish sessiyasini boshlash
5. **Stop** — Sessiyani to'xtatish

## 🍪 Cookie formati

```json
[
  {
    "name": "session_id",
    "value": "your_session_value",
    "domain": "example.com",
    "path": "/",
    "httpOnly": true,
    "secure": true
  }
]
```

## 🔐 Kapcha turlari

| Tur | Tavsif |
|---|---|
| `icon` | IconCaptcha — rasmlardan to'g'ri belgini topish |
| `recaptcha` | Google reCAPTCHA v2/v3 |
| `cloudflare` | Cloudflare Turnstile |

## ⚠️ Muhim eslatmalar

- Har bir foydalanuvchi uchun alohida brauzer profil katalogi yaratiladi
- `browser_profiles/` katalogi avtomatik yaratiladi
- Ishlab chiqarish muhitida FSM storage uchun Redis tavsiya etiladi
- Kapcha solver funksiyalari shablon sifatida berilgan — haqiqiy implementatsiya uchun moslashtiring
