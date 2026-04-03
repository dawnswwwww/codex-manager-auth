from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import main


class _NeverVisibleLocator:
    def __init__(self):
        self.first = self

    async def count(self):
        return 0

    async def is_visible(self, timeout=None):
        return False

    async def fill(self, value):
        return None

    async def click(self):
        return None

    async def press(self, key):
        return None

    async def press_sequentially(self, text, delay=None):
        return None


class _LoginFlowPage:
    url = "https://auth.openai.com/log-in"

    async def wait_for_selector(self, selector, timeout=None):
        return None

    def locator(self, selector):
        return _NeverVisibleLocator()


class _StaticUrlPage:
    def __init__(self, url):
        self.url = url


class _HardFailurePage:
    def __init__(self, url="https://auth.openai.com/add-phone"):
        self.url = url

    def locator(self, selector):
        return _NeverVisibleLocator()

    async def wait_for_selector(self, selector, timeout=None):
        raise RuntimeError("timeout waiting for selector")


class _ProfileLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.first = self

    async def count(self):
        return 1 if self.page.visible.get(self.selector, False) else 0

    async def is_visible(self, timeout=None):
        return self.page.visible.get(self.selector, False)

    async def fill(self, value):
        self.page.fills.append((self.selector, value))

    async def click(self):
        self.page.clicks.append(self.selector)

    async def press(self, key):
        self.page.presses.append((self.selector, key))

    async def press_sequentially(self, text, delay=None):
        self.page.typed.append((self.selector, text))


class _ProfilePage:
    def __init__(self, visible):
        self.visible = visible
        self.fills = []
        self.clicks = []
        self.presses = []
        self.typed = []

    def locator(self, selector):
        return _ProfileLocator(self, selector)

    async def wait_for_selector(self, selector, timeout=None):
        if self.visible.get(selector, False):
            return object()
        raise RuntimeError(f"selector not visible: {selector}")


class _RateLimitLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.first = self

    async def count(self):
        return 1 if self.page.has_selector(self.selector) else 0

    async def is_visible(self, timeout=None):
        return self.page.has_selector(self.selector)

    async def click(self):
        self.page.retry_clicks += 1
        self.page.rate_limit_visible = False


class _RateLimitPage:
    def __init__(self):
        self.rate_limit_visible = True
        self.retry_clicks = 0
        self.wait_attempts = 0

    def has_selector(self, selector):
        if selector in main.RATE_LIMIT_MESSAGE_SELECTORS:
            return self.rate_limit_visible
        if selector in main.RATE_LIMIT_RETRY_BUTTON_SELECTORS:
            return self.rate_limit_visible
        return False

    def locator(self, selector):
        return _RateLimitLocator(self, selector)

    async def wait_for_selector(self, selector, timeout=None):
        self.wait_attempts += 1
        if self.wait_attempts == 1:
            raise RuntimeError("timeout waiting for selector")
        return object()


class _FakeOAuthClient:
    def __init__(self):
        self.client_id = "client-123"
        self.redirect_port = 2456
        self.redirect_uri = "http://localhost:2456/auth/callback"
        self.session = object()
        self.exchange_token_and_save = AsyncMock(return_value={"account_id": "acct-123"})

    def create_session(self):
        return self.session

    def build_auth_url(self, session):
        assert session is self.session
        return "https://auth.openai.com/oauth/authorize?state=session"

    def extract_callback_params(self, callback_url, session):
        assert session is self.session
        if not isinstance(callback_url, str):
            return None
        assert callback_url.startswith(self.redirect_uri)
        return {"code": "auth-code", "state": "session"}


class ExecutionReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_skips_registration_verification_when_account_already_exists(self):
        class _RunPage:
            pass

        class _RunContext:
            def __init__(self):
                self.new_page = AsyncMock(side_effect=[_RunPage(), _RunPage()])

        class _RunBrowser:
            def __init__(self, context):
                self.new_context = AsyncMock(return_value=context)
                self.close = AsyncMock()

        class _RunPlaywrightManager:
            def __init__(self, browser):
                self.chromium = type("Chromium", (), {"launch": AsyncMock(return_value=browser)})()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        context = _RunContext()
        browser = _RunBrowser(context)
        playwright_manager = _RunPlaywrightManager(browser)
        stealth = type("StealthStub", (), {"apply_stealth_async": AsyncMock()})()
        oauth_client = _FakeOAuthClient()

        with patch.object(main, "async_playwright", return_value=playwright_manager), patch.object(
            main,
            "exchange_refresh_token",
            AsyncMock(return_value="access-token"),
        ), patch.object(
            main,
            "openai_register",
            AsyncMock(
                return_value=main.RegistrationFlowOutcome(
                    registration_status="already_exists",
                    should_verify_registration=False,
                )
            ),
        ), patch.object(
            main,
            "verify_registration_complete",
            AsyncMock(),
        ) as verify_mock, patch.object(
            main,
            "openai_second_login",
            AsyncMock(return_value=f"{oauth_client.redirect_uri}?code=auth-code&state=session"),
        ) as login_mock, patch.object(main, "Stealth", return_value=stealth), patch.object(main, "OAUTH_CLIENT", oauth_client):
            result = await main.run("user@example.com", "Secret123", "refresh-token", "client-id")

        verify_mock.assert_not_awaited()
        login_mock.assert_awaited_once()
        self.assertEqual(result.registration_status, "already_exists")
        self.assertEqual(result.login_status, "success")

    async def test_run_passes_the_normalized_account_password_into_both_phases(self):
        class _RunPage:
            pass

        class _RunContext:
            def __init__(self):
                self.new_page = AsyncMock(side_effect=[_RunPage(), _RunPage()])

        class _RunBrowser:
            def __init__(self, context):
                self.new_context = AsyncMock(return_value=context)
                self.close = AsyncMock()

        class _RunPlaywrightManager:
            def __init__(self, browser):
                self.chromium = type("Chromium", (), {"launch": AsyncMock(return_value=browser)})()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        context = _RunContext()
        browser = _RunBrowser(context)
        playwright_manager = _RunPlaywrightManager(browser)
        stealth = type("StealthStub", (), {"apply_stealth_async": AsyncMock()})()
        oauth_client = _FakeOAuthClient()

        with patch.object(main, "async_playwright", return_value=playwright_manager), patch.object(
            main,
            "exchange_refresh_token",
            AsyncMock(return_value="access-token"),
        ), patch.object(main, "openai_register", AsyncMock()) as register_mock, patch.object(
            main,
            "verify_registration_complete",
            AsyncMock(),
        ), patch.object(
            main,
            "openai_second_login",
            AsyncMock(return_value=f"{oauth_client.redirect_uri}?code=auth-code&state=session"),
        ) as login_mock, patch.object(main, "Stealth", return_value=stealth), patch.object(main, "OAUTH_CLIENT", oauth_client):
            await main.run("user@example.com", "Secret123", "refresh-token", "client-id")

        self.assertEqual(register_mock.await_args.args[2], "Secret123000")
        self.assertEqual(login_mock.await_args.args[2], "Secret123000")

    async def test_run_login_stage_exchanges_token_after_successful_callback(self):
        class _RunPage:
            pass

        class _RunContext:
            def __init__(self):
                self.new_page = AsyncMock(return_value=_RunPage())

        class _RunBrowser:
            def __init__(self, context):
                self.new_context = AsyncMock(return_value=context)
                self.close = AsyncMock()

        class _RunPlaywrightManager:
            def __init__(self, browser):
                self.chromium = type("Chromium", (), {"launch": AsyncMock(return_value=browser)})()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        context = _RunContext()
        browser = _RunBrowser(context)
        playwright_manager = _RunPlaywrightManager(browser)
        stealth = type("StealthStub", (), {"apply_stealth_async": AsyncMock()})()
        oauth_client = _FakeOAuthClient()
        registration_result = main.AccountExecutionResult(
            email="user@example.com",
            password="Secret123000",
            registration_status="success",
            login_status="pending",
            error_reason="",
            registration_attempts=1,
        )
        account = main.AccountRecord(
            email="user@example.com",
            password="Secret123",
            client_id="client-123",
            refresh_token="refresh-token",
        )

        with patch.object(main, "async_playwright", return_value=playwright_manager), patch.object(
            main,
            "exchange_refresh_token",
            AsyncMock(return_value="access-token"),
        ), patch.object(
            main,
            "openai_second_login",
            AsyncMock(return_value=f"{oauth_client.redirect_uri}?code=auth-code&state=session"),
        ), patch.object(main, "Stealth", return_value=stealth), patch.object(main, "OAUTH_CLIENT", oauth_client):
            result = await main.run_login_stage(account, registration_result)

        oauth_client.exchange_token_and_save.assert_awaited_once_with("auth-code", "user@example.com", oauth_client.session)
        self.assertEqual(result.login_status, "success")

    async def test_openai_login_flow_raises_when_no_expected_followup_state_is_detected(self):
        page = _LoginFlowPage()

        with patch.object(main, "human_type", AsyncMock()), patch.object(
            main,
            "human_click",
            AsyncMock(),
        ), patch.object(main, "human_delay", AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "Login flow"):
                await main.openai_login_flow(page, "user@example.com", "Secret123", "token")

    async def test_execute_stage_with_retry_retries_until_stage_succeeds(self):
        operation = AsyncMock(side_effect=[RuntimeError("first"), None])

        result = await main.execute_stage_with_retry("registration", operation, max_attempts=3)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(operation.await_count, 2)

    async def test_execute_stage_with_retry_stops_immediately_for_non_retryable_errors(self):
        operation = AsyncMock(side_effect=main.NonRetryableStageError("phone required"))

        result = await main.execute_stage_with_retry("registration", operation, max_attempts=3)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.error, "phone required")
        self.assertEqual(operation.await_count, 1)

    async def test_wait_for_callback_url_raises_when_redirect_never_arrives(self):
        page = _StaticUrlPage("https://auth.openai.com/authorize")

        with self.assertRaisesRegex(RuntimeError, "callback"):
            await main.wait_for_callback_url(page, timeout_s=0.01, poll_interval_s=0.0)

    async def test_wait_for_callback_url_raises_immediately_for_hard_failure_pages(self):
        page = _HardFailurePage()

        with self.assertRaisesRegex(main.NonRetryableStageError, "phone number"):
            await main.wait_for_callback_url(page, timeout_s=0.01, poll_interval_s=0.0)

    async def test_wait_for_selector_with_rate_limit_retry_clicks_retry_and_retries(self):
        page = _RateLimitPage()

        with patch.object(main, "human_delay", AsyncMock()):
            await main.wait_for_selector_with_rate_limit_retry(page, "input[name='email']", timeout=1)

        self.assertEqual(page.retry_clicks, 1)
        self.assertEqual(page.wait_attempts, 2)

    async def test_wait_for_selector_with_rate_limit_retry_raises_for_hard_failure_pages(self):
        page = _HardFailurePage()

        with self.assertRaisesRegex(main.NonRetryableStageError, "phone number"):
            await main.wait_for_selector_with_rate_limit_retry(page, "input[name='email']", timeout=1)

    async def test_get_login_terminal_state_detects_callback_and_hard_failure_pages(self):
        callback_page = _StaticUrlPage(f"{main.get_expected_callback_url()}?code=ok")
        hard_failure_page = _HardFailurePage()

        callback_state = await main.get_login_terminal_state(callback_page)
        hard_failure_state = await main.get_login_terminal_state(hard_failure_page)

        self.assertEqual(callback_state.status, "callback")
        self.assertIn("code=ok", callback_state.detail)
        self.assertEqual(hard_failure_state.status, "hard_failure")
        self.assertIn("phone number", hard_failure_state.detail)

    async def test_fill_profile_age_uses_age_input_when_year_input_is_missing(self):
        page = _ProfilePage(
            {
                main.CSS_OA_NAME_INPUT: True,
                main.CSS_OA_AGE_INPUT_SELECTORS[0]: True,
                main.CSS_OA_BIRTHDAY_YEAR: False,
            }
        )

        with patch.object(main, "human_delay", AsyncMock()):
            await main.fill_profile_age(page, "CroffMost", "32", "1994")

        self.assertEqual(
            page.fills,
            [
                (main.CSS_OA_NAME_INPUT, ""),
                (main.CSS_OA_AGE_INPUT_SELECTORS[0], ""),
            ],
        )
        self.assertIn((main.CSS_OA_NAME_INPUT, "CroffMost"), page.typed)
        self.assertIn((main.CSS_OA_AGE_INPUT_SELECTORS[0], "32"), page.typed)


