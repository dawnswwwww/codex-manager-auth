from datetime import date, datetime, timedelta
import random
import re
import time

from .models import NonRetryableStageError, PageTerminalState, RegistrationFlowOutcome, StageExecutionResult
from .openai_selectors import (
    ACCOUNT_DEACTIVATED_MESSAGE_SELECTORS,
    ADD_PHONE_URL_KEYWORDS,
    CSS_INVALID_CODE_ERROR,
    CSS_INVALID_PASSWORD_ERROR,
    CSS_L_CODE,
    CSS_L_CONSENT_BTN,
    CSS_L_CONTINUE_CODE,
    CSS_L_CONTINUE_EMAIL,
    CSS_L_CONTINUE_PWD,
    CSS_L_EMAIL,
    CSS_L_PASSWORDLESS_LOGIN_BTN,
    CSS_L_PASSWORD,
    CSS_OA_BIRTHDAY_HIDDEN_INPUT,
    CSS_OA_ACCOUNT_EXISTS_ERROR,
    CSS_OA_AGE_INPUT_SELECTORS,
    CSS_OA_BIRTHDAY_CONFIRM_BUTTON_SELECTORS,
    CSS_OA_BIRTHDAY_YEAR,
    CSS_OA_CODE_INPUT,
    CSS_OA_CONTINUE_BTN,
    CSS_OA_CREATE_ACCOUNT_BTN,
    CSS_OA_EMAIL_INPUT,
    CSS_OA_NAME_INPUT,
    CSS_OA_PASSWORD_BTN,
    CSS_OA_PASSWORD_INPUT,
    CSS_OA_SIGNUP_LINK,
    RATE_LIMIT_MESSAGE_SELECTORS,
    RATE_LIMIT_RETRY_BUTTON_SELECTORS,
)
from .microsoft_mail_api import fetch_verification_code
from .playwright_helpers import (
    close_page_quietly,
    find_visible_selector,
    human_click,
    human_delay,
    human_type,
    is_selector_visible,
    new_stealth_page,
)


async def retry_rate_limit_error_page(page) -> bool:
    message_selector = await find_visible_selector(page, RATE_LIMIT_MESSAGE_SELECTORS)
    retry_selector = await find_visible_selector(page, RATE_LIMIT_RETRY_BUTTON_SELECTORS)
    if not message_selector or not retry_selector:
        return False

    print("[OpenAI] Rate limit page detected. Clicking retry...")
    await human_click(page, retry_selector)
    await human_delay(1, 2)
    return True


def get_hard_failure_reason_from_url(url: str) -> str | None:
    normalized_url = (url or "").lower()
    for keyword in ADD_PHONE_URL_KEYWORDS:
        if keyword in normalized_url:
            return "OpenAI required a phone number on the add-phone page"
    return None


async def get_hard_failure_reason(page) -> str | None:
    reason = get_hard_failure_reason_from_url(getattr(page, "url", ""))
    if reason:
        return reason

    account_deactivated_selector = await find_visible_selector(page, ACCOUNT_DEACTIVATED_MESSAGE_SELECTORS)
    if account_deactivated_selector:
        return "OpenAI reported account_deactivated during verification"

    return None


async def raise_for_hard_failure_page(page):
    reason = await get_hard_failure_reason(page)
    if reason:
        raise NonRetryableStageError(reason)


async def get_login_terminal_state(page, expected_callback_url: str) -> PageTerminalState | None:
    if page.url.startswith(expected_callback_url):
        return PageTerminalState(status="callback", detail=page.url)

    reason = await get_hard_failure_reason(page)
    if reason:
        return PageTerminalState(status="hard_failure", detail=reason)

    if "/consent" in page.url and await is_selector_visible(page, CSS_L_CONSENT_BTN):
        return PageTerminalState(status="consent", detail=page.url)

    return None


async def wait_for_selector_with_rate_limit_retry(page, selector: str, timeout: int = 15000):
    await raise_for_hard_failure_page(page)
    retried_rate_limit = await retry_rate_limit_error_page(page)

    try:
        return await page.wait_for_selector(selector, timeout=timeout)
    except Exception:
        await raise_for_hard_failure_page(page)
        if not retried_rate_limit:
            retried_rate_limit = await retry_rate_limit_error_page(page)
        if not retried_rate_limit:
            raise
        await raise_for_hard_failure_page(page)
        return await page.wait_for_selector(selector, timeout=timeout)


