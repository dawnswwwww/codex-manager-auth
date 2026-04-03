import asyncio
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import random
import re
import time
from urllib.parse import parse_qs, urlparse

from app_config import APP_CONFIG
import httpx
from playwright.async_api import async_playwright
from playwright_stealth import Stealth


ACCOUNT_FILE = APP_CONFIG.account_file
ACCOUNT_LINE_SEPARATOR = "----"
MAX_STAGE_ATTEMPTS = 3
RESULTS_DIR = Path(__file__).with_name("results")


@dataclass(frozen=True)
class AccountRecord:
    email: str
    password: str
    client_id: str
    refresh_token: str


@dataclass(frozen=True)
class StageExecutionResult:
    status: str
    attempts: int
    error: str = ""


@dataclass(frozen=True)
class AccountExecutionResult:
    email: str
    registration_status: str
    registration_attempts: int
    login_status: str
    login_attempts: int
    overall_status: str
    error: str = ""


def normalize_password(password: str) -> str:
    normalized = password.strip()
    if len(normalized) < 12:
        normalized = normalized + "0" * (12 - len(normalized))
    return normalized


def parse_account_line(line: str, line_number: int) -> AccountRecord:
    parts = [part.strip() for part in line.split(ACCOUNT_LINE_SEPARATOR, maxsplit=3)]
    if len(parts) != 4 or any(not part for part in parts):
        raise ValueError(
            f"Invalid account format on line {line_number}: "
            "expected email----password----client_id----refresh_token"
        )
    email, password, client_id, refresh_token = parts
    return AccountRecord(
        email=email,
        password=password,
        client_id=client_id,
        refresh_token=refresh_token,
    )


def load_accounts(path: Path) -> list[AccountRecord]:
    accounts: list[AccountRecord] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        accounts.append(parse_account_line(line, line_number))
    if not accounts:
        raise ValueError(f"No accounts found in {path}")
    return accounts


def build_results_csv_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return RESULTS_DIR / f"execution_results_{timestamp}.csv"


def append_account_result(csv_path: Path, result: AccountExecutionResult):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(
                [
                    "email",
                    "registration_status",
                    "registration_attempts",
                    "login_status",
                    "login_attempts",
                    "overall_status",
                    "error",
                ]
            )
        writer.writerow(
            [
                result.email,
                result.registration_status,
                result.registration_attempts,
                result.login_status,
                result.login_attempts,
                result.overall_status,
                result.error,
            ]
        )


def get_expected_callback_url() -> str:
    redirect_uri = parse_qs(urlparse(OPENAI_OAUTH_URL).query).get("redirect_uri", [None])[0]
    if not redirect_uri:
        raise RuntimeError("redirect_uri is missing from OPENAI_OAUTH_URL")
    return redirect_uri


# --- Outlook API ---
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
OUTLOOK_BASE = "https://outlook.office.com/api/v2.0"


async def exchange_refresh_token(refresh_token: str, client_id: str) -> str:
    print("[Token] Exchanging refresh token...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_URL, data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "https://outlook.office.com/.default openid profile offline_access",
        })
        if resp.status_code == 200:
            print("[Token] Success!")
            return resp.json()["access_token"]
        else:
            raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text}")


