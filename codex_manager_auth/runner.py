import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from .accounts import iter_accounts, normalize_password
from .checkpoint import (
    build_checkpoint_csv_path,
    create_pending_account_result,
    find_account_result,
    upsert_account_result,
)
from .config import APP_CONFIG
from .models import AccountExecutionResult, AccountRecord, RegistrationFlowOutcome
from .openai_flows import execute_stage_with_retry, openai_register, openai_second_login, verify_registration_complete
from .openai_oauth import OpenAIOAuthClient
from .outlook_mail import exchange_refresh_token
from .playwright_helpers import close_page_quietly, launch_browser_and_context, new_stealth_page


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


def should_attempt_login(result: AccountExecutionResult) -> bool:
    return (
        result.registration_status in LOGIN_ELIGIBLE_REGISTRATION_STATUSES
        and result.login_status != "success"
    )


def is_disabled_account_result(result: AccountExecutionResult) -> bool:
    reason = (result.error_reason or "").lower()
    return "phone number" in reason or "add-phone" in reason


def is_remote_verification_block_result(result: AccountExecutionResult) -> bool:
    reason = (result.error_reason or "").lower()
    return (
        "max_check_attempts" in reason
        or "verification session hit" in reason
        or "account_deactivated" in reason
    )


def is_transient_auth_navigation_result(result: AccountExecutionResult) -> bool:
    reason = (result.error_reason or "").lower()
    return (
        "page.goto: net::err_connection_closed" in reason
        or ("page.goto:" in reason and "timeout" in reason)
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
                expected_callback_url = OAUTH_CLIENT.get_expected_callback_url()
                page = await new_stealth_page(context)
                try:
                    callback_url = await openai_second_login(
                        page,
                        account.email,
                        password,
                        access_token,
                        auth_url,
                        expected_callback_url,
                    )
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
    return await run_accounts_full_chain(accounts_file)


async def run_accounts_full_chain(accounts_file: Path):
    csv_path = build_checkpoint_csv_path(accounts_file)
    print(f"[Main] Streaming accounts from {accounts_file}")
    print(f"[Main] Using checkpoint CSV {csv_path}")

    accounts = list(iter_accounts(accounts_file))
    total_accounts = len(accounts)

    for index, account in enumerate(accounts, start=1):
        current_result = find_account_result(csv_path, account.email)
        if current_result is None:
            current_result = create_pending_account_result(account)
            upsert_account_result(csv_path, current_result)

        token_path = OAUTH_CLIENT.token_output_dir / OAUTH_CLIENT.build_token_filename(account.email)
        if token_path.exists():
            print(f"[Main] [{index}/{total_accounts}] Token already exists for {account.email}, skipping execution.")
            synced_result = AccountExecutionResult(
                email=account.email,
                password=normalize_password(account.password),
                registration_status=current_result.registration_status if current_result.registration_status != "pending" else "success",
                login_status="success",
                error_reason="",
                registration_attempts=current_result.registration_attempts,
                login_attempts=current_result.login_attempts,
                overall_status="success",
            )
            upsert_account_result(csv_path, synced_result)
            continue

        print(f"[Main] [{index}/{total_accounts}] Full-chain start: {account.email}")
        result = await run(
            email=account.email,
            password=account.password,
            refresh_token=account.refresh_token,
            client_id=account.client_id,
        )
        upsert_account_result(csv_path, result)
        token_exists = token_path.exists()
        print(
            f"[Main] [{index}/{total_accounts}] Full-chain result: "
            f"{account.email} reg={result.registration_status} "
            f"login={result.login_status} token={token_exists}"
        )
        if result.login_status != "success" or not token_exists:
            if is_disabled_account_result(result):
                print(f"[Main] [{index}/{total_accounts}] Disabled account detected for {account.email}, continuing.")
                continue
            if is_remote_verification_block_result(result):
                print(f"[Main] [{index}/{total_accounts}] Remote verification block detected for {account.email}, continuing.")
                continue
            if is_transient_auth_navigation_result(result):
                print(f"[Main] [{index}/{total_accounts}] Transient auth navigation failure detected for {account.email}, continuing.")
                continue
            print(f"[Main] Stopping after failure on {account.email}")
            break

    return csv_path


def main():
    asyncio.run(run_accounts_full_chain(ACCOUNT_FILE))
