"""Submit a bid on ponisha.ir using Selenium.

Ponisha's proposal dialog is a React/MUI modal with these exact input IDs:
  #input-amount   (type="text", name="amount")  — price in Toman
  #input-days     (type="number", name="days")   — delivery days
  #input-description (textarea, name="description") — proposal text

The form also has a "payment request" section (payments.0.amount etc.) which
we leave untouched — ponisha auto-fills it from the main amount.

Note: the driver is shared with the live-browser window — callers must hold
BotApp._lock while calling submit().
"""

import logging
import os
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from ponishabot import paths
from ponishabot.models import BidPlan, Project

logger = logging.getLogger(__name__)

# ── Exact selectors confirmed from ponisha.ir's proposal dialog ──────────
# Price input: MUI TextField with id="input-amount", name="amount"
SEL_AMOUNT = (By.CSS_SELECTOR, "#input-amount")
# Duration input: MUI TextField with id="input-days", name="days"
SEL_DAYS   = (By.CSS_SELECTOR, "#input-days")
# Description: textarea with id="input-description", name="description"
SEL_DESC   = (By.CSS_SELECTOR, "#input-description")
# Submit button, scoped to the dialog. The project page also carries a button
# reading "ارسال پیشنهاد" (project-action-button) that opens the dialog; matching
# it here clicked the page behind the overlay and raised
# "element click intercepted". Anchoring on the dialog container keeps the
# click inside the form.
SEL_DIALOG = (By.CSS_SELECTOR, '[role="dialog"], .MuiDialog-container')
SEL_SUBMIT = (By.XPATH,
              '//*[@role="dialog" or contains(@class,"MuiDialog-container")]'
              '//button[contains(., "ثبت پیشنهاد") or contains(., "ارسال پیشنهاد")]')
SEL_SUBMIT_FALLBACK = (By.XPATH,
                       '//*[@role="dialog" or contains(@class,"MuiDialog-container")]'
                       '//button[@type="submit"]')
# Cancel button: type="button" with text "لغو"
# "Open bid dialog" button on the project page — several possible texts
OPEN_BID_XPATHS = [
    '//button[contains(., "ارسال پیشنهاد")]',
    '//button[contains(., "پیشنهاد بده")]',
    '//a[contains(., "ارسال پیشنهاد") and contains(@class, "btn")]',
]
# Confirmation that the bid actually landed.
#
# Scope matters more than the phrases: `//*[contains(text(), ...)]` matches the
# <title> element, and a project whose title contains "ثبت شد" (a common Persian
# phrase) then matches on page load — before anything is submitted. Every bid
# reported "Bid confirmed on page" and bid_log filled up while ponisha received
# nothing. Restrict to elements that can actually be a toast/dialog message,
# and require them to be visible.
SUCCESS_XPATH = (
    '(//div | //span | //p | //h1 | //h2 | //h3 | //h4 | //h5 | //h6 | //li)'
    '[not(ancestor::title)][not(ancestor::head)]'
    '[not(ancestor::next-route-announcer)]'   # Next.js a11y node, always role=alert
    '[contains(@class, "alert-success") or contains(@class, "toast-success") '
    ' or contains(@class, "MuiAlert-message") or contains(@class, "MuiAlert-standardSuccess") '
    ' or contains(@class, "MuiAlert-filledSuccess")]'
    ' | '
    '(//div | //span | //p)[not(ancestor::next-route-announcer)]'
    '[contains(text(), "با موفقیت ارسال") '
    ' or contains(text(), "با موفقیت ثبت") '
    ' or contains(text(), "پیشنهاد شما ارسال شد") '
    ' or contains(text(), "پیشنهاد شما ثبت شد")]'
)
# "Sponsored bid" checkbox — decline it (we don't want to pay extra)
SPONSORED_CHECKBOX = (By.CSS_SELECTOR, '.css-viuvdr input[type="checkbox"]')

# The site's validation text. MUI renders it as helper text under each field
# (class contains "Mui-error"/"MuiFormHelperText") or as an alert/toast.
# Deliberately not `//*[contains(@class,"error")]`: that also matches the input
# wrapper around the "تومان" adornment, so a rejected bid reported the currency
# label as its error message.
ERROR_XPATH = (
    '//*[contains(@class, "Mui-error") or contains(@class, "MuiFormHelperText") '
    ' or contains(@class, "alert-danger") '
    ' or (contains(@class, "MuiAlert") '
    '     and not(contains(@class, "Success")) '
    '     and not(contains(@class, "success")) '
    '     and not(contains(@class, "MuiAlert-message")))]'
)
# Strings that are field furniture rather than a complaint.
ERROR_NOISE = {"تومان", "روز", "٪", "%", "عدد", "لغو", "ثبت پیشنهاد"}