async def fetch_verification_code(access_token: str, max_retries: int = 10, interval: int = 5, exclude_codes: set[str] | None = None) -> str:
    """Poll Outlook inbox for the latest ChatGPT verification code."""
    exclude_codes = exclude_codes or set()
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(base_url=OUTLOOK_BASE, headers=headers, timeout=30) as client:
        for attempt in range(max_retries):
            print(f"[Outlook] Checking inbox (attempt {attempt + 1}/{max_retries})...")
            try:
                resp = await client.get("/me/messages?$top=10&$orderby=ReceivedDateTime desc")
                if resp.status_code == 200:
                    # iterate newest-first and return the first unseen code
                    for msg in resp.json().get("value", []):
                        subject = msg.get("Subject", "")
                        body = msg.get("BodyPreview", "")
                        combined = f"{subject}\n{body}"
                        match = re.search(r'代码为\s*(\d{6})', combined)
                        if not match:
                            match = re.search(r'code\s*(?:is\s*)?(\d{6})', combined, re.IGNORECASE)
                        if not match:
                            match = re.search(r'\b(\d{6})\b', combined)
                        if match:
                            code = match.group(1)
                            if code in exclude_codes:
                                continue
                            print(f"[Outlook] Found latest code: {code}")
                            return code
            except Exception as e:
                print(f"[Outlook] Error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(interval)
    raise RuntimeError("Failed to find verification code after max retries")


OPENAI_OAUTH_URL = APP_CONFIG.openai_oauth_url

# --- Phase 1: Registration selectors ---
CSS_OA_SIGNUP_LINK = 'a[href*="create-account"]'
CSS_OA_EMAIL_INPUT = 'input[type="email"][name="email"]'
CSS_OA_CONTINUE_BTN = 'form button[type="submit"][name="intent"]'
CSS_OA_PASSWORD_INPUT = 'input[type="password"][name="new-password"]'
CSS_OA_PASSWORD_BTN = 'form:has(input[name="new-password"]) button[type="submit"]'
CSS_OA_CODE_INPUT = 'input[name="code"]'
CSS_OA_NAME_INPUT = 'input[name="name"]'
CSS_OA_BIRTHDAY_YEAR = '[data-type="year"]'
CSS_OA_CREATE_ACCOUNT_BTN = 'button[type="submit"]:has-text("完成帐户创建")'
CSS_OA_ACCOUNT_EXISTS_ERROR = 'li:has-text("已存在")'

# --- Phase 2: Login selectors (second OAuth pass) ---
CSS_L_EMAIL = 'input[type="email"][name="email"]'
CSS_L_CONTINUE_EMAIL = 'form button[type="submit"][name="intent"]'
CSS_L_PASSWORD = 'input[name="current-password"]'
CSS_L_CONTINUE_PWD = 'form:has(input[name="current-password"]) button[type="submit"]'
CSS_L_CODE = 'input[name="code"]'
CSS_L_CONTINUE_CODE = 'button[name="intent"][value="validate"]'
CSS_L_CONSENT_BTN = 'button[type="submit"]:has-text("继续")'
CSS_INVALID_CODE_ERROR = 'li:has-text("代码不正确")'
RATE_LIMIT_MESSAGE_SELECTORS = (
    'text=/Rate limit exceeded/i',
    'text=/try again later/i',
)
RATE_LIMIT_RETRY_BUTTON_SELECTORS = (
    'button:has-text("重试")',
    'button:has-text("Retry")',
)


# --- Human-like helpers ---
async def human_delay(min_s: float = 0.5, max_s: float = 2.0):
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_type(page, selector: str, text: str):
    await page.locator(selector).press_sequentially(text, delay=random.randint(50, 150))
    await human_delay(0.3, 0.8)


async def human_click(page, selector: str):
    await human_delay(0.5, 1.5)
    await page.locator(selector).click()


async def close_page_quietly(page):
    if page is None or not hasattr(page, "close"):
        return
    try:
        await page.close()
    except Exception:
        pass


async def new_stealth_page(context):
    page = await context.new_page()
    stealth = Stealth()
    await stealth.apply_stealth_async(page)
    return page


async def is_selector_visible(page, selector: str) -> bool:
    try:
        locator = page.locator(selector)
        return await locator.count() > 0 and await locator.first.is_visible(timeout=1000)
    except Exception:
        return False


async def find_visible_selector(page, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        if await is_selector_visible(page, selector):
            return selector
    return None


async def retry_rate_limit_error_page(page) -> bool:
    message_selector = await find_visible_selector(page, RATE_LIMIT_MESSAGE_SELECTORS)
    retry_selector = await find_visible_selector(page, RATE_LIMIT_RETRY_BUTTON_SELECTORS)
    if not message_selector or not retry_selector:
        return False

    print("[OpenAI] Rate limit page detected. Clicking retry...")
    await human_click(page, retry_selector)
    await human_delay(1, 2)
    return True


async def wait_for_selector_with_rate_limit_retry(page, selector: str, timeout: int = 15000):
    retried_rate_limit = await retry_rate_limit_error_page(page)

    try:
        return await page.wait_for_selector(selector, timeout=timeout)
    except Exception:
        if not retried_rate_limit:
            retried_rate_limit = await retry_rate_limit_error_page(page)
        if not retried_rate_limit:
            raise
        return await page.wait_for_selector(selector, timeout=timeout)


async def wait_for_callback_url(page, timeout_s: float = 15.0, poll_interval_s: float = 0.2) -> str:
    expected_callback_url = get_expected_callback_url()
    deadline = time.monotonic() + timeout_s

    while time.monotonic() <= deadline:
        if page.url.startswith(expected_callback_url):
            return page.url
        await asyncio.sleep(poll_interval_s)

    raise RuntimeError(f"OAuth callback was not reached within {timeout_s:.1f}s")


async def execute_stage_with_retry(stage_name: str, operation, max_attempts: int = MAX_STAGE_ATTEMPTS) -> StageExecutionResult:
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        try:
            await operation()
            return StageExecutionResult(status="success", attempts=attempt)
        except Exception as exc:
            last_error = str(exc)
            print(f"[{stage_name}] Attempt {attempt}/{max_attempts} failed: {last_error}")

    return StageExecutionResult(status="failed", attempts=max_attempts, error=last_error)


async def verify_registration_complete(context, email: str):
    page = await new_stealth_page(context)
    try:
        print("[OpenAI] Verifying registration on a fresh OAuth page...")
        await page.goto(OPENAI_OAUTH_URL, wait_until="domcontentloaded")
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
) -> str:
    attempted_codes: set[str] = set()

    for attempt in range(max_attempts):
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


# === Phase 1: Registration ===
async def openai_register(page, email: str, password: str, access_token: str):
    """Register on OpenAI with the supplied credentials."""
    print("[OpenAI] Using password from account file.")

    # 1. Go to OAuth page
    print("[OpenAI] Navigating to OAuth page...")
    await page.goto(OPENAI_OAUTH_URL, wait_until="domcontentloaded")
    await human_delay(2, 4)

    # 2. Click "Sign up"
    print("[OpenAI] Clicking sign up link...")
    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_SIGNUP_LINK, timeout=15000)
    await human_click(page, CSS_OA_SIGNUP_LINK)
    await human_delay(2, 4)

    # 3. Enter email
    print(f"[OpenAI] Entering email: {email}")
    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_EMAIL_INPUT, timeout=15000)
    await human_type(page, CSS_OA_EMAIL_INPUT, email)
    await human_click(page, CSS_OA_CONTINUE_BTN)
    await human_delay(2, 4)

    # 4. Enter password (registration continues)
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

    # 4.5 Check: account already exists after submitting password?
    try:
        error_el = page.locator(CSS_OA_ACCOUNT_EXISTS_ERROR)
        if await error_el.count() > 0 and await error_el.first.is_visible():
            print("[OpenAI] Account already exists! Switching to login flow...")
            await page.goto("https://auth.openai.com/log-in", wait_until="domcontentloaded")
            await human_delay(2, 4)
            await openai_login_flow(page, email, password, access_token)
            return
    except Exception:
        pass

    # 5. Enter verification code
    print("[OpenAI] Waiting for code input...")
    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_CODE_INPUT, timeout=15000)
    await submit_verification_code_with_retry(page, CSS_OA_CODE_INPUT, access_token)

    # 6. Confirm age
    local = email.split("@")[0]
    name = re.sub(r'\d+', '', local)
    year = str(random.randint(1990, 1999))
    print(f"[OpenAI] Confirming age. Name: {name}, Year: {year}")

    try:
        await wait_for_selector_with_rate_limit_retry(page, CSS_OA_NAME_INPUT, timeout=15000)
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
    await human_type(page, CSS_OA_NAME_INPUT, name)
    await human_delay(0.5, 1.0)

    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_BIRTHDAY_YEAR, timeout=15000)
    year_el = page.locator(CSS_OA_BIRTHDAY_YEAR)
    await year_el.click()
    await human_delay(0.3, 0.6)
    await year_el.press("Control+a")
    await human_delay(0.2, 0.4)
    await year_el.press_sequentially(year, delay=random.randint(80, 150))
    await human_delay(0.5, 1.0)

    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_CREATE_ACCOUNT_BTN, timeout=10000)
    await human_click(page, CSS_OA_CREATE_ACCOUNT_BTN)
    await human_delay(3, 5)
    print("[OpenAI] Registration phase complete.")


