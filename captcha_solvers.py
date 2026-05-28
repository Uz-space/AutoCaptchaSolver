"""
captcha_solvers.py

2Captcha API orqali uchta kapcha turini haqiqatda yechadigan to'liq modul:
  - CloudflareTurnstileSolver  (turnstile)
  - ReCaptchaSolver            (recaptcha v2)
  - IconCaptchaSolver          (koordinata/click kapcha)

Har bir solver:
  1. Sahifadan kerakli parametrlarni (sitekey, rasm) ajratib oladi
  2. 2Captcha ga yuboradi va CAPTCHA_NOT_READY siklida javobni kutadi
  3. Qaytgan tokenni / koordinatani sahifaga qo'llab formani yuboradi
"""

import asyncio
import base64
import logging
import time
from typing import Optional

import requests
from playwright.async_api import Page

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# 2Captcha API sozlamalari
# ──────────────────────────────────────────────────────────────────────────
TWO_CAPTCHA_API_KEY: str = "YOUR_2CAPTCHA_API_KEY"   # <-- o'zgartiring

# API endpointlari
IN_URL  = "https://2captcha.com/in.php"
RES_URL = "https://2captcha.com/res.php"

# Polling parametrlari
POLL_INTERVAL_SEC  = 5    # har necha sekundda so'rov yuboriladi
POLL_MAX_ATTEMPTS  = 24   # maksimal urinish soni  (24 × 5 sek = 2 daqiqa)

# HTTP so'rov timeout (soniya)
HTTP_TIMEOUT = 30


