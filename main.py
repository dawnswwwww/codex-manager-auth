import asyncio
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import random
import re
import tempfile
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
RESULT_CSV_HEADERS = (
    "email",
    "password",
    "registration_status",
    "login_status",
    "error_reason",
)
LOGIN_ELIGIBLE_REGISTRATION_STATUSES = {"success", "already_exists"}


@dataclass(frozen=True)
class AccountRecord:
    email: str
    password: str
    client_id: str
    refresh_token: str


@dataclass(frozen=True)
class RegistrationFlowOutcome:
    registration_status: str
    should_verify_registration: bool = True


@dataclass(frozen=True)
class StageExecutionResult:
    status: str
    attempts: int
    error: str = ""
    value: object | None = None


@dataclass(frozen=True)
class AccountExecutionResult:
    email: str
    password: str
    registration_status: str
    login_status: str
    error_reason: str = ""
    registration_attempts: int = 0
    login_attempts: int = 0
    overall_status: str = ""


def normalize_password(password: str) -> str:
    normalized = password.strip()
    if len(normalized) < 12:
        normalized = normalized + "0" * (12 - len(normalized))
    return normalized


def normalize_registration_flow_outcome(value) -> RegistrationFlowOutcome:
    if isinstance(value, RegistrationFlowOutcome):
        return value
    return RegistrationFlowOutcome(registration_status="success", should_verify_registration=True)


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


def iter_accounts(path: Path):
    seen_emails: set[str] = set()
    found_account = False
    with path.open("r", encoding="utf-8") as fh:
        for line_number, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            found_account = True
            account = parse_account_line(line, line_number)
            if account.email in seen_emails:
                raise ValueError(f"Duplicate account email in account file: {account.email}")
            seen_emails.add(account.email)
            yield account

    if not found_account:
        raise ValueError(f"No accounts found in {path}")


def load_accounts(path: Path) -> list[AccountRecord]:
    return list(iter_accounts(path))


def build_checkpoint_csv_path(accounts_file: Path) -> Path:
    return RESULTS_DIR / f"{accounts_file.stem}_checkpoint.csv"


def normalize_csv_field(value) -> str:
    text = str(value).replace("\r", "\n")
    parts = [part.strip() for part in text.split("\n") if part.strip()]
    return " | ".join(parts)


def create_pending_account_result(account: AccountRecord) -> AccountExecutionResult:
    return AccountExecutionResult(
        email=account.email,
        password=normalize_password(account.password),
        registration_status="pending",
        login_status="pending",
        error_reason="",
        overall_status="pending",
    )


def load_account_results(csv_path: Path) -> list[AccountExecutionResult]:
    if not csv_path.exists():
        return []

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        missing_headers = [header for header in RESULT_CSV_HEADERS if header not in fieldnames]
        if missing_headers:
            raise ValueError(f"Results CSV is missing headers: {', '.join(missing_headers)}")

        results: list[AccountExecutionResult] = []
        for row in reader:
            if not row:
                continue
            results.append(
                AccountExecutionResult(
                    email=(row.get("email") or "").strip(),
                    password=(row.get("password") or "").strip(),
                    registration_status=(row.get("registration_status") or "").strip(),
                    login_status=(row.get("login_status") or "").strip(),
                    error_reason=(row.get("error_reason") or "").strip(),
                )
            )
        return results


def write_account_results_atomic(csv_path: Path, results: list[AccountExecutionResult]):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=csv_path.parent,
        prefix=f"{csv_path.stem}_",
        suffix=".tmp",
        delete=False,
    ) as tmp_file:
        writer = csv.writer(tmp_file)
        writer.writerow(list(RESULT_CSV_HEADERS))
        for result in results:
            if not isinstance(result, AccountExecutionResult):
                raise TypeError(
                    f"write_account_results_atomic expects AccountExecutionResult, got {type(result).__name__}"
                )
            writer.writerow(
                [
                    normalize_csv_field(result.email),
                    normalize_csv_field(result.password),
                    normalize_csv_field(result.registration_status),
                    normalize_csv_field(result.login_status),
                    normalize_csv_field(result.error_reason),
                ]
            )
        tmp_path = Path(tmp_file.name)

    tmp_path.replace(csv_path)


