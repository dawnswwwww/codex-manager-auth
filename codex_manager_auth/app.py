import asyncio
from pathlib import Path

from .accounts import (
    ACCOUNT_LINE_SEPARATOR,
    iter_accounts,
    load_accounts,
    normalize_password,
    parse_account_line,
)
from .checkpoint import (
    RESULT_CSV_HEADERS,
    RESULTS_DIR,
    build_checkpoint_csv_path,
    create_pending_account_result,
    find_account_result,
    load_account_results,
    normalize_csv_field,
    upsert_account_result,
    write_account_results_atomic,
)
from .config import APP_CONFIG
from .models import (
    AccountExecutionResult,
    AccountRecord,
    NonRetryableStageError,
    PageTerminalState,
    RegistrationFlowOutcome,
    StageExecutionResult,
)
from .openai_flows import (
    clear_and_type_locator,
    execute_stage_with_retry,
    fill_profile_age,
    get_hard_failure_reason,
    get_hard_failure_reason_from_url,
    get_login_terminal_state as _get_login_terminal_state,
    has_invalid_code_error,
    openai_login_flow as _openai_login_flow,
    openai_register as _openai_register,
    openai_second_login as _openai_second_login,
    raise_for_hard_failure_page,
    retry_rate_limit_error_page,
    submit_verification_code_with_retry,
    verify_registration_complete as _verify_registration_complete,
    wait_for_callback_url as _wait_for_callback_url,
    wait_for_selector_with_rate_limit_retry,
)
from .openai_oauth import OpenAIOAuthClient
from .openai_selectors import (
    ADD_PHONE_URL_KEYWORDS,
    CSS_INVALID_CODE_ERROR,
    CSS_L_CODE,
    CSS_L_CONSENT_BTN,
    CSS_L_CONTINUE_CODE,
    CSS_L_CONTINUE_EMAIL,
    CSS_L_CONTINUE_PWD,
    CSS_L_EMAIL,
    CSS_L_PASSWORD,
    CSS_OA_ACCOUNT_EXISTS_ERROR,
    CSS_OA_AGE_INPUT_SELECTORS,
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
from .outlook_mail import exchange_refresh_token, fetch_verification_code
from .playwright_helpers import (
    close_page_quietly,
    find_visible_selector,
    human_click,
    human_delay,
    human_type,
    is_selector_visible,
    launch_browser_and_context,
    new_stealth_page,
)
from playwright.async_api import async_playwright


ACCOUNT_FILE = APP_CONFIG.account_file
MAX_STAGE_ATTEMPTS = 3
LOGIN_ELIGIBLE_REGISTRATION_STATUSES = {"success", "already_exists"}
OAUTH_CLIENT = OpenAIOAuthClient(
    client_id=APP_CONFIG.oauth_client_id,
    redirect_port=APP_CONFIG.oauth_redirect_port,
    token_output_dir=APP_CONFIG.token_output_dir,
)


def normalize_registration_flow_outcome(value) -> RegistrationFlowOutcome:
    if isinstance(value, RegistrationFlowOutcome):
        return value
    return RegistrationFlowOutcome(registration_status="success", should_verify_registration=True)


def get_expected_callback_url() -> str:
    return OAUTH_CLIENT.get_expected_callback_url()


async def get_login_terminal_state(page):
    return await _get_login_terminal_state(page, get_expected_callback_url())


async def wait_for_callback_url(page, timeout_s: float = 15.0, poll_interval_s: float = 0.2) -> str:
    return await _wait_for_callback_url(
        page,
        get_expected_callback_url(),
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )


async def verify_registration_complete(context, email: str, auth_url: str):
    return await _verify_registration_complete(context, email, auth_url)


async def openai_register(page, email: str, password: str, access_token: str, auth_url: str):
    return await _openai_register(page, email, password, access_token, auth_url)


async def openai_login_flow(page, email: str, password: str, access_token: str):
    return await _openai_login_flow(page, email, password, access_token, get_expected_callback_url())


async def openai_second_login(page, email: str, password: str, access_token: str, auth_url: str):
    return await _openai_second_login(page, email, password, access_token, auth_url, get_expected_callback_url())


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
                auth_url = OAUTH_CLIENT.build_auth_url(OAUTH_CLIENT.create_session())
                page = await new_stealth_page(context)
                try:
                    registration_outcome = normalize_registration_flow_outcome(
                        await openai_register(page, account.email, password, access_token, auth_url)
                    )
                    if registration_outcome.should_verify_registration:
                        await verify_registration_complete(context, account.email, auth_url)
                    return registration_outcome
                finally:
                    await close_page_quietly(page)

            registration_result = await execute_stage_with_retry(
                "registration",
                registration_operation,
                max_attempts=MAX_STAGE_ATTEMPTS,
            )
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
                oauth_session = OAUTH_CLIENT.create_session()
                auth_url = OAUTH_CLIENT.build_auth_url(oauth_session)
                page = await new_stealth_page(context)
                try:
                    callback_url = await openai_second_login(page, account.email, password, access_token, auth_url)
                    return callback_url, oauth_session
                finally:
                    await close_page_quietly(page)

            login_result = await execute_stage_with_retry(
                "login",
                login_operation,
                max_attempts=MAX_STAGE_ATTEMPTS,
            )
        finally:
            await browser.close()

    overall_status = "success" if login_result.status == "success" else "failed"
    error = login_result.error if login_result.status != "success" else ""
    if login_result.status == "success":
        callback_url, oauth_session = login_result.value
        callback_params = OAUTH_CLIENT.extract_callback_params(callback_url, oauth_session)
        if not callback_params:
            overall_status = "failed"
            error = "Invalid OAuth callback parameters"
        elif callback_params.get("error"):
            overall_status = "failed"
            error = callback_params.get("error_description") or callback_params["error"]
        elif not callback_params.get("code"):
            overall_status = "failed"
            error = "OAuth callback did not include an authorization code"
        else:
            try:
                await OAUTH_CLIENT.exchange_token_and_save(callback_params["code"], account.email, oauth_session)
            except Exception as exc:
                overall_status = "failed"
                error = str(exc)
    if overall_status == "success":
        print("[Main] All done!")

    return AccountExecutionResult(
        email=account.email,
        password=password,
        registration_status=registration_result.registration_status,
        login_status="success" if overall_status == "success" else "failed",
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