class PonishaBidder:
    def __init__(self, auth, proposal_text: str):
        self.auth = auth
        self.proposal_text = proposal_text

    def _wait_for(self, locator, timeout=10):
        """Wait for an element and return it, or None."""
        try:
            return WebDriverWait(self.auth.driver, timeout).until(
                EC.presence_of_element_located(locator))
        except Exception:
            return None

    def _robust_click(self, el, locator=None) -> bool:
        """Click an element even when something overlaps or replaces it.

        Two failure modes on this site:
          - "element click intercepted" — a sticky footer or the dialog
            backdrop covers the button, so the pointer event never reaches it.
          - "stale element reference" — React re-renders after we looked the
            element up, so our handle points at a node that is no longer in
            the DOM. The click animation can still be visible in the browser
            while nothing is actually submitted.
        Scroll first, retry with a fresh lookup when stale, then fall back to
        a JS click for the intercepted case.
        """
        for attempt in (1, 2):
            try:
                self.auth.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.4)
                el.click()
                return True
            except Exception as e:
                msg = str(e).lower()
                if "stale" in msg and locator and attempt == 1:
                    fresh = self._wait_for(locator, timeout=5)
                    if fresh is not None:
                        logger.info("Element was re-rendered — retrying with a fresh handle")
                        el = fresh
                        continue
                    logger.warning("Element went stale and did not come back")
                    return False
                if "intercepted" not in msg:
                    logger.warning(f"Click failed: {str(e)[:80]}")
                    return False
                break
        # reached only for the "intercepted" case
        try:
            self.auth.driver.execute_script("arguments[0].click();", el)
            logger.info("Clicked via JS (overlay intercepted the pointer)")
            return True
        except Exception as e2:
            logger.warning(f"JS click also failed: {str(e2)[:80]}")
            return False

    @staticmethod
    def _values_match(actual: str, expected: str) -> bool:
        """Is a field's displayed value the one we typed?

        The site formats numbers as it accepts them ("8000000" is echoed as
        "8,000,000") and Persian digits may come back, so compare on digits
        only rather than the raw string — otherwise a correctly filled field
        reads as a failure and the bid aborts.
        """
        persian = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        actual, expected = str(actual), str(expected)
        a = "".join(ch for ch in actual.translate(persian) if ch.isdigit())
        e = "".join(ch for ch in expected.translate(persian) if ch.isdigit())
        if not e:
            return actual.strip() == expected.strip()
        return a.lstrip("0") == e.lstrip("0")

    def _fill_field(self, locator, value, clear=True):
        """Type into a text/number input so the framework registers it.

        These are React/MUI inputs: setting `.value` from JavaScript updates the
        DOM but leaves React's state empty, so the field *looks* filled while
        the form still submits blank — the site then answers "مبلغ پیشنهادی را
        وارد کنید" and the bid never lands. Verified values are not enough;
        only real key events reach React.

        Typing is therefore the primary path. JS is kept for the rare input
        that refuses keyboard focus, and it is only trusted after React has
        echoed the value back through get_attribute.
        """
        el = self._wait_for(locator, timeout=10)
        if el is None:
            return False
        expected = str(value)
        try:
            self.auth.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", el)
            time.sleep(0.2)

            # Primary: real key events from the keyboard
            el.click()
            if clear:
                el.send_keys(Keys.CONTROL + "a")
                el.send_keys(Keys.DELETE)
                time.sleep(0.1)
            el.send_keys(expected)
            time.sleep(0.3)
            actual = (el.get_attribute("value") or "").strip()
            if self._values_match(actual, expected):
                return True

            # Fallback for inputs that ignore the keyboard
            self.auth.driver.execute_script(
                "var e=arguments[0], v=arguments[1];"
                "var setter=Object.getOwnPropertyDescriptor("
                "  window.HTMLInputElement.prototype,'value').set;"
                "setter.call(e, v);"                       # React's own setter
                "e.dispatchEvent(new Event('input',{bubbles:true}));"
                "e.dispatchEvent(new Event('change',{bubbles:true}));",
                el, expected
            )
            time.sleep(0.3)
            actual = (el.get_attribute("value") or "").strip()
            if self._values_match(actual, expected):
                return True

            logger.warning(f"Field verify failed for {locator}: "
                           f"expected {expected!r}, got {actual!r}")
            return False
        except Exception as e:
            logger.warning(f"Failed to fill field {locator}: {e}")
            return False

    def _fill_payment_steps(self, driver, plan: BidPlan) -> bool:
        """Fill the escrow payment section of the proposal form.
        Each step has a title and a percent of the total price.

        Returns True only when every planned step got a row and a value —
        the caller aborts the bid otherwise, because a short list makes the
        escrow total disagree with the bid price and the site refuses it.
        """
        steps = plan.payment_steps
        if not steps:
            steps = [{"title": "پرداخت کامل", "percent": 100}]

        made = 0
        for i, step in enumerate(steps):
            if i > 0:
                # Add a row for step i, then WAIT for its input to appear.
                # A plain .click() is not enough: the button sits under the
                # dialog's sticky footer, so the pointer event gets intercepted
                # and the row is never created — the old code swallowed that as
                # a warning and still reported every step as filled.
                try:
                    add_btn = self._wait_for(
                        (By.XPATH, '//button[contains(., "اضافه کردن پرداخت")]'),
                        timeout=5)
                    if add_btn is None or not self._robust_click(
                            add_btn,
                            (By.XPATH, '//button[contains(., "اضافه کردن پرداخت")]')):
                        logger.warning(f"Could not add payment step {i + 1} "
                                       f"— keeping {made} step(s)")
                        break
                except Exception as e:
                    logger.warning(f"Could not add payment step {i + 1}: {e}")
                    break
                # the new row mounts asynchronously; wait for its own input
                if self._wait_for(
                        (By.CSS_SELECTOR, f'input[name="payments.{i}.title"]'),
                        timeout=5) is None:
                    logger.warning(f"Payment row {i + 1} never appeared "
                                   f"— keeping {made} step(s)")
                    break

            # fill title
            title_ok = False
            try:
                title_el = driver.find_element(By.CSS_SELECTOR,
                    f'input[name="payments.{i}.title"]')
                title_el.clear()
                title_el.send_keys(step["title"])
                title_ok = True
            except Exception as e:
                logger.warning(f"payment.{i}.title not filled: {e}")

            # fill amount = price * percent / 100
            amount_ok = not plan.price  # nothing to write when there is no price
            if plan.price:
                amount = plan.price * step["percent"] // 100
                try:
                    amount_el = driver.find_element(By.CSS_SELECTOR,
                        f'input[name="payments.{i}.amount"]')
                    amount_el.clear()
                    amount_el.send_keys(str(amount))
                    actual = (amount_el.get_attribute("value") or "").strip()
                    # _values_match takes strings; passing the int raised
                    # "'int' object has no attribute 'translate'" and the row
                    # was reported filled while its amount stayed empty.
                    amount_ok = self._values_match(actual, str(amount))
                    if not amount_ok:
                        logger.warning(f"payment.{i}.amount shows {actual!r}, "
                                       f"expected {amount}")
                except Exception as e:
                    logger.warning(f"payment.{i}.amount not filled: {e}")

            if title_ok and amount_ok:
                made += 1

        logger.info(f"Payment structure: {made} of {len(steps)} step(s) filled")
        return made == len(steps)

    def submit(self, project: Project, plan: BidPlan) -> str:
        """Open the project page, open the bid dialog, fill the form, submit.

        Returns one of:
          "submitted" — the submit button was clicked and the site confirmed it
          "unconfirmed" — the button was clicked but no confirmation appeared,
                          so the bid may or may not have landed
          "aborted" — we never reached the click (dialog missing, field not
                      filled, driver timed out); nothing was sent

        The caller needs this distinction: "unconfirmed" must be recorded so
        the monitor never double-bids, but "aborted" must NOT be, or a
        transient failure permanently burns the project.
        """
        driver = self.auth.driver
        if driver is None:
            self.auth.inject_cookies_into_driver()
            driver = self.auth.driver

        clicked = False  # set right before submit.click(), read by the handler
        try:
            logger.info(f"Opening project for bid: {project.url}")
            driver.get(project.url)
            time.sleep(3)

            # ── Step 1: click "ارسال پیشنهاد" to open the MUI dialog ──
            open_btn = None
            for xpath in OPEN_BID_XPATHS:
                try:
                    open_btn = WebDriverWait(driver, 6).until(
                        EC.element_to_be_clickable((By.XPATH, xpath)))
                    if open_btn:
                        break
                except Exception:
                    continue
            if open_btn:
                if not self._robust_click(
                        open_btn,
                        (By.XPATH, '//button[contains(., "ارسال پیشنهاد")]')):
                    logger.warning("Could not click the bid button")
                    return "aborted"
                logger.info("Opened proposal dialog")
                time.sleep(3)
            else:
                logger.warning("Bid button not found on project page")
                return "aborted"

            # ── Step 2: decline "sponsored bid" upsell if present ──
            try:
                cb = driver.find_element(*SPONSORED_CHECKBOX)
                if cb.is_selected():
                    cb.click()
                    logger.info("Unchecked sponsored bid checkbox")
            except Exception:
                pass  # sponsored section may not be present

            # ── Step 3: fill amount (price) — REQUIRED ──
            if plan.price:
                if self._fill_field(SEL_AMOUNT, plan.price):
                    logger.info(f"Price entered: {plan.price:,} Toman")
                else:
                    logger.error("Amount input (#input-amount) not filled — aborting submit")
                    return "aborted"

            # ── Step 4: fill duration (days) — required when planned ──
            if plan.duration_days:
                if self._fill_field(SEL_DAYS, plan.duration_days):
                    logger.info(f"Duration entered: {plan.duration_days} days")
                else:
                    logger.error("Days input (#input-days) not filled — aborting submit")
                    return "aborted"

            # ── Step 5: fill description (AI-generated proposal takes priority) ──
            desc_text = plan.proposal or self.proposal_text
            if desc_text:
                if self._fill_field(SEL_DESC, desc_text):
                    logger.info(f"Description entered ({len(desc_text)} chars, "
                                f"{'AI' if plan.proposal else 'template'})")
                else:
                    logger.error("Description textarea (#input-description) not filled — aborting submit")
                    return "aborted"

            # ── Step 6: fill escrow payment structure ──
            # A partial fill is worse than none: the site requires the payment
            # rows to sum to the bid amount, so fewer rows than the plan asked
            # for makes the submit fail validation ("مبلغ وارد شده بیش از مبلغ
            # کل پروژه است"). Abort while the project is still retryable.
            if not self._fill_payment_steps(driver, plan):
                logger.error("Payment rows did not match the plan — aborting submit")
                return "aborted"

            # ── Step 7: submit ──
            submit_btn = self._wait_for(SEL_SUBMIT, timeout=5)
            if not submit_btn:
                submit_btn = self._wait_for(SEL_SUBMIT_FALLBACK, timeout=3)
            if not submit_btn:
                logger.warning("Submit button not found")
                return "aborted"

            if not self._robust_click(submit_btn,
                                      (By.CSS_SELECTOR,
                                       'button.action-buttons__submit')):
                logger.warning("Could not click the submit button")
                return "aborted"
            clicked = True
            logger.info("Clicked submit — waiting for confirmation…")

            # The form was on screen a moment ago; accept a vanished #input-amount
            # as proof the dialog closed, which only happens on a real submit.
            # Text matching alone kept reporting success on static page furniture.
            dialog_was_open = bool(driver.find_elements(By.CSS_SELECTOR, SEL_AMOUNT[1]))

            success = False
            error_msg = None
            for _ in range(15):  # Wait up to 15 seconds
                time.sleep(1)
                try:
                    for el in driver.find_elements(By.XPATH, SUCCESS_XPATH):
                        if el.is_displayed() and (el.text or "").strip():
                            success = True
                            logger.info(f"Bid confirmed on page: {el.text[:60]!r}")
                            break
                    if success:
                        break
                except Exception:
                    pass
                # dialog gone + no error shown on it => submitted
                if dialog_was_open:
                    still_open = bool(driver.find_elements(By.CSS_SELECTOR,
                                                           SEL_AMOUNT[1]))
                    if not still_open:
                        success = True
                        logger.info("Bid confirmed: proposal dialog closed")
                        break
                # Read the site's OWN validation text.
                #
                # The old selector was `//*[contains(@class,"error")]`, which
                # matches any element whose class merely contains "error" —
                # including the MUI InputBase wrapper that holds the "تومان"
                # adornment. That is why a rejected bid logged «Bid failed:
                # تومان» and told us nothing. Scope to helper text and alerts,
                # drop label noise, and keep the longest message.
                try:
                    texts = []
                    for el in driver.find_elements(By.XPATH, ERROR_XPATH):
                        if not el.is_displayed():
                            continue
                        t = (el.text or "").strip()
                        if len(t) > 3 and t not in texts and t not in ERROR_NOISE:
                            texts.append(t)
                    if texts:
                        error_msg = max(texts, key=len)
                        break
                except Exception:
                    pass

            if success:
                logger.info("Bid submitted successfully")
                return "submitted"
            if error_msg:
                # The site rejected the form, so nothing was sent — safe to retry.
                logger.error(f"Bid failed: {error_msg}")
                return "aborted"
            logger.warning("No success message detected — verify manually in ponisha's 'My proposals' page")
            return "unconfirmed"

        except Exception as e:
            logger.error(f"Bid submission error: {e}")
            try:
                os.makedirs(paths.DEBUG_DIR, exist_ok=True)
                driver.save_screenshot(
                    os.path.join(paths.DEBUG_DIR, f"bid_error_{project.id}.png"))
            except Exception:
                pass
            # If the click already went through, the bid may have landed even
            # though the driver died while waiting for the response.
            return "unconfirmed" if clicked else "aborted"