async def wait_for_callback_url(page, expected_callback_url: str, timeout_s: float = 15.0, poll_interval_s: float = 0.2) -> str:
    deadline = time.monotonic() + timeout_s

    while time.monotonic() <= deadline:
        if page.url.startswith(expected_callback_url):
            return page.url
        await raise_for_hard_failure_page(page)
        await human_delay(poll_interval_s, poll_interval_s)

    raise RuntimeError(f"OAuth callback was not reached within {timeout_s:.1f}s")


async def execute_stage_with_retry(stage_name: str, operation, max_attempts: int) -> StageExecutionResult:
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        try:
            value = await operation()
            return StageExecutionResult(status="success", attempts=attempt, value=value)
        except NonRetryableStageError as exc:
            print(f"[{stage_name}] Non-retryable failure: {exc}")
            return StageExecutionResult(status="failed", attempts=attempt, error=str(exc))
        except Exception as exc:
            last_error = str(exc)
            print(f"[{stage_name}] Attempt {attempt}/{max_attempts} failed: {last_error}")

    return StageExecutionResult(status="failed", attempts=max_attempts, error=last_error)


async def verify_registration_complete(context, email: str, auth_url: str):
    page = await new_stealth_page(context)
    try:
        print("[OpenAI] Verifying registration on a fresh OAuth page...")
        await page.goto(auth_url, wait_until="domcontentloaded")
        await wait_for_selector_with_rate_limit_retry(page, CSS_L_EMAIL, timeout=15000)
        await human_type(page, CSS_L_EMAIL, email)
        await human_click(page, CSS_L_CONTINUE_EMAIL)
        await human_delay(2, 4)

        if await is_selector_visible(page, CSS_L_PASSWORD):
            return

        if await is_selector_visible(page, CSS_OA_PASSWORD_INPUT):
            raise RuntimeError("Registration verification fell back to sign-up password screen")

        raise RuntimeError(f"Registration verification did not reach the login password screen (url={page.url})")
    finally:
        await close_page_quietly(page)


async def clear_and_type_locator(locator, text: str):
    await locator.click()
    await human_delay(0.3, 0.6)
    await locator.press("Control+a")
    await human_delay(0.2, 0.4)
    await locator.press_sequentially(text, delay=random.randint(80, 150))
    await human_delay(0.5, 1.0)


async def focus_and_type_locator(locator, text: str):
    await locator.focus()
    await human_delay(0.2, 0.4)
    await locator.press("Control+a")
    await human_delay(0.2, 0.4)
    await locator.press_sequentially(text, delay=random.randint(80, 150))
    await human_delay(0.2, 0.4)
    await locator.press("Tab")
    await human_delay(0.5, 1.0)


async def has_selector(page, selector: str) -> bool:
    try:
        return await page.locator(selector).count() > 0
    except Exception:
        return False