def upsert_account_result(csv_path: Path, result: AccountExecutionResult):
    if not isinstance(result, AccountExecutionResult):
        raise TypeError(f"upsert_account_result expects AccountExecutionResult, got {type(result).__name__}")

    results = load_account_results(csv_path)
    updated = False
    for index, existing in enumerate(results):
        if existing.email == result.email:
            results[index] = result
            updated = True
            break
    if not updated:
        results.append(result)

    write_account_results_atomic(csv_path, results)


def find_account_result(csv_path: Path, email: str) -> AccountExecutionResult | None:
    for result in load_account_results(csv_path):
        if result.email == email:
            return result
    return None


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
CSS_OA_AGE_INPUT_SELECTORS = (
    'input[name="age"]',
    'input[aria-label="年龄"]',
    'input[placeholder*="年龄"]',
)
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
            value = await operation()
            return StageExecutionResult(status="success", attempts=attempt, value=value)
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


async def clear_and_type_locator(locator, text: str):
    await locator.click()
    await human_delay(0.3, 0.6)
    await locator.press("Control+a")
    await human_delay(0.2, 0.4)
    await locator.press_sequentially(text, delay=random.randint(80, 150))
    await human_delay(0.5, 1.0)


async def fill_profile_age(page, name: str, age_value: str, year_value: str):
    await wait_for_selector_with_rate_limit_retry(page, CSS_OA_NAME_INPUT, timeout=15000)
    name_el = page.locator(CSS_OA_NAME_INPUT)
    await name_el.fill("")
    await human_type(page, CSS_OA_NAME_INPUT, name)
    await human_delay(0.5, 1.0)

    if await is_selector_visible(page, CSS_OA_BIRTHDAY_YEAR):
        await clear_and_type_locator(page.locator(CSS_OA_BIRTHDAY_YEAR), year_value)
        return

    age_selector = await find_visible_selector(page, CSS_OA_AGE_INPUT_SELECTORS)
    if age_selector:
        age_el = page.locator(age_selector)
        await age_el.fill("")
        await clear_and_type_locator(age_el, age_value)
        return

    raise RuntimeError("Could not find a visible age/year input on the profile page")


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
async def openai_register(page, email: str, password: str, access_token: str) -> RegistrationFlowOutcome:
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
            print("[OpenAI] Account already exists. Skipping registration verification and handing off to login stage.")
            return RegistrationFlowOutcome(
                registration_status="already_exists",
                should_verify_registration=False,
            )
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
    age = str(max(18, datetime.now().year - int(year)))
    print(f"[OpenAI] Confirming age. Name: {name}, Age: {age}, Year: {year}")

    try:
        await fill_profile_age(page, name, age, year)
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
    age = str(max(18, datetime.now().year - int(year)))
    completed = False

    for _ in range(3):
        code_el = page.locator('input[name="code"]')
        name_el = page.locator('input[name="name"]')
        birthday_el = page.locator('[data-type="year"]')
        age_selector = await find_visible_selector(page, CSS_OA_AGE_INPUT_SELECTORS)

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
               (await birthday_el.count() > 0 and await birthday_el.first.is_visible(timeout=3000)) or \
               age_selector:
                print(f"[Login] Profile page found, name: {name}, age: {age}, year: {year}")
                await fill_profile_age(page, name, age, year)

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


