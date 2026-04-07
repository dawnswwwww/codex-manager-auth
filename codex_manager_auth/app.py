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
    CSS_INVALID_PASSWORD_ERROR,
    CSS_L_CODE,
    CSS_L_CONSENT_BTN,
    CSS_L_CONTINUE_CODE,
    CSS_L_CONTINUE_EMAIL,
    CSS_L_CONTINUE_PWD,
    CSS_L_EMAIL,
    CSS_L_PASSWORDLESS_LOGIN_BTN,
    CSS_L_PASSWORD,
    CSS_OA_ACCOUNT_EXISTS_ERROR,
    CSS_OA_AGE_INPUT_SELECTORS,
    CSS_OA_BIRTHDAY_HIDDEN_INPUT,
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
from .microsoft_oauth import exchange_refresh_token
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
from .runner import (
    ACCOUNT_FILE,
    LOGIN_ELIGIBLE_REGISTRATION_STATUSES,
    MAX_STAGE_ATTEMPTS,
    OAUTH_CLIENT,
    get_expected_callback_url,
    main,
    normalize_registration_flow_outcome,
    run,
    run_accounts,
    run_accounts_full_chain,
    run_login_stage,
    run_registration_stage,
    should_attempt_login,
)


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