# === Login flow (when account already exists) ===
async def openai_login_flow(page, email: str, password: str, access_token: str):
    """Login to existing OpenAI account. Handles different branches after password submit."""
    print("[Login] Using password from account file.")

    # 1. Enter email
    await wait_for_selector_with_rate_limit_retry(page, 'input[type="email"][name="email"]', timeout=15000)
    await human_type(page, 'input[type="email"][name="email"]', email)
    await human_click(page, 'form button[type="submit"][name="intent"]')
    await human_delay(2, 4)

    # 2. Enter password
    print("[Login] Entering password...")
    await wait_for_selector_with_rate_limit_retry(page, 'input[name="current-password"]', timeout=15000)
    await human_type(page, 'input[name="current-password"]', password)
    await human_click(page, 'form:has(input[name="current-password"]) button[type="submit"]')
    await human_delay(2, 4)

    # 3. Branch after password submit: could be code page OR name/birthday page
    print("[Login] Detecting next page state...")
    local = email.split("@")[0]
    name = re.sub(r'\d+', '', local)
    year = str(random.randint(1990, 1999))
    completed = False

    for _ in range(3):
        code_el = page.locator('input[name="code"]')
        name_el = page.locator('input[name="name"]')
        birthday_el = page.locator('[data-type="year"]')

        # Case 1: verification code page
        try:
            if await code_el.count() > 0 and await code_el.first.is_visible(timeout=3000):
                print("[Login] Code page found.")
                await submit_verification_code_with_retry(page, CSS_L_CODE, access_token)
                continue
        except Exception:
            pass

        # Case 2: name/birthday page
        try:
            if (await name_el.count() > 0 and await name_el.first.is_visible(timeout=3000)) or \
               (await birthday_el.count() > 0 and await birthday_el.first.is_visible(timeout=3000)):
                print(f"[Login] Birthday page found, name: {name}, year: {year}")

                if await name_el.count() > 0:
                    await name_el.first.fill("")
                    await human_type(page, 'input[name="name"]', name)
                    await human_delay(0.5, 1.0)

                if await birthday_el.count() > 0:
                    await birthday_el.first.click()
                    await human_delay(0.3, 0.6)
                    await birthday_el.first.press("Control+a")
                    await human_delay(0.2, 0.4)
                    await birthday_el.first.press_sequentially(year, delay=random.randint(80, 150))
                    await human_delay(0.5, 1.0)

                submit_btn = page.locator('button[type="submit"]:has-text("完成帐户创建"), button[type="submit"]:has-text("继续")')
                if await submit_btn.count() > 0:
                    await human_click(page, 'button[type="submit"]:has-text("完成帐户创建"), button[type="submit"]:has-text("继续")')
                await human_delay(3, 5)
                completed = True
                break
        except Exception:
            pass

        if page.url.startswith(get_expected_callback_url()) or await is_selector_visible(page, CSS_L_CONSENT_BTN):
            completed = True
            break

        if await retry_rate_limit_error_page(page):
            continue

        # Nothing matched yet, wait a bit
        await human_delay(2, 3)

    if not completed:
        raise RuntimeError("Login flow did not reach a confirmed completion state")

    print("[Login] Login flow complete.")