# ──────────────────────────────────────────────────────────────────────────
# Asosiy klass
# ──────────────────────────────────────────────────────────────────────────
class CaptchaSolverBase:
    """Barcha kapcha solverlar uchun umumiy asos."""

    def __init__(self, page: Page, api_key: str = TWO_CAPTCHA_API_KEY):
        self.page    = page
        self.api_key = api_key

    async def solve(self) -> bool:
        raise NotImplementedError("solve() subklassda amalga oshirilishi kerak.")

    # ── yordamchi: element kutish ──────────────────────────────────────────
    async def _wait_for(self, selector: str, timeout_ms: int = 10_000) -> bool:
        try:
            await self.page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            return False

    # ── yordamchi: 2Captcha ga vazifa yuborish ─────────────────────────────
    def _submit_task(self, payload: dict) -> Optional[str]:
        """
        2Captcha /in.php ga POST yuboradi va task ID ni qaytaradi.
        Xato bo'lsa None qaytaradi.
        """
        try:
            resp = requests.post(IN_URL, data=payload, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            text = resp.text.strip()

            if text.startswith("OK|"):
                task_id = text.split("|", 1)[1]
                logger.info(f"2Captcha vazifa qabul qilindi, task_id={task_id}")
                return task_id

            # Xato kodlari
            logger.error(f"2Captcha /in.php xatosi: {text}")
            return None

        except requests.RequestException as exc:
            logger.error(f"2Captcha /in.php tarmoq xatosi: {exc}")
            return None

    # ── yordamchi: 2Captcha dan natijani polling ───────────────────────────
    def _poll_result(self, task_id: str) -> Optional[str]:
        """
        CAPTCHA_NOT_READY bo'lsa POLL_MAX_ATTEMPTS marta kutadi.
        Muvaffaqiyatli bo'lsa token/natijani qaytaradi, aks holda None.
        """
        params = {
            "key":    self.api_key,
            "action": "get",
            "id":     task_id,
        }

        for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
            time.sleep(POLL_INTERVAL_SEC)
            try:
                resp = requests.get(RES_URL, params=params, timeout=HTTP_TIMEOUT)
                resp.raise_for_status()
                text = resp.text.strip()

                if text == "CAPCHA_NOT_READY":
                    logger.debug(f"[{attempt}/{POLL_MAX_ATTEMPTS}] Kapcha hali tayyor emas...")
                    continue

                if text.startswith("OK|"):
                    result = text.split("|", 1)[1]
                    logger.info(f"2Captcha natija olindi (attempt={attempt}): {result[:60]}...")
                    return result

                # Boshqa xato javoblari
                logger.error(f"2Captcha /res.php xatosi: {text}")
                return None

            except requests.RequestException as exc:
                logger.warning(f"2Captcha polling tarmoq xatosi (attempt={attempt}): {exc}")

        logger.error(f"2Captcha {POLL_MAX_ATTEMPTS} urinishdan keyin ham javob bermadi.")
        return None

    # ── yordamchi: polling ni asyncio event loop dan chaqirish ───────────
    async def _await_result(self, task_id: str) -> Optional[str]:
        """Blokirovchi _poll_result ni alohida threadda ishlatish."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._poll_result, task_id)


# ──────────────────────────────────────────────────────────────────────────
# 1. Cloudflare Turnstile Solver
# ──────────────────────────────────────────────────────────────────────────
class CloudflareTurnstileSolver(CaptchaSolverBase):
    """
    Cloudflare Turnstile kapchani 2Captcha turnstile metodi orqali yechadi.

    Algoritm:
      1. Sahifadan sitekey topiladi (.cf-turnstile yoki [data-sitekey])
      2. 2Captcha ga sitekey + pageurl yuboriladi
      3. Qaytgan token cf-turnstile-response inputiga joylashtiriladi
      4. Forma yuboriladi
    """

    # Sitekey topish uchun CSS selectorlar (prioritet bo'yicha)
    SITEKEY_SELECTORS = [
        ".cf-turnstile[data-sitekey]",
        "[data-sitekey]",
        "div.cf-turnstile",
    ]

    async def _extract_sitekey(self) -> Optional[str]:
        """Sahifadan Cloudflare sitekey ni ajratib olish."""
        for selector in self.SITEKEY_SELECTORS:
            try:
                element = await self.page.query_selector(selector)
                if element:
                    sitekey = await element.get_attribute("data-sitekey")
                    if sitekey:
                        logger.info(f"Cloudflare sitekey topildi: {sitekey[:20]}...")
                        return sitekey
            except Exception as exc:
                logger.debug(f"Selector '{selector}' tekshirishda xato: {exc}")

        # JavaScript orqali ham qidirish
        try:
            sitekey = await self.page.evaluate("""
                () => {
                    // window obyektidan turnstile sitekey ni qidirish
                    const el = document.querySelector('[data-sitekey]');
                    if (el) return el.getAttribute('data-sitekey');

                    // Cloudflare skript parametrlaridan qidirish
                    const scripts = document.querySelectorAll('script[src*="turnstile"]');
                    for (const s of scripts) {
                        const m = s.src.match(/sitekey=([^&]+)/);
                        if (m) return m[1];
                    }
                    return null;
                }
            """)
            if sitekey:
                logger.info(f"Cloudflare sitekey JS orqali topildi: {sitekey[:20]}...")
                return sitekey
        except Exception as exc:
            logger.debug(f"JS sitekey qidirishda xato: {exc}")

        return None

    async def _inject_token(self, token: str) -> bool:
        """Tokenni sahifadagi yashirin inputga joylash."""
        try:
            result = await self.page.evaluate(f"""
                (token) => {{
                    // cf-turnstile-response nomli yashirin inputni topish/yaratish
                    let input = document.querySelector('[name="cf-turnstile-response"]');
                    if (!input) {{
                        input = document.createElement('textarea');
                        input.name = 'cf-turnstile-response';
                        input.style.display = 'none';
                        document.body.appendChild(input);
                    }}
                    input.value = token;

                    // Agar turnstile callback mavjud bo'lsa, uni chaqirish
                    if (typeof window.turnstileCallback === 'function') {{
                        window.turnstileCallback(token);
                    }}

                    // Cloudflare widget callback ni ham tekshirish
                    if (window.turnstile && typeof window.turnstile.execute === 'function') {{
                        // widget ID ni olish
                        const widget = document.querySelector('.cf-turnstile');
                        if (widget) {{
                            const widgetId = widget.getAttribute('data-widget-id');
                            if (widgetId) {{
                                window.turnstile.reset(widgetId);
                            }}
                        }}
                    }}
                    return true;
                }}
            """, token)
            logger.info("Cloudflare token sahifaga joylashtirildi.")
            return bool(result)
        except Exception as exc:
            logger.error(f"Token joylashtirishda xato: {exc}")
            return False

    async def solve(self) -> bool:
        logger.info("Cloudflare Turnstile hal qilinmoqda...")

        # 1. Kapcha mavjudligini tekshirish
        captcha_present = await self._wait_for(".cf-turnstile, [data-sitekey]", 8_000)
        if not captcha_present:
            logger.info("Cloudflare Turnstile topilmadi — o'tkazib yuborilmoqda.")
            return True

        # 2. Sitekey olish
        sitekey = await self._extract_sitekey()
        if not sitekey:
            logger.error("Cloudflare sitekey topilmadi.")
            return False

        page_url = self.page.url

        # 3. 2Captcha ga yuborish (turnstile metodi)
        payload = {
            "key":      self.api_key,
            "method":   "turnstile",
            "sitekey":  sitekey,
            "pageurl":  page_url,
            "json":     0,
        }

        loop = asyncio.get_event_loop()
        task_id = await loop.run_in_executor(None, self._submit_task, payload)
        if not task_id:
            logger.error("2Captcha ga Cloudflare vazifasi yuborilmadi.")
            return False

        # 4. Natijani kutish
        token = await self._await_result(task_id)
        if not token:
            logger.error("2Captcha dan Cloudflare tokeni olinmadi.")
            return False

        # 5. Tokenni sahifaga joylashtirish
        injected = await self._inject_token(token)
        if not injected:
            return False

        # 6. Formani yuborish
        try:
            # Birinchi usul: submit tugmasini bosish
            submit_btn = await self.page.query_selector(
                "button[type='submit'], input[type='submit'], .submit-btn"
            )
            if submit_btn:
                await submit_btn.click()
                logger.info("Cloudflare: forma submit tugmasi bosildi.")
            else:
                # Ikkinchi usul: JS orqali formani yuborish
                await self.page.evaluate("""
                    () => {
                        const form = document.querySelector('form');
                        if (form) form.submit();
                    }
                """)
                logger.info("Cloudflare: forma JS orqali yuborildi.")

            await asyncio.sleep(2)
            logger.info("Cloudflare Turnstile muvaffaqiyatli yechildi ✅")
            return True

        except Exception as exc:
            logger.error(f"Forma yuborishda xato: {exc}")
            return False


# ──────────────────────────────────────────────────────────────────────────
# 2. Google reCAPTCHA v2 Solver
# ──────────────────────────────────────────────────────────────────────────
class ReCaptchaSolver(CaptchaSolverBase):
    """
    Google reCAPTCHA v2 ni 2Captcha userrecaptcha metodi orqali yechadi.

    Algoritm:
      1. Sahifadan data-sitekey topiladi
      2. 2Captcha ga sitekey + pageurl yuboriladi
      3. Qaytgan g-recaptcha-response textarea ga joylashtiriladi
      4. Forma yuboriladi
    """

    SITEKEY_SELECTORS = [
        ".g-recaptcha[data-sitekey]",
        "[data-sitekey]",
        "#recaptcha",
        ".recaptcha",
    ]

    async def _extract_sitekey(self) -> Optional[str]:
        """reCAPTCHA sitekey ni sahifadan olish."""
        # CSS selectorlar orqali
        for selector in self.SITEKEY_SELECTORS:
            try:
                element = await self.page.query_selector(selector)
                if element:
                    sitekey = await element.get_attribute("data-sitekey")
                    if sitekey:
                        logger.info(f"reCAPTCHA sitekey topildi: {sitekey[:20]}...")
                        return sitekey
            except Exception as exc:
                logger.debug(f"reCAPTCHA selector xatosi '{selector}': {exc}")

        # iframe src dan qidirish
        try:
            sitekey = await self.page.evaluate("""
                () => {
                    // Standart .g-recaptcha div
                    const div = document.querySelector('.g-recaptcha[data-sitekey]');
                    if (div) return div.getAttribute('data-sitekey');

                    // reCAPTCHA iframe src dan
                    const iframes = document.querySelectorAll(
                        'iframe[src*="google.com/recaptcha"], iframe[src*="recaptcha.net"]'
                    );
                    for (const f of iframes) {
                        const m = f.src.match(/[?&]k=([^&]+)/);
                        if (m) return m[1];
                    }

                    // grecaptcha.render argumentlaridan
                    const scripts = document.querySelectorAll('script:not([src])');
                    for (const s of scripts) {
                        const m = s.textContent.match(/['"]sitekey['"][ \t]*:[ \t]*['"]([^'"]+)['"]/);
                        if (m) return m[1];
                    }
                    return null;
                }
            """)
            if sitekey:
                logger.info(f"reCAPTCHA sitekey JS orqali topildi: {sitekey[:20]}...")
                return sitekey
        except Exception as exc:
            logger.debug(f"reCAPTCHA JS sitekey xatosi: {exc}")

        return None

    async def _inject_token(self, token: str) -> bool:
        """g-recaptcha-response textarea ga token joylash va callback chaqirish."""
        try:
            await self.page.evaluate(f"""
                (token) => {{
                    // 1. Barcha g-recaptcha-response textarea larni to'ldirish
                    document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{
                        el.innerHTML = token;
                        el.value     = token;
                    }});

                    // 2. grecaptcha callback ni topib chaqirish
                    if (typeof ___grecaptcha_cfg !== 'undefined') {{
                        Object.entries(___grecaptcha_cfg.clients || {{}}).forEach(([id, client]) => {{
                            try {{
                                const callbacks = client['callback'] || [];
                                if (typeof callbacks === 'function') {{
                                    callbacks(token);
                                }} else if (Array.isArray(callbacks)) {{
                                    callbacks.forEach(cb => typeof cb === 'function' && cb(token));
                                }}
                            }} catch(e) {{}}
                        }});
                    }}

                    // 3. grecaptcha.execute callback (v3 uchun ham)
                    if (window.grecaptcha && window.grecaptcha.getResponse) {{
                        // v2 widget callback
                        const divs = document.querySelectorAll('.g-recaptcha');
                        divs.forEach(div => {{
                            const cb = div.getAttribute('data-callback');
                            if (cb && typeof window[cb] === 'function') {{
                                window[cb](token);
                            }}
                        }});
                    }}
                }}
            """, token)
            logger.info("reCAPTCHA tokeni sahifaga joylashtirildi.")
            return True
        except Exception as exc:
            logger.error(f"reCAPTCHA token joylashtirishda xato: {exc}")
            return False

    async def solve(self) -> bool:
        logger.info("reCAPTCHA v2 hal qilinmoqda...")

        # 1. Kapcha mavjudligini tekshirish
        captcha_present = await self._wait_for(
            ".g-recaptcha, iframe[src*='google.com/recaptcha'], iframe[src*='recaptcha.net']",
            8_000
        )
        if not captcha_present:
            logger.info("reCAPTCHA topilmadi — o'tkazib yuborilmoqda.")
            return True

        # 2. Sitekey olish
        sitekey = await self._extract_sitekey()
        if not sitekey:
            logger.error("reCAPTCHA sitekey topilmadi.")
            return False

        page_url = self.page.url

        # 3. 2Captcha ga yuborish
        payload = {
            "key":       self.api_key,
            "method":    "userrecaptcha",
            "googlekey": sitekey,
            "pageurl":   page_url,
            "json":      0,
        }

        loop = asyncio.get_event_loop()
        task_id = await loop.run_in_executor(None, self._submit_task, payload)
        if not task_id:
            logger.error("2Captcha ga reCAPTCHA vazifasi yuborilmadi.")
            return False

        # 4. Natijani kutish (reCAPTCHA odatda 20-60 sek oladi)
        token = await self._await_result(task_id)
        if not token:
            logger.error("2Captcha dan reCAPTCHA tokeni olinmadi.")
            return False

        # 5. Token joylashtirish
        injected = await self._inject_token(token)
        if not injected:
            return False

        # 6. Forma yuborish
        try:
            # data-callback atributi orqali forma submit bo'lgan bo'lishi mumkin
            # Agar bo'lmasa, qo'lda submit qilamiz
            await asyncio.sleep(1)  # callback ishlashi uchun vaqt berish

            submitted = await self.page.evaluate("""
                () => {
                    // Agar forma hali yuborilmagan bo'lsa, yuborish
                    const form = document.querySelector('form');
                    if (form) {
                        const submitBtn = form.querySelector(
                            'button[type="submit"], input[type="submit"]'
                        );
                        if (submitBtn) {
                            submitBtn.click();
                            return 'clicked';
                        }
                        form.submit();
                        return 'submitted';
                    }
                    return 'no_form';
                }
            """)
            logger.info(f"reCAPTCHA: forma natijasi = {submitted}")

            await asyncio.sleep(2)
            logger.info("reCAPTCHA v2 muvaffaqiyatli yechildi ✅")
            return True

        except Exception as exc:
            logger.error(f"reCAPTCHA forma yuborishda xato: {exc}")
            return False


# ──────────────────────────────────────────────────────────────────────────
# 3. IconCaptcha (koordinata/click) Solver
# ──────────────────────────────────────────────────────────────────────────
class IconCaptchaSolver(CaptchaSolverBase):
    """
    Koordinata asosidagi (click) kapchani 2Captcha ClickCaptcha metodi orqali yechadi.

    Algoritm:
      1. Kapcha rasm konteyneri topiladi va screenshot olinadi
      2. Rasm base64 ga aylantiriladi va 2Captcha coordinatescaptcha ga yuboriladi
      3. Qaytgan X,Y koordinatalarga page.mouse.click() qilinadi
      4. Tasdiqlash tugmasi bosiladi
    """

    # Kapcha rasm konteyneri selectorlari (prioritet bo'yicha)
    CONTAINER_SELECTORS = [
        ".captcha-image-holder",
        ".iconcaptcha-modal",
        ".iconcaptcha-widget",
        "#iconcaptcha-modal",
        "[class*='iconcaptcha']",
        "[class*='captcha-image']",
        "[id*='captcha']",
    ]

    # Tasdiqlash tugmasi selectorlari
    SUBMIT_SELECTORS = [
        ".iconcaptcha-button-submit",
        ".captcha-submit",
        "button.captcha-verify",
        "[class*='captcha'][class*='submit']",
        "[class*='captcha'][class*='button']",
    ]

    async def _find_container(self):
        """Kapcha rasm konteynerini topish."""
        for selector in self.CONTAINER_SELECTORS:
            try:
                element = await self.page.query_selector(selector)
                if element:
                    # Ko'rinadigan elementni tanlash
                    is_visible = await element.is_visible()
                    if is_visible:
                        logger.info(f"Kapcha konteyneri topildi: '{selector}'")
                        return element, selector
            except Exception as exc:
                logger.debug(f"Konteyner selector '{selector}' xatosi: {exc}")
        return None, None

    async def _take_captcha_screenshot(self, element) -> Optional[bytes]:
        """Kapcha konteynerining screenshot ini bytes sifatida olish."""
        try:
            screenshot_bytes = await element.screenshot(type="png")
            logger.info(f"Kapcha screenshot olindi ({len(screenshot_bytes)} bayt).")
            return screenshot_bytes
        except Exception as exc:
            logger.error(f"Kapcha screenshot xatosi: {exc}")
            # Konteyner screenshot bermasa, to'liq sahifa screenshot
            try:
                logger.info("To'liq sahifa screenshot urinilmoqda...")
                screenshot_bytes = await self.page.screenshot(type="png", full_page=False)
                return screenshot_bytes
            except Exception as exc2:
                logger.error(f"To'liq sahifa screenshot ham xato: {exc2}")
                return None

    def _screenshot_to_base64(self, screenshot_bytes: bytes) -> str:
        """Bytes ni base64 string ga aylantirish."""
        return base64.b64encode(screenshot_bytes).decode("utf-8")

    def _parse_coordinates(self, result: str) -> list[tuple[int, int]]:
        """
        2Captcha coordinatescaptcha natijasini tahlil qilish.

        Kutilgan format: "x1=10,y1=20" yoki "x1=10,y1=20/x2=30,y2=40" (bir nechta klik)
        """
        coordinates = []
        try:
            # Bitta yoki bir nechta koordinata: "x=10,y=20" yoki "10,20"
            parts = result.strip().split("/")
            for part in parts:
                part = part.strip()
                if not part:
                    continue

                # "x1=10,y1=20" formati
                if "x" in part.lower() and "y" in part.lower():
                    import re
                    nums = re.findall(r'\d+', part)
                    if len(nums) >= 2:
                        coordinates.append((int(nums[0]), int(nums[1])))

                # "10,20" formati
                elif "," in part:
                    nums = part.split(",")
                    if len(nums) >= 2:
                        coordinates.append((int(nums[0].strip()), int(nums[1].strip())))

            logger.info(f"Koordinatalar tahlil qilindi: {coordinates}")
        except Exception as exc:
            logger.error(f"Koordinata tahlilida xato (input='{result}'): {exc}")

        return coordinates

    async def _get_element_offset(self, element) -> tuple[int, int]:
        """
        Elementning sahifadagi mutlaq pozitsiyasini olish.
        Bu koordinatalarni to'g'ri hisoblash uchun kerak.
        """
        try:
            box = await element.bounding_box()
            if box:
                return int(box["x"]), int(box["y"])
        except Exception as exc:
            logger.debug(f"Element offset xatosi: {exc}")
        return 0, 0

    async def solve(self) -> bool:
        logger.info("IconCaptcha (koordinata) hal qilinmoqda...")

        # 1. Kapcha konteynerini topish
        await asyncio.sleep(1)  # Kapcha yuklanishini kutish
        container, selector = await self._find_container()

        if not container:
            logger.info("IconCaptcha konteyneri topilmadi — o'tkazib yuborilmoqda.")
            return True

        # 2. Screenshot olish
        screenshot_bytes = await self._take_captcha_screenshot(container)
        if not screenshot_bytes:
            logger.error("Kapcha screenshot olinmadi.")
            return False

        # 3. Base64 ga aylantirish
        image_b64 = self._screenshot_to_base64(screenshot_bytes)

        # 4. 2Captcha ga yuborish (coordinatescaptcha metodi)
        payload = {
            "key":       self.api_key,
            "method":    "coordinatescaptcha",
            "body":      image_b64,
            "imginstructions": (
                "Click on all icons that match the target icon shown. "
                "If there is a target icon shown separately, click only on matching icons."
            ),
            "json":      0,
        }

        loop = asyncio.get_event_loop()
        task_id = await loop.run_in_executor(None, self._submit_task, payload)
        if not task_id:
            logger.error("2Captcha ga IconCaptcha vazifasi yuborilmadi.")
            return False

        # 5. Koordinatalarni kutish
        result = await self._await_result(task_id)
        if not result:
            logger.error("2Captcha dan IconCaptcha koordinatalari olinmadi.")
            return False

        # 6. Koordinatalarni tahlil qilish
        coordinates = self._parse_coordinates(result)
        if not coordinates:
            logger.error(f"Koordinatalar tahlil qilinmadi. Xom natija: '{result}'")
            return False

        # 7. Element ofsetini olish (nisbiy → mutlaq koordinata)
        offset_x, offset_y = await self._get_element_offset(container)
        logger.info(f"Konteyner ofset: x={offset_x}, y={offset_y}")

        # 8. Har bir koordinataga klik qilish
        for i, (rel_x, rel_y) in enumerate(coordinates):
            abs_x = offset_x + rel_x
            abs_y = offset_y + rel_y
            logger.info(f"Klik {i+1}/{len(coordinates)}: nisbiy=({rel_x},{rel_y}), mutlaq=({abs_x},{abs_y})")
            try:
                await self.page.mouse.click(abs_x, abs_y)
                await asyncio.sleep(0.4)  # Kliklar orasida qisqa pauza
            except Exception as exc:
                logger.error(f"Mouse klik xatosi ({abs_x},{abs_y}): {exc}")
                return False

        # 9. Tasdiqlash tugmasini bosish
        await asyncio.sleep(0.5)
        for submit_selector in self.SUBMIT_SELECTORS:
            try:
                btn = await self.page.query_selector(submit_selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info(f"Tasdiqlash tugmasi bosildi: '{submit_selector}'")
                    break
            except Exception as exc:
                logger.debug(f"Submit selector '{submit_selector}' xatosi: {exc}")

        # 10. Muvaffaqiyatni tekshirish
        await asyncio.sleep(1.5)
        success = await self._wait_for(
            ".iconcaptcha-success, .captcha-success, [class*='captcha-correct']",
            5_000
        )
        if success:
            logger.info("IconCaptcha muvaffaqiyatli yechildi ✅")
            return True

        # Xato elementini ham tekshirish
        error = await self._wait_for(
            ".iconcaptcha-error, .captcha-error, [class*='captcha-wrong']",
            3_000
        )
        if error:
            logger.warning("IconCaptcha noto'g'ri yechildi ❌ — qayta urinish kerak.")
            return False

        # Muvaffaqiyat yoki xato ko'rinmasa, davom etish
        logger.info("IconCaptcha: natija aniqlanmadi, davom etilmoqda.")
        return True


# ──────────────────────────────────────────────────────────────────────────
# Factory funksiya
# ──────────────────────────────────────────────────────────────────────────
def get_solver(
    captcha_type: str,
    page: Page,
    api_key: str = TWO_CAPTCHA_API_KEY
) -> Optional[CaptchaSolverBase]:
    """
    Kapcha turiga qarab to'g'ri solver instansini qaytaradi.

    Args:
        captcha_type : 'icon' | 'recaptcha' | 'cloudflare'
        page         : Playwright sahifa obyekti
        api_key      : 2Captcha API kaliti (ixtiyoriy, default config dan)

    Returns:
        Tegishli solver obyekti yoki None (noma'lum tur)
    """
    solvers = {
        "cloudflare": CloudflareTurnstileSolver,
        "recaptcha":  ReCaptchaSolver,
        "icon":       IconCaptchaSolver,
    }

    solver_class = solvers.get(captcha_type)
    if solver_class is None:
        logger.error(
            f"Noma'lum kapcha turi: '{captcha_type}'. "
            f"Mumkin bo'lganlar: {list(solvers.keys())}"
        )
        return None

    return solver_class(page, api_key)