async def set_hidden_birthday_value(page, birthday_value: str):
    locator = page.locator(CSS_OA_BIRTHDAY_HIDDEN_INPUT)
    await locator.evaluate(
        """(node, value) => {
            node.value = value;
            node.dispatchEvent(new Event('input', { bubbles: true }));
            node.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        birthday_value,
    )
    await human_delay(0.3, 0.6)


def generate_birth_profile(local_part: str) -> tuple[str, str, str, str]:
    name = re.sub(r'\d+', '', local_part)
    start = date(1980, 1, 1)
    end = date(2006, 12, 31)
    birthday = start + timedelta(days=random.randint(0, (end - start).days))
    today = date.today()
    age = today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))
    return name, birthday.isoformat(), str(age), str(birthday.year)


async def fill_profile_age(page, name: str, age_value: str, year_value: str, birthday_value: str):
    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_NAME_INPUT, timeout=15000)
    name_el = page.locator(CSS_OA_NAME_INPUT)
    await name_el.fill("")
    await human_type(page, CSS_OA_NAME_INPUT, name)
    await human_delay(0.5, 1.0)

    if await is_selector_visible(page, CSS_OA_BIRTHDAY_YEAR):
        await focus_and_type_locator(page.locator(CSS_OA_BIRTHDAY_YEAR), year_value)
        await maybe_confirm_birthday_dialog(page)
        return

    age_selector = await find_visible_selector(page, CSS_OA_AGE_INPUT_SELECTORS)
    if age_selector:
        age_el = page.locator(age_selector)
        await age_el.fill("")
        await clear_and_type_locator(age_el, age_value)
        await maybe_confirm_birthday_dialog(page)
        return

    if await has_selector(page, CSS_OA_BIRTHDAY_HIDDEN_INPUT):
        await set_hidden_birthday_value(page, birthday_value)
        await maybe_confirm_birthday_dialog(page)
        return

    raise RuntimeError("Could not find a visible age/year input on the profile page")


async def maybe_confirm_birthday_dialog(page) -> bool:
    confirm_selector = await find_visible_selector(page, CSS_OA_BIRTHDAY_CONFIRM_BUTTON_SELECTORS)
    if not confirm_selector:
        return False

    print("[OpenAI] Birthday confirmation dialog detected. Confirming...")
    await human_click(page, confirm_selector)
    await human_delay(1, 2)
    return True


async def has_invalid_code_error(page) -> bool:
    error_el = page.locator(CSS_INVALID_CODE_ERROR)
    try:
        return await error_el.count() > 0 and await error_el.is_visible()
    except Exception:
        return False


async def submit_verification_code_with_retry(
    page,
    selector: str,
    access_token: str,
    submit_mode: str = "enter",
    submit_selector: str | None = None,
    max_attempts: int = 3,
    attempted_codes: set[str] | None = None,
) -> str:
    attempted_codes = attempted_codes if attempted_codes is not None else set()

    for _ in range(max_attempts):
        code = await fetch_verification_code(access_token, exclude_codes=set(attempted_codes))
        attempted_codes.add(code)
        print(f"[OpenAI] Entering code: {code}")
        await page.locator(selector).fill("")
        await human_type(page, selector, code)
        await human_delay(0.5, 1.0)

        if submit_mode == "click":
            if not submit_selector:
                raise ValueError("submit_selector is required when submit_mode='click'")
            await human_click(page, submit_selector)
        else:
            await page.locator(selector).press("Enter")

        await human_delay(2, 4)
        if not await has_invalid_code_error(page):
            return code

        print("[OpenAI] Verification code was rejected, checking inbox for a newer code...")

    raise RuntimeError("Verification code was rejected after max attempts")


async def openai_register(page, email: str, password: str, access_token: str, auth_url: str) -> RegistrationFlowOutcome:
    print("[OpenAI] Using password from account file.")
    print("[OpenAI] Navigating to OAuth page...")
    await page.goto(auth_url, wait_until="domcontentloaded")
    await human_delay(2, 4)

    print("[OpenAI] Clicking sign up link...")
    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_SIGNUP_LINK, timeout=15000)
    await human_click(page, CSS_OA_SIGNUP_LINK)
    await human_delay(2, 4)

    print(f"[OpenAI] Entering email: {email}")
    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_EMAIL_INPUT, timeout=15000)
    await human_type(page, CSS_OA_EMAIL_INPUT, email)
    await human_click(page, CSS_OA_CONTINUE_BTN)
    await human_delay(2, 4)

    print("[OpenAI] Entering password...")
    try:
        await wait_for_selector_with_rate_limit_retry(page, CSS_OA_PASSWORD_INPUT, timeout=15000)
    except Exception:
        await page.screenshot(path="debug_password_page.png")
        dump = await page.evaluate("""() => {
            return document.querySelectorAll('input, button').length + ' elements: ' +
                Array.from(document.querySelectorAll('input, button')).map(e =>
                    '<' + e.tagName + ' type=' + (e.type||'') + ' name=' + (e.name||'') + ' id=' + (e.id||'')
                ).join(' | ');
        }""")
        print(f"[Debug] Page elements: {dump}")
        raise
    await human_type(page, CSS_OA_PASSWORD_INPUT, password)
    await human_click(page, CSS_OA_PASSWORD_BTN)
    await human_delay(2, 4)

    try:
        error_el = page.locator(CSS_OA_ACCOUNT_EXISTS_ERROR)
        if await error_el.count() > 0 and await error_el.first.is_visible():
            print("[OpenAI] Account already exists. Skipping registration verification and handing off to login stage.")
            return RegistrationFlowOutcome(
                registration_status="already_exists",
                should_verify_registration=False,
            )
    except Exception:
        pass

    print("[OpenAI] Waiting for code input...")
    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_CODE_INPUT, timeout=15000)
    await submit_verification_code_with_retry(page, CSS_OA_CODE_INPUT, access_token)

    local = email.split("@")[0]
    name, birthday, age, year = generate_birth_profile(local)
    print(f"[OpenAI] Confirming age. Name: {name}, Birthday: {birthday}, Age: {age}, Year: {year}")

    try:
        await fill_profile_age(page, name, age, year, birthday)
    except Exception:
        await page.screenshot(path="debug_age_page.png")
        dump = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('input, button, [role="spinbutton"], [data-type]')).map(e =>
                '<' + e.tagName + ' type=' + (e.type||'') + ' name=' + (e.name||'') + ' id=' + (e.id||'') +
                ' data-type=' + (e.dataset.type||'') + ' role=' + (e.getAttribute('role')||'') +
                ' placeholder=' + (e.placeholder||'') + ' text=' + (e.textContent||'').trim().slice(0,30)
            ).join(' | ');
        }""")
        print(f"[Debug] Age page elements: {dump}")
        raise

    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_CREATE_ACCOUNT_BTN, timeout=10000)
    await human_click(page, CSS_OA_CREATE_ACCOUNT_BTN)
    await human_delay(3, 5)
    print("[OpenAI] Registration phase complete.")
    return RegistrationFlowOutcome(registration_status="success", should_verify_registration=True)


