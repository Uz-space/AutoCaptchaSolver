import json
import logging
from typing import Any

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

import database
from browser_manager import browser_manager
from config import CAPTCHA_TYPES, STATUS_ACTIVE

logger = logging.getLogger(__name__)
router = Router()


# ─────────────────────────── FSM holatlari ────────────────────────────────
class UserState(StatesGroup):
    waiting_for_cookies = State()


# ──────────────────────────── Klaviatura ──────────────────────────────────
def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Asosiy inline klaviaturani yaratish. Holat bo'yicha Start/Stop ko'rsatish."""
    is_active = browser_manager.is_session_active(user_id)

    buttons = [
        [
            InlineKeyboardButton(
                text="🍪 Cookie kiritish", callback_data="action:set_cookie"
            ),
            InlineKeyboardButton(
                text="🔐 Kapcha turi", callback_data="action:captcha_type"
            ),
        ],
        [
            InlineKeyboardButton(
                text="⏹ Stop" if is_active else "▶️ Start",
                callback_data="action:stop" if is_active else "action:start",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_captcha_keyboard() -> InlineKeyboardMarkup:
    """Kapcha turi tanlash uchun inline klaviatura."""
    buttons = [
        [InlineKeyboardButton(text="🖼 IconCaptcha", callback_data="captcha:icon")],
        [InlineKeyboardButton(text="🤖 reCAPTCHA", callback_data="captcha:recaptcha")],
        [InlineKeyboardButton(
            text="☁️ Cloudflare Turnstile", callback_data="captcha:cloudflare"
        )],
        [InlineKeyboardButton(text="◀️ Ortga", callback_data="action:back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ────────────────────────── Yordamchi funksiyalar ─────────────────────────
def validate_cookies(raw_text: str) -> tuple[bool, Any]:
    """
    Foydalanuvchi yuborgan matnni JSON cookie sifatida validatsiya qilish.

    Returns:
        (True, cookies_list)  - validatsiya o'tdi
        (False, error_message) - validatsiya o'tmadi
    """
    try:
        data = json.loads(raw_text.strip())
    except json.JSONDecodeError as e:
        return False, f"JSON formati noto'g'ri: {e}"

    # Cookie ro'yxat bo'lishi kerak
    if not isinstance(data, list):
        return False, "Cookie-lar JSON massiv (list) bo'lishi kerak. Misol: [{...}, {...}]"

    # Har bir element dict bo'lishi kerak
    for i, cookie in enumerate(data):
        if not isinstance(cookie, dict):
            return False, f"Cookie #{i+1} dict (lug'at) bo'lishi kerak."

        # Majburiy maydonlarni tekshirish
        if "name" not in cookie:
            return False, f"Cookie #{i+1} da 'name' maydoni yo'q."
        if "value" not in cookie:
            return False, f"Cookie #{i+1} da 'value' maydoni yo'q."

        # Domain yoki URL majburiy
        if "domain" not in cookie and "url" not in cookie:
            return False, (
                f"Cookie #{i+1} da 'domain' yoki 'url' maydoni bo'lishi kerak.\n"
                f"Misol: {{\"name\": \"session\", \"value\": \"abc\", \"domain\": \"example.com\"}}"
            )

    return True, data


async def send_main_menu(target: Message | CallbackQuery, user_id: int) -> None:
    """Asosiy menyuni yuborish yoki yangilash."""
    user_data = database.get_user(user_id)

    has_cookies = bool(user_data and user_data.get("cookies"))
    captcha_type = user_data.get("captcha_type", "recaptcha") if user_data else "recaptcha"
    captcha_name = CAPTCHA_TYPES.get(captcha_type, captcha_type)
    is_active = browser_manager.is_session_active(user_id)

    status_emoji = "🟢 Aktiv" if is_active else "🔴 To'xtatilgan"
    cookie_status = "✅ Kiritilgan" if has_cookies else "❌ Kiritilmagan"

    text = (
        f"👤 <b>Boshqaruv paneli</b>\n\n"
        f"📌 <b>Holat:</b> {status_emoji}\n"
        f"🍪 <b>Cookie:</b> {cookie_status}\n"
        f"🔐 <b>Kapcha turi:</b> {captcha_name}\n\n"
        f"Quyidagi tugmalardan foydalaning:"
    )
    keyboard = get_main_keyboard(user_id)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ─────────────────────────────── Handlerlar ───────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """/start buyrug'i."""
    await state.clear()
    user_id = message.from_user.id

    try:
        database.upsert_user(user_id)
        logger.info(f"Yangi foydalanuvchi: {user_id} ({message.from_user.full_name})")
        await send_main_menu(message, user_id)
    except Exception as e:
        logger.error(f"Foydalanuvchi {user_id} /start xatosi: {e}")
        await message.answer("❌ Xato yuz berdi. Iltimos, qayta urinib ko'ring.")


@router.callback_query(F.data == "action:back")
async def cb_back(callback: CallbackQuery, state: FSMContext) -> None:
    """Ortga tugmasi - asosiy menyuga qaytish."""
    await state.clear()
    await callback.answer()
    await send_main_menu(callback, callback.from_user.id)


