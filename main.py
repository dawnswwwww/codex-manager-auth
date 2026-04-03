import asyncio
import random
import re
import sys

import httpx
from playwright.async_api import async_playwright


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


async def fetch_verification_code(access_token: str, max_retries: int = 10, interval: int = 5) -> str:
    """Poll Outlook inbox for ChatGPT verification code."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(base_url=OUTLOOK_BASE, headers=headers, timeout=30) as client:
        for attempt in range(max_retries):
            print(f"[Outlook] Checking inbox (attempt {attempt + 1}/{max_retries})...")
            try:
                resp = await client.get("/me/messages?$top=5&$orderby=ReceivedDateTime desc")
                if resp.status_code == 200:
                    for msg in resp.json().get("value", []):
                        subject = msg.get("Subject", "")
                        body = msg.get("BodyPreview", "")
                        match = re.search(r'代码为\s*(\d{6})', subject)
                        if not match:
                            match = re.search(r'code\s*(?:is\s*)?(\d{6})', subject, re.IGNORECASE)
                        if not match:
                            match = re.search(r'(?:代码为|code(?:\s*is)?\s*[:：]?\s*)(\d{6})', body, re.IGNORECASE)
                        if match:
                            code = match.group(1)
                            print(f"[Outlook] Found code: {code}")
                            return code
            except Exception as e:
                print(f"[Outlook] Error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(interval)
    raise RuntimeError("Failed to find verification code after max retries")


# --- OpenAI OAuth URL ---
OPENAI_OAUTH_URL = (
    "https://auth.openai.com/oauth/authorize"
    "?response_type=code"
    "&client_id=app_EMoamEEZ73f0CkXaXp7hrann"
    "&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback"
    "&scope=openid%20profile%20email%20offline_access%20api.connectors.read%20api.connectors.invoke"
    "&code_challenge=eIqXnpzcbqDoJMEQ0wfICGkVjCN3xImfz1qsLTygoSE"
    "&code_challenge_method=S256"
    "&id_token_add_organizations=true"
    "&codex_cli_simplified_flow=true"
    "&state=-Xfx2Gr4g51W52DY1dR1Rqrx6SawslWkl-9kJ-tWMDI"
    "&originator=codex_cli_rs"
)

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

# --- Phase 2: Login selectors (second OAuth pass) ---
CSS_L_EMAIL = 'input[type="email"][name="email"]'
CSS_L_CONTINUE_EMAIL = 'form button[type="submit"][name="intent"]'
CSS_L_PASSWORD = 'input[name="current-password"]'
CSS_L_CONTINUE_PWD = 'form:has(input[name="current-password"]) button[type="submit"]'
CSS_L_CODE = 'input[name="code"]'
CSS_L_CONTINUE_CODE = 'button[name="intent"][value="validate"]'
CSS_L_CONSENT_BTN = 'button[type="submit"]:has-text("继续")'


# --- Human-like helpers ---
async def human_delay(min_s: float = 0.5, max_s: float = 2.0):
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_type(page, selector: str, text: str):
    await page.locator(selector).press_sequentially(text, delay=random.randint(50, 150))
    await human_delay(0.3, 0.8)


async def human_click(page, selector: str):
    await human_delay(0.5, 1.5)
    await page.locator(selector).click()


def generate_password(email: str) -> str:
    """Take the part before @, pad with 0s to 12 chars minimum."""
    local = email.split("@")[0]
    if len(local) < 12:
        local = local + "0" * (12 - len(local))
    return local


# === Phase 1: Registration ===
async def openai_register(page, email: str, access_token: str):
    """Register on OpenAI. Returns password."""
    password = generate_password(email)
    print(f"[OpenAI] Generated password: {password}")

    # 1. Go to OAuth page
    print("[OpenAI] Navigating to OAuth page...")
    await page.goto(OPENAI_OAUTH_URL, wait_until="domcontentloaded")
    await human_delay(2, 4)

    # 2. Click "Sign up"
    print("[OpenAI] Clicking sign up link...")
    await page.wait_for_selector(CSS_OA_SIGNUP_LINK, timeout=15000)
    await human_click(page, CSS_OA_SIGNUP_LINK)
    await human_delay(2, 4)

    # 3. Enter email
    print(f"[OpenAI] Entering email: {email}")
    await page.wait_for_selector(CSS_OA_EMAIL_INPUT, timeout=15000)
    await human_type(page, CSS_OA_EMAIL_INPUT, email)
    await human_click(page, CSS_OA_CONTINUE_BTN)
    await human_delay(2, 4)

    # 4. Enter password
    print("[OpenAI] Entering password...")
    await page.wait_for_selector(CSS_OA_PASSWORD_INPUT, timeout=15000)
    await human_type(page, CSS_OA_PASSWORD_INPUT, password)
    await human_click(page, CSS_OA_PASSWORD_BTN)
    await human_delay(2, 4)

    # 5. Enter verification code
    print("[OpenAI] Waiting for code input...")
    await page.wait_for_selector(CSS_OA_CODE_INPUT, timeout=15000)
    code = await fetch_verification_code(access_token)
    print(f"[OpenAI] Entering code: {code}")
    await human_type(page, CSS_OA_CODE_INPUT, code)
    await human_delay(0.5, 1.0)
    await page.locator(CSS_OA_CODE_INPUT).press("Enter")
    await human_delay(2, 4)

    # 6. Confirm age
    local = email.split("@")[0]
    name = re.sub(r'\d+', '', local)
    year = str(random.randint(1990, 1999))
    print(f"[OpenAI] Confirming age. Name: {name}, Year: {year}")

    await page.wait_for_selector(CSS_OA_NAME_INPUT, timeout=15000)
    await human_type(page, CSS_OA_NAME_INPUT, name)
    await human_delay(0.5, 1.0)

    await page.wait_for_selector(CSS_OA_BIRTHDAY_YEAR, timeout=15000)
    year_el = page.locator(CSS_OA_BIRTHDAY_YEAR)
    await year_el.click()
    await human_delay(0.3, 0.6)
    await year_el.press("Control+a")
    await human_delay(0.2, 0.4)
    await year_el.press_sequentially(year, delay=random.randint(80, 150))
    await human_delay(0.5, 1.0)

    await page.wait_for_selector(CSS_OA_CREATE_ACCOUNT_BTN, timeout=10000)
    await human_click(page, CSS_OA_CREATE_ACCOUNT_BTN)
    await human_delay(3, 5)
    print("[OpenAI] Registration phase complete.")

    return password


# === Phase 2: Second OAuth login + consent ===
async def openai_second_login(page, email: str, access_token: str):
    """After registration, re-visit OAuth URL to login and handle consent."""
    password = generate_password(email)
    print("[OpenAI] Phase 2: Re-visiting OAuth URL to login...")

    await page.goto(OPENAI_OAUTH_URL, wait_until="domcontentloaded")
    await human_delay(2, 4)

    # 1. Enter email
    print(f"[OpenAI] Entering email: {email}")
    await page.wait_for_selector(CSS_L_EMAIL, timeout=15000)
    await human_type(page, CSS_L_EMAIL, email)
    await human_click(page, CSS_L_CONTINUE_EMAIL)
    await human_delay(2, 4)

    # 2. Enter password
    print("[OpenAI] Entering password...")
    await page.wait_for_selector(CSS_L_PASSWORD, timeout=15000)
    await human_type(page, CSS_L_PASSWORD, password)
    await human_click(page, CSS_L_CONTINUE_PWD)
    await human_delay(2, 4)

    # 3. Enter verification code (new code sent)
    print("[OpenAI] Waiting for verification code input...")
    await page.wait_for_selector(CSS_L_CODE, timeout=15000)
    code = await fetch_verification_code(access_token)
    print(f"[OpenAI] Entering code: {code}")
    await human_type(page, CSS_L_CODE, code)
    await human_click(page, CSS_L_CONTINUE_CODE)
    await human_delay(2, 4)

    # 4. Consent page: "使用 ChatGPT 登录到 Codex" → click 继续
    print("[OpenAI] Waiting for consent page...")
    await page.wait_for_selector(CSS_L_CONSENT_BTN, timeout=15000)
    await human_click(page, CSS_L_CONSENT_BTN)
    await human_delay(3, 5)

    print(f"[OpenAI] Consent submitted. Final URL: {page.url}")


# --- Main ---
async def run(email: str, refresh_token: str, client_id: str):
    access_token = await exchange_refresh_token(refresh_token, client_id)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # === Phase 1: Registration ===
            password = await openai_register(page, email, access_token)
            print(f"[Main] Registration done. Password: {password}")

            # === Phase 2: Second OAuth login ===
            print("[Main] Starting second OAuth pass...")
            page2 = await context.new_page()
            await openai_second_login(page2, email, access_token)

            print(f"[Main] All done!")
            input("[Debug] Press Enter to close browser...")
        except KeyboardInterrupt:
            pass
        finally:
            await browser.close()


def main():
    if len(sys.argv) < 4:
        print("Usage: uv run python main.py <email> <refresh_token> <client_id>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1], sys.argv[2], sys.argv[3]))


if __name__ == "__main__":
    main()
