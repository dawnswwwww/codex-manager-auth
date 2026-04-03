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


class ExecutionReportingTests(unittest.IsolatedAsyncioTestCase):
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
            AsyncMock(),
        ) as login_mock, patch.object(main, "Stealth", return_value=stealth):
            await main.run("user@example.com", "Secret123", "refresh-token", "client-id")

        self.assertEqual(register_mock.await_args.args[2], "Secret123000")
        self.assertEqual(login_mock.await_args.args[2], "Secret123000")

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

    async def test_wait_for_callback_url_raises_when_redirect_never_arrives(self):
        page = _StaticUrlPage("https://auth.openai.com/authorize")

        with self.assertRaisesRegex(RuntimeError, "callback"):
            await main.wait_for_callback_url(page, timeout_s=0.01, poll_interval_s=0.0)

    async def test_wait_for_selector_with_rate_limit_retry_clicks_retry_and_retries(self):
        page = _RateLimitPage()

        with patch.object(main, "human_delay", AsyncMock()):
            await main.wait_for_selector_with_rate_limit_retry(page, "input[name='email']", timeout=1)

        self.assertEqual(page.retry_clicks, 1)
        self.assertEqual(page.wait_attempts, 2)


class CsvResultTests(unittest.TestCase):
    def test_append_account_result_rejects_non_result_objects(self):
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "results.csv"
            with self.assertRaisesRegex(TypeError, "AccountExecutionResult"):
                main.append_account_result(csv_path, object())

    def test_append_account_result_writes_header_and_row(self):
        result = main.AccountExecutionResult(
            email="user@example.com",
            registration_status="success",
            registration_attempts=2,
            login_status="failed",
            login_attempts=3,
            overall_status="failed",
            error="consent callback not reached\nCall log:\n  - waiting for selector",
        )

        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "results.csv"
            main.append_account_result(csv_path, result)

            content = csv_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(
            content,
            [
                "email,registration_status,registration_attempts,login_status,login_attempts,overall_status,error",
                "user@example.com,success,2,failed,3,failed,consent callback not reached | Call log: | - waiting for selector",
            ],
        )