async def launch_browser_and_context(playwright_manager):
    browser = await playwright_manager.chromium.launch(
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
    return browser, context


def should_attempt_login(result: AccountExecutionResult) -> bool:
    return (
        result.registration_status in LOGIN_ELIGIBLE_REGISTRATION_STATUSES
        and result.login_status != "success"
    )


async def run_registration_stage(account: AccountRecord) -> AccountExecutionResult:
    password = normalize_password(account.password)
    try:
        access_token = await exchange_refresh_token(account.refresh_token, account.client_id)
    except Exception as exc:
        return AccountExecutionResult(
            email=account.email,
            password=password,
            registration_status="failed",
            login_status="skipped",
            error_reason=str(exc),
            registration_attempts=0,
            login_attempts=0,
            overall_status="failed",
        )

    async with async_playwright() as p:
        browser, context = await launch_browser_and_context(p)
        try:
            async def registration_operation():
                page = await new_stealth_page(context)
                try:
                    registration_outcome = normalize_registration_flow_outcome(
                        await openai_register(page, account.email, password, access_token)
                    )
                    if registration_outcome.should_verify_registration:
                        await verify_registration_complete(context, account.email)
                    return registration_outcome
                finally:
                    await close_page_quietly(page)

            registration_result = await execute_stage_with_retry("registration", registration_operation)
        finally:
            await browser.close()

    if registration_result.status != "success":
        return AccountExecutionResult(
            email=account.email,
            password=password,
            registration_status="failed",
            login_status="skipped",
            error_reason=registration_result.error,
            registration_attempts=registration_result.attempts,
            login_attempts=0,
            overall_status="failed",
        )

    registration_outcome = normalize_registration_flow_outcome(registration_result.value)
    return AccountExecutionResult(
        email=account.email,
        password=password,
        registration_status=registration_outcome.registration_status,
        login_status="pending",
        error_reason="",
        registration_attempts=registration_result.attempts,
        login_attempts=0,
        overall_status="pending",
    )


async def run_login_stage(account: AccountRecord, registration_result: AccountExecutionResult) -> AccountExecutionResult:
    password = normalize_password(account.password)
    try:
        access_token = await exchange_refresh_token(account.refresh_token, account.client_id)
    except Exception as exc:
        return AccountExecutionResult(
            email=account.email,
            password=password,
            registration_status=registration_result.registration_status,
            login_status="failed",
            error_reason=str(exc),
            registration_attempts=registration_result.registration_attempts,
            login_attempts=0,
            overall_status="failed",
        )

    async with async_playwright() as p:
        browser, context = await launch_browser_and_context(p)
        try:
            async def login_operation():
                print("[Main] Starting second OAuth pass...")
                page = await new_stealth_page(context)
                try:
                    await openai_second_login(page, account.email, password, access_token)
                finally:
                    await close_page_quietly(page)

            login_result = await execute_stage_with_retry("login", login_operation)
        finally:
            await browser.close()

    overall_status = "success" if login_result.status == "success" else "failed"
    error = login_result.error if login_result.status != "success" else ""
    if overall_status == "success":
        print("[Main] All done!")

    return AccountExecutionResult(
        email=account.email,
        password=password,
        registration_status=registration_result.registration_status,
        login_status=login_result.status,
        error_reason=error,
        registration_attempts=registration_result.registration_attempts,
        login_attempts=login_result.attempts,
        overall_status=overall_status,
    )


# --- Main ---
async def run(email: str, password: str, refresh_token: str, client_id: str):
    account = AccountRecord(
        email=email,
        password=password,
        client_id=client_id,
        refresh_token=refresh_token,
    )
    registration_result = await run_registration_stage(account)
    if not should_attempt_login(registration_result):
        return registration_result

    print("[Main] Registration done.")
    return await run_login_stage(account, registration_result)


async def run_accounts(accounts_file: Path):
    csv_path = build_checkpoint_csv_path(accounts_file)
    print(f"[Main] Streaming accounts from {accounts_file}")
    print(f"[Main] Using checkpoint CSV {csv_path}")

    registration_count = 0
    for account in iter_accounts(accounts_file):
        registration_count += 1
        current_result = find_account_result(csv_path, account.email)
        if current_result is None:
            current_result = create_pending_account_result(account)
            upsert_account_result(csv_path, current_result)

        print(f"[Main] Registration phase account {registration_count}: {account.email}")
        if current_result.registration_status in LOGIN_ELIGIBLE_REGISTRATION_STATUSES:
            print(f"[Main] Registration already completed for {account.email}, skipping.")
            continue

        registration_result = await run_registration_stage(account)
        upsert_account_result(csv_path, registration_result)

    if registration_count == 0:
        raise ValueError(f"No accounts found in {accounts_file}")

    login_count = 0
    for account in iter_accounts(accounts_file):
        current_result = find_account_result(csv_path, account.email)
        if current_result is None:
            raise RuntimeError(f"Checkpoint row missing for account: {account.email}")
        if not should_attempt_login(current_result):
            continue

        login_count += 1
        print(f"[Main] Login phase account {login_count}: {account.email}")
        login_result = await run_login_stage(account, current_result)
        upsert_account_result(csv_path, login_result)

    return csv_path


def main():
    asyncio.run(run_accounts(ACCOUNT_FILE))


if __name__ == "__main__":
    main()
