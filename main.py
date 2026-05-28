import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import database
from browser_manager import browser_manager
from config import BOT_TOKEN
from handlers import router

# ─────────────────────────── Logging sozlamasi ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# Uchinchi tomon kutubxonalarining log darajasini kamaytirish
logging.getLogger("aiogram").setLevel(logging.WARNING)
logging.getLogger("playwright").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)


# ──────────────────────────── Bot lifecycle ───────────────────────────────
async def on_startup(bot: Bot) -> None:
    """Bot ishga tushganda bajariladigan amallar."""
    logger.info("Bot ishga tushmoqda...")

    # Ma'lumotlar bazasini ishga tushirish
    database.initialize_database()
    logger.info("Ma'lumotlar bazasi tayyor.")

    # Playwright ni ishga tushirish
    await browser_manager.start_playwright()
    logger.info("Browser manager tayyor.")

    # Bot ma'lumotlarini olish
    try:
        bot_info = await bot.get_me()
        logger.info(f"Bot muvaffaqiyatli ishga tushdi: @{bot_info.username} (ID: {bot_info.id})")
    except Exception as e:
        logger.error(f"Bot ma'lumotlarini olishda xato: {e}")


async def on_shutdown(bot: Bot) -> None:
    """Bot to'xtatilganda bajariladigan tozalash amallari."""
    logger.info("Bot to'xtatilmoqda...")

    # Barcha brauzer sessiyalarini yopish
    await browser_manager.stop_playwright()
    logger.info("Barcha brauzer sessiyalari yopildi.")

    # Bot sessiyasini yopish
    await bot.session.close()
    logger.info("Bot sessiyasi yopildi.")


# ──────────────────────────── Asosiy funksiya ─────────────────────────────
async def main() -> None:
    """Botni ishga tushirishning asosiy kirish nuqtasi."""

    # Token tekshirish
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or not BOT_TOKEN:
        logger.critical(
            "BOT_TOKEN o'rnatilmagan! "
            "Iltimos, config.py faylida yoki BOT_TOKEN muhit o'zgaruvchisida tokenni kiriting."
        )
        sys.exit(1)

    # Bot va Dispatcher yaratish
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # FSM uchun xotira saqlash (ishlab chiqarish uchun Redis tavsiya etiladi)
    storage = MemoryStorage()

    dp = Dispatcher(storage=storage)

    # Startup va shutdown hook-larini ro'yxatdan o'tkazish
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Handler-larni qo'shish
    dp.include_router(router)

    # Polling rejimida ishga tushirish
    logger.info("Polling boshlandi...")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True,  # Eski xabarlarni o'tkazib yuborish
        )
    except Exception as e:
        logger.critical(f"Polling xatosi: {e}")
        raise
    finally:
        logger.info("Bot to'xtatildi.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot Ctrl+C bilan to'xtatildi.")
    except Exception as e:
        logger.critical(f"Kutilmagan xato: {e}", exc_info=True)
        sys.exit(1)