# === Phase 2: Second OAuth login + consent ===
async def openai_second_login(page, email: str, password: str, access_token: str):
    """After registration, re-visit OAuth URL to login and handle consent."""
    print("[OpenAI] Phase 2: Re-visiting OAuth URL to login...")

    await page.goto(OPENAI_OAUTH_URL, wait_until="domcontentloaded")
    await human_delay(2, 4)

    # 1. Enter email
    print(f"[OpenAI] Entering email: {email}")
    await wait_for_selector_with_rate_limit_retry(page, CSS_L_EMAIL, timeout=15000)
    await human_type(page, CSS_L_EMAIL, email)
    await human_click(page, CSS_L_CONTINUE_EMAIL)
    await human_delay(2, 4)

    # 2. Enter password
    print("[OpenAI] Entering password...")
    await wait_for_selector_with_rate_limit_retry(page, CSS_L_PASSWORD, timeout=15000)
    await human_type(page, CSS_L_PASSWORD, password)
    await human_click(page, CSS_L_CONTINUE_PWD)
    await human_delay(2, 4)

    # 3. Enter verification code (new code sent)
    print("[OpenAI] Waiting for verification code input...")
    await wait_for_selector_with_rate_limit_retry(page, CSS_L_CODE, timeout=15000)
    await submit_verification_code_with_retry(
        page,
        CSS_L_CODE,
        access_token,
        submit_mode="click",
        submit_selector=CSS_L_CONTINUE_CODE,
    )

    # 4. Consent page: "使用 ChatGPT 登录到 Codex" → click 继续
    print("[OpenAI] Waiting for consent page...")
    await wait_for_selector_with_rate_limit_retry(page, CSS_L_CONSENT_BTN, timeout=15000)
    await human_click(page, CSS_L_CONSENT_BTN)
    callback_url = await wait_for_callback_url(page)

    print(f"[OpenAI] Consent submitted. Final URL: {callback_url}")
    return callback_url