@router.callback_query(F.data == "action:set_cookie")
async def cb_set_cookie(callback: CallbackQuery, state: FSMContext) -> None:
    """Cookie kiritish tugmasi."""
    await callback.answer()
    await state.set_state(UserState.waiting_for_cookies)

    example = json.dumps(
        [{"name": "session_id", "value": "abc123xyz", "domain": "example.com"}],
        ensure_ascii=False,
        indent=2,
    )

    await callback.message.edit_text(
        "🍪 <b>Cookie kiritish</b>\n\n"
        "Playwright formatidagi JSON cookie-larni yuboring.\n\n"
        f"<b>Namuna:</b>\n<code>{example}</code>\n\n"
        "❕ Bir nechta cookie uchun massiv ichiga kiriting.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Ortga", callback_data="action:back")
            ]]
        ),
        parse_mode="HTML",
    )


@router.message(UserState.waiting_for_cookies)
async def process_cookies(message: Message, state: FSMContext) -> None:
    """Foydalanuvchi yuborgan cookie matnini qayta ishlash."""
    user_id = message.from_user.id

    if not message.text:
        await message.answer("❌ Iltimos, JSON matn yuboring.")
        return

    # Validatsiya
    is_valid, result = validate_cookies(message.text)

    if not is_valid:
        await message.answer(
            f"❌ <b>Validatsiya xatosi:</b>\n{result}\n\n"
            "Iltimos, to'g'ri JSON formatida qayta yuboring.",
            parse_mode="HTML",
        )
        return

    # Bazaga saqlash
    try:
        database.save_cookies(user_id, result)
        await state.clear()
        await message.answer(
            f"✅ <b>{len(result)} ta cookie muvaffaqiyatli saqlandi!</b>",
            parse_mode="HTML",
        )
        await send_main_menu(message, user_id)
    except Exception as e:
        logger.error(f"Cookie saqlashda xato (user_id={user_id}): {e}")
        await message.answer("❌ Cookie saqlashda xato yuz berdi.")


@router.callback_query(F.data == "action:captcha_type")
async def cb_captcha_type(callback: CallbackQuery) -> None:
    """Kapcha turi tanlash menyusini ko'rsatish."""
    await callback.answer()
    await callback.message.edit_text(
        "🔐 <b>Kapcha turini tanlang:</b>\n\n"
        "• <b>IconCaptcha</b> — Rasmlardan to'g'ri belgini topish\n"
        "• <b>reCAPTCHA</b> — Google reCAPTCHA v2/v3\n"
        "• <b>Cloudflare Turnstile</b> — Cloudflare himoya tizimi",
        reply_markup=get_captcha_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("captcha:"))
async def cb_captcha_select(callback: CallbackQuery) -> None:
    """Kapcha turini saqlash."""
    captcha_type = callback.data.split(":")[1]
    user_id = callback.from_user.id

    try:
        database.update_captcha_type(user_id, captcha_type)
        captcha_name = CAPTCHA_TYPES.get(captcha_type, captcha_type)
        await callback.answer(f"✅ {captcha_name} tanlandi!", show_alert=False)
        await send_main_menu(callback, user_id)
    except ValueError as e:
        await callback.answer(f"❌ {e}", show_alert=True)
    except Exception as e:
        logger.error(f"Kapcha turi yangilashda xato (user_id={user_id}): {e}")
        await callback.answer("❌ Xato yuz berdi.", show_alert=True)


@router.callback_query(F.data == "action:start")
async def cb_start_session(callback: CallbackQuery) -> None:
    """Avtomatlashtirish sessiyasini boshlash."""
    user_id = callback.from_user.id

    # Cookie mavjudligini tekshirish
    user_data = database.get_user(user_id)
    if not user_data or not user_data.get("cookies"):
        await callback.answer(
            "❌ Avval cookie kiriting!", show_alert=True
        )
        return

    # Sessiyani boshlash
    try:
        started = await browser_manager.start_session(user_id)
        if started:
            await callback.answer("▶️ Sessiya boshlandi!", show_alert=False)
            logger.info(f"Foydalanuvchi {user_id} sessiyani boshladi.")
        else:
            await callback.answer("⚠️ Sessiya allaqachon ishlayapti.", show_alert=True)

        await send_main_menu(callback, user_id)
    except Exception as e:
        logger.error(f"Sessiya boshlashda xato (user_id={user_id}): {e}")
        await callback.answer("❌ Sessiya boshlashda xato.", show_alert=True)


@router.callback_query(F.data == "action:stop")
async def cb_stop_session(callback: CallbackQuery) -> None:
    """Avtomatlashtirish sessiyasini to'xtatish."""
    user_id = callback.from_user.id

    try:
        stopped = await browser_manager.stop_session(user_id)
        if stopped:
            await callback.answer("⏹ Sessiya to'xtatildi.", show_alert=False)
            logger.info(f"Foydalanuvchi {user_id} sessiyani to'xtatdi.")
        else:
            await callback.answer("⚠️ Aktiv sessiya topilmadi.", show_alert=True)

        await send_main_menu(callback, user_id)
    except Exception as e:
        logger.error(f"Sessiyani to'xtatishda xato (user_id={user_id}): {e}")
        await callback.answer("❌ Sessiyani to'xtatishda xato.", show_alert=True)


@router.callback_query()
async def cb_unknown(callback: CallbackQuery) -> None:
    """Noma'lum callback-larni qayta ishlash."""
    logger.warning(f"Noma'lum callback: {callback.data} (user_id={callback.from_user.id})")
    await callback.answer("⚠️ Noma'lum buyruq.", show_alert=False)


@router.message()
async def msg_unknown(message: Message, state: FSMContext) -> None:
    """Kutilmagan xabarlarga javob berish."""
    current_state = await state.get_state()
    if current_state is None:
        await send_main_menu(message, message.from_user.id)
