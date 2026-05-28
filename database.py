import sqlite3
import json
import logging
from typing import Optional, Dict, Any
from contextlib import contextmanager

from config import DATABASE_PATH, STATUS_INACTIVE

logger = logging.getLogger(__name__)


@contextmanager
def get_db_connection():
    """Thread-safe ma'lumotlar bazasi ulanishini boshqaruvchi context manager."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # natijalarni dict kabi ishlatish uchun
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Ma'lumotlar bazasi xatosi: {e}")
        raise
    finally:
        conn.close()


def initialize_database() -> None:
    """Ma'lumotlar bazasini yaratish va jadvallarni tayyorlash."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     INTEGER PRIMARY KEY,
                    cookies     TEXT    DEFAULT NULL,
                    captcha_type TEXT   DEFAULT 'recaptcha'
                                        CHECK(captcha_type IN ('icon', 'recaptcha', 'cloudflare')),
                    status      TEXT    NOT NULL DEFAULT 'inactive'
                                        CHECK(status IN ('active', 'inactive')),
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # updated_at ni avtomatik yangilash uchun trigger
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_users_timestamp
                AFTER UPDATE ON users
                FOR EACH ROW
                BEGIN
                    UPDATE users SET updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = OLD.user_id;
                END
            """)

            logger.info("Ma'lumotlar bazasi muvaffaqiyatli ishga tushirildi.")
    except Exception as e:
        logger.critical(f"Ma'lumotlar bazasini ishga tushirishda xato: {e}")
        raise


def upsert_user(user_id: int) -> None:
    """Foydalanuvchini bazaga qo'shish (agar mavjud bo'lmasa)."""
    try:
        with get_db_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO users (user_id) VALUES (?)
            """, (user_id,))
            logger.debug(f"Foydalanuvchi {user_id} bazaga qo'shildi yoki allaqachon mavjud.")
    except Exception as e:
        logger.error(f"Foydalanuvchi {user_id} ni qo'shishda xato: {e}")
        raise


def save_cookies(user_id: int, cookies: list) -> None:
    """Foydalanuvchining cookie-larini JSON formatda saqlash."""
    try:
        cookies_json = json.dumps(cookies, ensure_ascii=False)
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO users (user_id, cookies)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET cookies = excluded.cookies
            """, (user_id, cookies_json))
            logger.info(f"Foydalanuvchi {user_id} ning cookie-lari saqlandi.")
    except Exception as e:
        logger.error(f"Cookie saqlashda xato (user_id={user_id}): {e}")
        raise


def update_captcha_type(user_id: int, captcha_type: str) -> None:
    """Foydalanuvchining kapcha turini yangilash."""
    valid_types = ("icon", "recaptcha", "cloudflare")
    if captcha_type not in valid_types:
        raise ValueError(f"Noto'g'ri kapcha turi: {captcha_type}. Mumkin bo'lganlar: {valid_types}")
    try:
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO users (user_id, captcha_type)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET captcha_type = excluded.captcha_type
            """, (user_id, captcha_type))
            logger.info(f"Foydalanuvchi {user_id} kapcha turi yangilandi: {captcha_type}")
    except Exception as e:
        logger.error(f"Kapcha turini yangilashda xato (user_id={user_id}): {e}")
        raise


def update_status(user_id: int, status: str) -> None:
    """Foydalanuvchi holatini yangilash (active/inactive)."""
    valid_statuses = ("active", "inactive")
    if status not in valid_statuses:
        raise ValueError(f"Noto'g'ri holat: {status}. Mumkin bo'lganlar: {valid_statuses}")
    try:
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO users (user_id, status)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET status = excluded.status
            """, (user_id, status))
            logger.info(f"Foydalanuvchi {user_id} holati yangilandi: {status}")
    except Exception as e:
        logger.error(f"Holat yangilashda xato (user_id={user_id}): {e}")
        raise


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Foydalanuvchi ma'lumotlarini olish."""
    try:
        with get_db_connection() as conn:
            row = conn.execute("""
                SELECT user_id, cookies, captcha_type, status, created_at, updated_at
                FROM users WHERE user_id = ?
            """, (user_id,)).fetchone()

            if row is None:
                return None

            result = dict(row)
            # Cookie-larni JSON dan ro'yxatga o'girish
            if result["cookies"]:
                try:
                    result["cookies"] = json.loads(result["cookies"])
                except json.JSONDecodeError:
                    logger.warning(f"Foydalanuvchi {user_id} cookie-lari buzilgan, null qilinmoqda.")
                    result["cookies"] = None
            return result
    except Exception as e:
        logger.error(f"Foydalanuvchi ma'lumotlarini olishda xato (user_id={user_id}): {e}")
        raise


def get_user_status(user_id: int) -> str:
    """Foydalanuvchi holatini tezkor olish."""
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT status FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return row["status"] if row else STATUS_INACTIVE
    except Exception as e:
        logger.error(f"Holat olishda xato (user_id={user_id}): {e}")
        return STATUS_INACTIVE