async def openai_login_flow(page, email: str, password: str, access_token: str, expected_callback_url: str):
    print("[Login] Using password from account file.")
    await wait_for_selector_with_rate_limit_retry(page, CSS_L_EMAIL, timeout=15000)
    await human_type(page, CSS_L_EMAIL, email)
    await human_click(page, CSS_L_CONTINUE_EMAIL)
    await human_delay(2, 4)

    print("[Login] Entering password...")
    await wait_for_selector_with_rate_limit_retry(page, CSS_L_PASSWORD, timeout=15000)
    await human_type(page, CSS_L_PASSWORD, password)
    await human_click(page, CSS_L_CONTINUE_PWD)
    await human_delay(2, 4)

    print("[Login] Detecting next page state...")
    local = email.split("@")[0]
    name, birthday, age, year = generate_birth_profile(local)
    completed = False
    attempted_codes: set[str] = set()

    for _ in range(3):
        terminal_state = await get_login_terminal_state(page, expected_callback_url)
        if terminal_state:
            if terminal_state.status == "hard_failure":
                raise NonRetryableStageError(terminal_state.detail)
            completed = True
            break

        if await maybe_confirm_birthday_dialog(page):
            continue

        code_el = page.locator(CSS_L_CODE)
        name_el = page.locator(CSS_OA_NAME_INPUT)
        birthday_el = page.locator(CSS_OA_BIRTHDAY_YEAR)
        age_selector = await find_visible_selector(page, CSS_OA_AGE_INPUT_SELECTORS)

        try:
            if await code_el.count() > 0 and await code_el.first.is_visible(timeout=3000):
                print("[Login] Code page found.")
                await submit_verification_code_with_retry(page, CSS_L_CODE, access_token, attempted_codes=attempted_codes)
                continue
        except Exception:
            pass

        try:
            if (await name_el.count() > 0 and await name_el.first.is_visible(timeout=3000)) or \
               (await birthday_el.count() > 0 and await birthday_el.first.is_visible(timeout=3000)) or \
               age_selector:
                print(f"[Login] Profile page found, name: {name}, birthday: {birthday}, age: {age}, year: {year}")
                await fill_profile_age(page, name, age, year, birthday)

                submit_btn = page.locator('button[type="submit"]:has-text("完成帐户创建"), button[type="submit"]:has-text("继续")')
                if await submit_btn.count() > 0:
                    await human_click(page, 'button[type="submit"]:has-text("完成帐户创建"), button[type="submit"]:has-text("继续")')
                await human_delay(3, 5)
                completed = True
                break
        except Exception:
            pass

        if await retry_rate_limit_error_page(page):
            continue

        await human_delay(2, 3)

    if not completed:
        raise RuntimeError("Login flow did not reach a confirmed completion state")

    print("[Login] Login flow complete.")