# --- Main ---
async def run(email: str, password: str, refresh_token: str, client_id: str):
    password = normalize_password(password)
    try:
        access_token = await exchange_refresh_token(refresh_token, client_id)
    except Exception as exc:
        return AccountExecutionResult(
            email=email,
            registration_status="skipped",
            registration_attempts=0,
            login_status="skipped",
            login_attempts=0,
            overall_status="failed",
            error=str(exc),
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                '--incognito',
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
            ],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        try:
            async def registration_operation():
                page = await new_stealth_page(context)
                try:
                    await openai_register(page, email, password, access_token)
                    await verify_registration_complete(context, email)
                finally:
                    await close_page_quietly(page)

            registration_result = await execute_stage_with_retry("registration", registration_operation)
            if registration_result.status != "success":
                return AccountExecutionResult(
                    email=email,
                    registration_status="failed",
                    registration_attempts=registration_result.attempts,
                    login_status="skipped",
                    login_attempts=0,
                    overall_status="failed",
                    error=registration_result.error,
                )

            print("[Main] Registration done.")

            async def login_operation():
                print("[Main] Starting second OAuth pass...")
                page = await new_stealth_page(context)
                try:
                    await openai_second_login(page, email, password, access_token)
                finally:
                    await close_page_quietly(page)

            login_result = await execute_stage_with_retry("login", login_operation)
            overall_status = "success" if login_result.status == "success" else "failed"
            error = login_result.error if login_result.status != "success" else ""
            if overall_status == "success":
                print("[Main] All done!")

            return AccountExecutionResult(
                email=email,
                registration_status="success",
                registration_attempts=registration_result.attempts,
                login_status=login_result.status,
                login_attempts=login_result.attempts,
                overall_status=overall_status,
                error=error,
            )
        finally:
            await browser.close()


async def run_accounts(accounts_file: Path):
    accounts = load_accounts(accounts_file)
    csv_path = build_results_csv_path()
    print(f"[Main] Loaded {len(accounts)} account(s) from {accounts_file}")
    print(f"[Main] Writing execution results to {csv_path}")
    for index, account in enumerate(accounts, start=1):
        print(f"[Main] Processing account {index}/{len(accounts)}: {account.email}")
        result = await run(
            email=account.email,
            password=account.password,
            refresh_token=account.refresh_token,
            client_id=account.client_id,
        )
        append_account_result(csv_path, result)

    return csv_path


def main():
    asyncio.run(run_accounts(ACCOUNT_FILE))


if __name__ == "__main__":
    main()