class CsvResultTests(unittest.TestCase):
    def test_upsert_account_result_rejects_non_result_objects(self):
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "results.csv"
            with self.assertRaisesRegex(TypeError, "AccountExecutionResult"):
                main.upsert_account_result(csv_path, object())

    def test_upsert_account_result_writes_header_and_row(self):
        result = main.AccountExecutionResult(
            email="user@example.com",
            password="Secret123000",
            registration_status="success",
            login_status="failed",
            error_reason="consent callback not reached\nCall log:\n  - waiting for selector",
        )

        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "results.csv"
            main.upsert_account_result(csv_path, result)

            content = csv_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(
            content,
            [
                "email,password,registration_status,login_status,error_reason",
                "user@example.com,Secret123000,success,failed,consent callback not reached | Call log: | - waiting for selector",
            ],
        )

    def test_upsert_account_result_rewrites_existing_row_instead_of_appending_duplicate(self):
        original = main.AccountExecutionResult(
            email="user@example.com",
            password="Secret123000",
            registration_status="success",
            login_status="pending",
            error_reason="",
        )
        updated = main.AccountExecutionResult(
            email="user@example.com",
            password="Secret123000",
            registration_status="success",
            login_status="success",
            error_reason="",
        )

        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "results.csv"
            main.upsert_account_result(csv_path, original)
            main.upsert_account_result(csv_path, updated)

            content = csv_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(
            content,
            [
                "email,password,registration_status,login_status,error_reason",
                "user@example.com,Secret123000,success,success,",
            ],
        )
