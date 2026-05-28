import asyncio
import logging
import os
from typing import Dict, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

import database
from captcha_solvers import get_solver
from config import (
    BROWSER_HEADLESS,
    BROWSER_TIMEOUT,
    BROWSER_PROFILES_DIR,
    TARGET_URL,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    TWO_CAPTCHA_API_KEY,
)

logger = logging.getLogger(__name__)


class UserSession:
    """Bitta foydalanuvchi uchun brauzer sessiyasini saqlash."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.context: Optional[BrowserContext] = None
        self.task: Optional[asyncio.Task] = None
        self.is_running: bool = False


class BrowserManager:
    """
    Barcha foydalanuvchilar uchun brauzer sessiyalarini markaziy boshqaruvchi.
    Har bir foydalanuvchiga alohida Chromium persistent context beriladi.
    """

    def __init__(self):
        self._sessions: Dict[int, UserSession] = {}
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()

    async def start_playwright(self) -> None:
        """Playwright va Chromium ni global ishga tushirish."""
        try:
            self._playwright = await async_playwright().start()
            # Bitta Chromium instansi, lekin har bir foydalanuvchiga alohida context
            self._browser = await self._playwright.chromium.launch(
                headless=BROWSER_HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--no-first-run",
                    "--no-zygote",
                    "--disable-gpu",
                ]
            )
            os.makedirs(BROWSER_PROFILES_DIR, exist_ok=True)
            logger.info("Playwright va Chromium muvaffaqiyatli ishga tushirildi.")
        except Exception as e:
            logger.critical(f"Playwright ishga tushirishda xato: {e}")
            raise

    async def stop_playwright(self) -> None:
        """Barcha sessiyalarni va Playwright ni to'xtatish."""
        logger.info("Barcha sessiyalar to'xtatilmoqda...")

        # Barcha aktiv sessiyalarni to'xtatish
        user_ids = list(self._sessions.keys())
        for user_id in user_ids:
            await self.stop_session(user_id)

        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        logger.info("Playwright to'xtatildi.")

    async def _get_or_create_context(self, user_id: int) -> BrowserContext:
        """
        Foydalanuvchi uchun persistent brauzer kontekstini olish yoki yaratish.
        Profil katalogi user_id ga asoslangan bo'lib, cookie-lar saqlanadi.
        """
        profile_dir = os.path.join(BROWSER_PROFILES_DIR, str(user_id))
        os.makedirs(profile_dir, exist_ok=True)

        # Persistent context - brauzer profil katalogida cookie va local storage saqlanadi
        context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=BROWSER_HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            timeout=BROWSER_TIMEOUT,
        )
        return context

    async def _load_cookies_to_context(
        self, context: BrowserContext, cookies: list
    ) -> None:
        """Cookie ro'yxatini brauzer kontekstiga yuklash."""
        if not cookies:
            logger.warning("Cookie ro'yxati bo'sh, yuklanmadi.")
            return
        try:
            await context.add_cookies(cookies)
            logger.info(f"{len(cookies)} ta cookie muvaffaqiyatli yuklandi.")
        except Exception as e:
            logger.error(f"Cookie yuklashda xato: {e}")
            raise

    async def _run_automation_task(self, user_id: int) -> None:
        """
        Foydalanuvchi uchun asosiy avtomatlashtirish vazifasi.
        Bu funksiya fon rejimida ishlaydi.
        """
        session = self._sessions.get(user_id)
        if not session:
            logger.error(f"Sessiya topilmadi: user_id={user_id}")
            return

        try:
            # Bazadan foydalanuvchi ma'lumotlarini olish
            user_data = database.get_user(user_id)
            if not user_data:
                logger.error(f"Foydalanuvchi ma'lumotlari topilmadi: {user_id}")
                database.update_status(user_id, STATUS_INACTIVE)
                return

            cookies = user_data.get("cookies")
            captcha_type = user_data.get("captcha_type", "recaptcha")

            if not cookies:
                logger.warning(f"Foydalanuvchi {user_id} uchun cookie topilmadi.")
                database.update_status(user_id, STATUS_INACTIVE)
                return

            # Brauzer kontekstini yaratish
            logger.info(f"Foydalanuvchi {user_id} uchun brauzer konteksti yaratilmoqda...")
            context = await self._get_or_create_context(user_id)
            session.context = context

            # Cookie-larni yuklash
            await self._load_cookies_to_context(context, cookies)

            # Yangi sahifa ochish
            page = await context.new_page()

            # Asosiy avtomatlashtirish sikli
            logger.info(f"Foydalanuvchi {user_id} avtomatlashtirish boshlandi. URL: {TARGET_URL}")

            while session.is_running:
                try:
                    # Maqsadli sahifaga o'tish
                    response = await page.goto(TARGET_URL, timeout=BROWSER_TIMEOUT)

                    if response and response.status >= 400:
                        logger.warning(
                            f"Foydalanuvchi {user_id}: HTTP {response.status} xatosi."
                        )
                        await asyncio.sleep(10)
                        continue

                    # Kapcha mavjudligini tekshirish va hal qilish
                    solver = get_solver(captcha_type, page, api_key=TWO_CAPTCHA_API_KEY)
                    if solver:
                        captcha_solved = await solver.solve()
                        if not captcha_solved:
                            logger.warning(
                                f"Foydalanuvchi {user_id}: Kapcha hal qilinmadi, qayta urinilmoqda..."
                            )
                            await asyncio.sleep(5)
                            continue

                    # ============================================================
                    # BU YERDA ASOSIY BIZNES LOGIKASI BO'LADI
                    # Masalan: forma to'ldirish, ma'lumot yig'ish, va hokazo
                    logger.info(f"Foydalanuvchi {user_id}: Sahifa muvaffaqiyatli yuklandi, vazifa bajarilmoqda...")
                    # ============================================================

                    # Sikl davomiyligi (kerakli vaqtga moslashtirish)
                    await asyncio.sleep(30)

                except asyncio.CancelledError:
                    logger.info(f"Foydalanuvchi {user_id} vazifasi bekor qilindi.")
                    break
                except Exception as e:
                    logger.error(f"Foydalanuvchi {user_id} sikl xatosi: {e}")
                    await asyncio.sleep(10)  # Xato bo'lganda 10 sekund kutish

        except asyncio.CancelledError:
            logger.info(f"Foydalanuvchi {user_id} vazifasi to'xtatildi.")
        except Exception as e:
            logger.error(f"Foydalanuvchi {user_id} avtomatlashtirish xatosi: {e}")
        finally:
            # Tozalash
            session.is_running = False
            if session.context:
                try:
                    await session.context.close()
                except Exception as e:
                    logger.warning(f"Kontekst yopishda xato: {e}")
                session.context = None

            # Bazada holatni yangilash
            try:
                database.update_status(user_id, STATUS_INACTIVE)
            except Exception as e:
                logger.error(f"Holat yangilashda xato: {e}")

            logger.info(f"Foydalanuvchi {user_id} sessiyasi to'liq yopildi.")

    async def start_session(self, user_id: int) -> bool:
        """
        Foydalanuvchi uchun yangi avtomatlashtirish sessiyasini boshlash.

        Returns:
            True - muvaffaqiyatli boshlandi
            False - allaqachon ishlayapti yoki xato
        """
        async with self._lock:
            # Avvalgi sessiya ishlayotganligini tekshirish
            existing_session = self._sessions.get(user_id)
            if existing_session and existing_session.is_running:
                logger.info(f"Foydalanuvchi {user_id} sessiyasi allaqachon ishlayapti.")
                return False

            # Yangi sessiya yaratish
            session = UserSession(user_id)
            session.is_running = True
            self._sessions[user_id] = session

        # Bazada holatni yangilash
        try:
            database.update_status(user_id, STATUS_ACTIVE)
        except Exception as e:
            logger.error(f"Holat yangilashda xato: {e}")

        # Fon vazifasini ishga tushirish
        task = asyncio.create_task(
            self._run_automation_task(user_id),
            name=f"automation_user_{user_id}"
        )
        session.task = task

        logger.info(f"Foydalanuvchi {user_id} sessiyasi muvaffaqiyatli boshlandi.")
        return True

    async def stop_session(self, user_id: int) -> bool:
        """
        Foydalanuvchi sessiyasini to'xtatish.

        Returns:
            True - to'xtatildi yoki allaqachon to'xtatilgan
            False - sessiya topilmadi
        """
        session = self._sessions.get(user_id)
        if not session:
            logger.info(f"Foydalanuvchi {user_id} uchun aktiv sessiya yo'q.")
            return False

        logger.info(f"Foydalanuvchi {user_id} sessiyasi to'xtatilmoqda...")
        session.is_running = False

        # Asyncio vazifasini bekor qilish
        if session.task and not session.task.done():
            session.task.cancel()
            try:
                await asyncio.wait_for(session.task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass  # Kutilgan xato

        # Sessiyani lug'atdan o'chirish
        self._sessions.pop(user_id, None)

        # Bazada holatni yangilash
        try:
            database.update_status(user_id, STATUS_INACTIVE)
        except Exception as e:
            logger.error(f"Holat yangilashda xato: {e}")

        logger.info(f"Foydalanuvchi {user_id} sessiyasi to'xtatildi.")
        return True

    def is_session_active(self, user_id: int) -> bool:
        """Foydalanuvchi sessiyasi ishlayotganligini tekshirish."""
        session = self._sessions.get(user_id)
        return session is not None and session.is_running

    def get_active_count(self) -> int:
        """Jami aktiv sessiyalar sonini olish."""
        return sum(1 for s in self._sessions.values() if s.is_running)


# Global BrowserManager instansi
browser_manager = BrowserManager()