async def openai_second_login(page, email: str, password: str, access_token: str, auth_url: str, expected_callback_url: str):
    print("[OpenAI] Phase 2: Re-visiting OAuth URL to login...")
    await page.goto(auth_url, wait_until="domcontentloaded")
    await human_delay(2, 4)

    print(f"[OpenAI] Entering email: {email}")
    await wait_for_selector_with_rate_limit_retry(page, CSS_L_EMAIL, timeout=15000)
    await human_type(page, CSS_L_EMAIL, email)
    await human_click(page, CSS_L_CONTINUE_EMAIL)
    await human_delay(2, 4)

    print("[OpenAI] Entering password...")
    await wait_for_selector_with_rate_limit_retry(page, CSS_L_PASSWORD, timeout=15000)
    await human_type(page, CSS_L_PASSWORD, password)
    await human_click(page, CSS_L_CONTINUE_PWD)
    await human_delay(2, 4)
    local = email.split("@")[0]
    name, birthday, age, year = generate_birth_profile(local)
    attempted_codes: set[str] = set()
    saw_retry_error_page = False

    for _ in range(8):
        terminal_state = await get_login_terminal_state(page, expected_callback_url)
        if terminal_state:
            if terminal_state.status == "hard_failure":
                raise NonRetryableStageError(terminal_state.detail)
            if terminal_state.status == "consent":
                print("[OpenAI] Waiting for consent page...")
                await human_click(page, CSS_L_CONSENT_BTN)
                callback_url = await wait_for_callback_url(page, expected_callback_url, timeout_s=30.0)
                print(f"[OpenAI] Consent submitted. Final URL: {callback_url}")
                return callback_url
            if terminal_state.status == "callback":
                print(f"[OpenAI] Callback reached directly. Final URL: {terminal_state.detail}")
                return terminal_state.detail

        if await maybe_confirm_birthday_dialog(page):
            continue

        age_selector = await find_visible_selector(page, CSS_OA_AGE_INPUT_SELECTORS)
        if (
            await is_selector_visible(page, CSS_OA_NAME_INPUT)
            or await is_selector_visible(page, CSS_OA_BIRTHDAY_YEAR)
            or age_selector
            or await has_selector(page, CSS_OA_BIRTHDAY_HIDDEN_INPUT)
        ):
            print(f"[OpenAI] Profile page found during login, name: {name}, birthday: {birthday}, age: {age}, year: {year}")
            await fill_profile_age(page, name, age, year, birthday)
            await human_click(page, 'button[type="submit"]:has-text("完成帐户创建"), button[type="submit"]:has-text("继续")')
            await human_delay(2, 4)
            continue

        if await is_selector_visible(page, CSS_INVALID_PASSWORD_ERROR) and await is_selector_visible(page, CSS_L_PASSWORDLESS_LOGIN_BTN):
            print("[OpenAI] Password login was rejected. Falling back to one-time-code login...")
            await human_click(page, CSS_L_PASSWORDLESS_LOGIN_BTN)
            await human_delay(2, 4)
            continue

        if await is_selector_visible(page, CSS_L_CODE):
            print("[OpenAI] Waiting for verification code input...")
            try:
                await submit_verification_code_with_retry(
                    page,
                    CSS_L_CODE,
                    access_token,
                    submit_mode="click",
                    submit_selector=CSS_L_CONTINUE_CODE,
                    attempted_codes=attempted_codes,
                )
            except RuntimeError as exc:
                if saw_retry_error_page and "Failed to find verification code after max retries" in str(exc):
                    raise RuntimeError("OpenAI verification session hit max_check_attempts after retries") from exc
                raise
            await human_delay(2, 4)
            continue

        if await retry_rate_limit_error_page(page):
            saw_retry_error_page = True
            continue

        await human_delay(2, 3)

    if saw_retry_error_page:
        raise RuntimeError("OpenAI verification session hit max_check_attempts after retries")
    raise RuntimeError("Second login flow did not reach a confirmed completion state")
