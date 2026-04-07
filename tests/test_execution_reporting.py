from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import main
from codex_manager_auth import openai_flows
from codex_manager_auth import playwright_helpers
from codex_manager_auth import runner as app_runner


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


class _VisibleSelectorPage:
    def __init__(self, url, visible=None):
        self.url = url
        self.visible = visible or {}

    def locator(self, selector):
        return _ProfileLocator(self, selector)

    async def wait_for_selector(self, selector, timeout=None):
        if self.visible.get(selector, False):
            return object()
        raise RuntimeError(f"selector not visible: {selector}")


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
        if self.selector in self.page.click_failures:
            raise RuntimeError(f"click failed for {self.selector}")
        self.page.clicks.append(self.selector)

    async def press(self, key):
        self.page.presses.append((self.selector, key))

    async def press_sequentially(self, text, delay=None):
        self.page.typed.append((self.selector, text))

    async def evaluate(self, script, value):
        self.page.evaluated.append((self.selector, value))

    async def focus(self):
        self.page.focused.append(self.selector)


class _ProfilePage:
    def __init__(self, visible, click_failures=None):
        self.visible = visible
        self.click_failures = set(click_failures or [])
        self.fills = []
        self.clicks = []
        self.focused = []
        self.presses = []
        self.typed = []
        self.evaluated = []

    def locator(self, selector):
        return _ProfileLocator(self, selector)

    async def wait_for_selector(self, selector, timeout=None):
        if self.visible.get(selector, False):
            return object()
        raise RuntimeError(f"selector not visible: {selector}")


class _StrictAgeProfileLocator(_ProfileLocator):
    async def fill(self, value):
        if self.selector == 'input[name="age"]':
            raise RuntimeError('strict mode violation: locator("input[name=\\"age\\"]") resolved to 2 elements')
        await super().fill(value)


class _StrictAgeProfilePage(_ProfilePage):
    def locator(self, selector):
        return _StrictAgeProfileLocator(self, selector)


class _MaskedBirthdayProfileLocator(_ProfileLocator):
    async def press_sequentially(self, text, delay=None):
        if self.selector in main.CSS_OA_BIRTHDAY_INPUT_SELECTORS:
            digits = "".join(ch for ch in text if ch.isdigit())
            if "/" in text:
                self.page.birthday_value = f"{digits[:4]}/月/日"
            elif len(digits) == 8:
                self.page.birthday_value = f"{digits[:4]}/{digits[4:6]}/{digits[6:8]}"
        await super().press_sequentially(text, delay=delay)


class _MaskedBirthdayProfilePage(_ProfilePage):
    def __init__(self, visible, click_failures=None):
        super().__init__(visible, click_failures=click_failures)
        self.birthday_value = ""

    def locator(self, selector):
        return _MaskedBirthdayProfileLocator(self, selector)


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


class _LocalizedRateLimitPage(_RateLimitPage):
    def has_selector(self, selector):
        if selector == 'text=/验证过程中出错/i':
            return self.rate_limit_visible
        if selector in main.RATE_LIMIT_RETRY_BUTTON_SELECTORS:
            return self.rate_limit_visible
        return False


class _AccountDeactivatedPage(_RateLimitPage):
    def __init__(self):
        super().__init__()
        self.url = "https://auth.openai.com/email-verification"

    def has_selector(self, selector):
        if selector == 'text=/account_deactivated/i':
            return True
        if selector in main.RATE_LIMIT_RETRY_BUTTON_SELECTORS:
            return True
        return False


class _SecondLoginLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.first = self

    async def count(self):
        if self.selector == main.CSS_OA_NAME_INPUT:
            return 1 if self.page.state == "profile" else 0
        if self.selector == main.CSS_L_CODE:
            return 1 if self.page.state == "code" else 0
        if self.selector == main.CSS_L_CONSENT_BTN:
            return 1 if self.page.state == "consent" else 0
        if self.selector == main.CSS_OA_BIRTHDAY_HIDDEN_INPUT:
            return 1 if self.page.state == "profile" else 0
        if self.selector == main.CSS_INVALID_PASSWORD_ERROR:
            return 1 if self.page.state == "password_error" else 0
        if self.selector == main.CSS_L_PASSWORDLESS_LOGIN_BTN:
            return 1 if self.page.state == "password_error" else 0
        return 0

    async def is_visible(self, timeout=None):
        return await self.count() > 0


class _SecondLoginProfilePage:
    def __init__(self):
        self.state = "profile"
        self.url = "https://auth.openai.com/log-in"

    async def goto(self, url, wait_until=None):
        self.url = url

    def locator(self, selector):
        return _SecondLoginLocator(self, selector)

    async def wait_for_selector(self, selector, timeout=None):
        if selector in {main.CSS_L_EMAIL, main.CSS_L_PASSWORD}:
            return object()
        if selector == main.CSS_L_CODE and self.state == "code":
            return object()
        if selector == main.CSS_L_CONSENT_BTN and self.state == "consent":
            return object()
        raise RuntimeError(f"selector not visible: {selector}")


class _SecondLoginWrongPasswordPage:
    def __init__(self):
        self.state = "password_error"
        self.url = "https://auth.openai.com/log-in/password"

    async def goto(self, url, wait_until=None):
        self.url = url

    def locator(self, selector):
        return _SecondLoginLocator(self, selector)

    async def wait_for_selector(self, selector, timeout=None):
        if selector in {main.CSS_L_EMAIL, main.CSS_L_PASSWORD}:
            return object()
        if selector == main.CSS_L_CODE and self.state == "code":
            return object()
        raise RuntimeError(f"selector not visible: {selector}")


class _SecondLoginBirthdayDialogLocator(_SecondLoginLocator):
    async def count(self):
        if self.selector in openai_flows.CSS_OA_BIRTHDAY_CONFIRM_BUTTON_SELECTORS:
            return 1 if self.page.state == "birthday_dialog" else 0
        if self.selector == main.CSS_OA_AGE_INPUT_SELECTORS[0]:
            return 1 if self.page.state == "birthday_dialog" else 0
        return await super().count()


class _SecondLoginBirthdayDialogPage:
    def __init__(self):
        self.state = "birthday_dialog"
        self.url = "https://auth.openai.com/about-you"

    async def goto(self, url, wait_until=None):
        self.url = url

    def locator(self, selector):
        return _SecondLoginBirthdayDialogLocator(self, selector)

    async def wait_for_selector(self, selector, timeout=None):
        if selector in {main.CSS_L_EMAIL, main.CSS_L_PASSWORD}:
            return object()
        if selector == main.CSS_L_CONSENT_BTN and self.state == "consent":
            return object()
        raise RuntimeError(f"selector not visible: {selector}")


class _SecondLoginRetryLoopPage:
    def __init__(self):
        self.state = "code"
        self.url = "https://auth.openai.com/email-verification"

    async def goto(self, url, wait_until=None):
        self.url = url

    def locator(self, selector):
        return _SecondLoginLocator(self, selector)

    async def wait_for_selector(self, selector, timeout=None):
        if selector in {main.CSS_L_EMAIL, main.CSS_L_PASSWORD}:
            return object()
        if selector == main.CSS_L_CODE and self.state == "code":
            return object()
        if selector == main.CSS_L_CONSENT_BTN and self.state == "consent":
            return object()
        raise RuntimeError(f"selector not visible: {selector}")


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

    def get_expected_callback_url(self):
        return self.redirect_uri

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

        with patch.object(app_runner, "async_playwright", return_value=playwright_manager), patch.object(
            app_runner,
            "exchange_refresh_token",
            AsyncMock(return_value="access-token"),
        ), patch.object(
            app_runner,
            "openai_register",
            AsyncMock(
                return_value=main.RegistrationFlowOutcome(
                    registration_status="already_exists",
                    should_verify_registration=False,
                )
            ),
        ), patch.object(
            app_runner,
            "verify_registration_complete",
            AsyncMock(),
        ) as verify_mock, patch.object(
            app_runner,
            "openai_second_login",
            AsyncMock(return_value=f"{oauth_client.redirect_uri}?code=auth-code&state=session"),
        ) as login_mock, patch.object(playwright_helpers, "Stealth", return_value=stealth), patch.object(app_runner, "OAUTH_CLIENT", oauth_client):
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

        with patch.object(app_runner, "async_playwright", return_value=playwright_manager), patch.object(
            app_runner,
            "exchange_refresh_token",
            AsyncMock(return_value="access-token"),
        ), patch.object(app_runner, "openai_register", AsyncMock()) as register_mock, patch.object(
            app_runner,
            "verify_registration_complete",
            AsyncMock(),
        ), patch.object(
            app_runner,
            "openai_second_login",
            AsyncMock(return_value=f"{oauth_client.redirect_uri}?code=auth-code&state=session"),
        ) as login_mock, patch.object(playwright_helpers, "Stealth", return_value=stealth), patch.object(app_runner, "OAUTH_CLIENT", oauth_client):
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

        with patch.object(app_runner, "async_playwright", return_value=playwright_manager), patch.object(
            app_runner,
            "exchange_refresh_token",
            AsyncMock(return_value="access-token"),
        ), patch.object(
            app_runner,
            "openai_second_login",
            AsyncMock(return_value=f"{oauth_client.redirect_uri}?code=auth-code&state=session"),
        ) as login_mock, patch.object(playwright_helpers, "Stealth", return_value=stealth), patch.object(app_runner, "OAUTH_CLIENT", oauth_client):
            result = await main.run_login_stage(account, registration_result)

        self.assertEqual(login_mock.await_args.args[1:], (
            "user@example.com",
            "Secret123000",
            "access-token",
            "https://auth.openai.com/oauth/authorize?state=session",
            oauth_client.redirect_uri,
        ))
        oauth_client.exchange_token_and_save.assert_awaited_once_with("auth-code", "user@example.com", oauth_client.session)
        self.assertEqual(result.login_status, "success")

    async def test_run_login_stage_starts_local_callback_server(self):
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

        class _FakeCallbackServer:
            instances = []

            def __init__(self, callback_url):
                self.callback_url = callback_url
                self.started = False
                self.closed = False
                _FakeCallbackServer.instances.append(self)

            async def __aenter__(self):
                self.started = True
                return self

            async def __aexit__(self, exc_type, exc, tb):
                self.closed = True
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

        with patch.object(app_runner, "async_playwright", return_value=playwright_manager), patch.object(
            app_runner,
            "exchange_refresh_token",
            AsyncMock(return_value="access-token"),
        ), patch.object(
            app_runner,
            "LocalOAuthCallbackServer",
            _FakeCallbackServer,
        ), patch.object(
            app_runner,
            "openai_second_login",
            AsyncMock(return_value=f"{oauth_client.redirect_uri}?code=auth-code&state=session"),
        ) as login_mock, patch.object(playwright_helpers, "Stealth", return_value=stealth), patch.object(app_runner, "OAUTH_CLIENT", oauth_client):
            await main.run_login_stage(account, registration_result)

        self.assertEqual(len(_FakeCallbackServer.instances), 1)
        self.assertEqual(_FakeCallbackServer.instances[0].callback_url, oauth_client.redirect_uri)
        self.assertTrue(_FakeCallbackServer.instances[0].started)
        self.assertTrue(_FakeCallbackServer.instances[0].closed)
        self.assertIs(login_mock.await_args.kwargs["callback_server"], _FakeCallbackServer.instances[0])

    async def test_openai_login_flow_raises_when_no_expected_followup_state_is_detected(self):
        page = _LoginFlowPage()

        with patch.object(openai_flows, "human_type", AsyncMock()), patch.object(
            openai_flows,
            "human_click",
            AsyncMock(),
        ), patch.object(openai_flows, "human_delay", AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "Login flow"):
                await main.openai_login_flow(page, "user@example.com", "Secret123", "token")

    async def test_openai_second_login_handles_profile_page_before_consent(self):
        page = _SecondLoginProfilePage()

        async def fake_wait(page_obj, selector, timeout=15000):
            return await page_obj.wait_for_selector(selector, timeout=timeout)

        async def fake_fill_profile_age(page_obj, name, age_value, year_value, birthday_value):
            page_obj.state = "consent"
            page_obj.url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"

        with patch.object(openai_flows, "wait_for_selector_with_rate_limit_retry", AsyncMock(side_effect=fake_wait)), patch.object(
            openai_flows,
            "human_type",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_click",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_delay",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "fill_profile_age",
            AsyncMock(side_effect=fake_fill_profile_age),
        ) as fill_mock, patch.object(
            openai_flows,
            "submit_verification_code_with_retry",
            AsyncMock(),
        ) as submit_mock, patch.object(
            openai_flows,
            "wait_for_callback_url",
            AsyncMock(return_value="http://localhost:2456/auth/callback?code=ok"),
        ) as callback_mock, patch.object(
            openai_flows,
            "retry_rate_limit_error_page",
            AsyncMock(return_value=False),
        ):
            callback_url = await openai_flows.openai_second_login(
                page,
                "user@example.com",
                "Secret123",
                "token",
                "https://auth.openai.com/oauth/authorize?state=session",
                "http://localhost:2456/auth/callback",
            )

        self.assertEqual(callback_url, "http://localhost:2456/auth/callback?code=ok")
        fill_mock.assert_awaited_once()
        submit_mock.assert_not_awaited()
        self.assertEqual(callback_mock.await_args.args[1], "http://localhost:2456/auth/callback")
        self.assertEqual(callback_mock.await_args.kwargs["timeout_s"], 30.0)

    async def test_openai_second_login_confirms_birthday_dialog_before_refilling_profile(self):
        page = _SecondLoginBirthdayDialogPage()

        async def fake_wait(page_obj, selector, timeout=15000):
            return await page_obj.wait_for_selector(selector, timeout=timeout)

        async def fake_human_click(page_obj, selector):
            if selector in openai_flows.CSS_OA_BIRTHDAY_CONFIRM_BUTTON_SELECTORS:
                page_obj.state = "consent"
                page_obj.url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
            return None

        with patch.object(openai_flows, "wait_for_selector_with_rate_limit_retry", AsyncMock(side_effect=fake_wait)), patch.object(
            openai_flows,
            "human_type",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_click",
            AsyncMock(side_effect=fake_human_click),
        ) as click_mock, patch.object(
            openai_flows,
            "human_delay",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "fill_profile_age",
            AsyncMock(side_effect=AssertionError("birthday dialog should be confirmed before refilling profile")),
        ) as fill_mock, patch.object(
            openai_flows,
            "wait_for_callback_url",
            AsyncMock(return_value="http://localhost:2456/auth/callback?code=ok"),
        ), patch.object(
            openai_flows,
            "retry_rate_limit_error_page",
            AsyncMock(return_value=False),
        ):
            callback_url = await openai_flows.openai_second_login(
                page,
                "user@example.com",
                "Secret123",
                "token",
                "https://auth.openai.com/oauth/authorize?state=session",
                "http://localhost:2456/auth/callback",
            )

        self.assertEqual(callback_url, "http://localhost:2456/auth/callback?code=ok")
        fill_mock.assert_not_awaited()
        self.assertIn(
            unittest.mock.call(page, openai_flows.CSS_OA_BIRTHDAY_CONFIRM_BUTTON_SELECTORS[0]),
            click_mock.await_args_list,
        )

    async def test_openai_second_login_survives_multiple_retry_pages_before_consent(self):
        page = _SecondLoginRetryLoopPage()
        submit_attempts = 0

        async def fake_wait(page_obj, selector, timeout=15000):
            return await page_obj.wait_for_selector(selector, timeout=timeout)

        async def fake_submit(page_obj, selector, access_token, submit_mode="enter", submit_selector=None, attempted_codes=None):
            nonlocal submit_attempts
            submit_attempts += 1
            if submit_attempts < 3:
                page_obj.state = "retry_error"
                page_obj.url = "https://auth.openai.com/email-verification"
            else:
                page_obj.state = "consent"
                page_obj.url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
            return "123456"

        async def fake_retry(page_obj):
            if page_obj.state != "retry_error":
                return False
            page_obj.state = "code"
            page_obj.url = "https://auth.openai.com/email-verification"
            return True

        with patch.object(openai_flows, "wait_for_selector_with_rate_limit_retry", AsyncMock(side_effect=fake_wait)), patch.object(
            openai_flows,
            "human_type",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_click",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_delay",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "submit_verification_code_with_retry",
            AsyncMock(side_effect=fake_submit),
        ), patch.object(
            openai_flows,
            "wait_for_callback_url",
            AsyncMock(return_value="http://localhost:2456/auth/callback?code=ok"),
        ), patch.object(
            openai_flows,
            "retry_rate_limit_error_page",
            AsyncMock(side_effect=fake_retry),
        ):
            callback_url = await openai_flows.openai_second_login(
                page,
                "user@example.com",
                "Secret123",
                "token",
                "https://auth.openai.com/oauth/authorize?state=session",
                "http://localhost:2456/auth/callback",
            )

        self.assertEqual(callback_url, "http://localhost:2456/auth/callback?code=ok")
        self.assertEqual(submit_attempts, 3)

    async def test_openai_second_login_reuses_attempted_codes_after_retry_page(self):
        page = _SecondLoginRetryLoopPage()

        async def fake_wait(page_obj, selector, timeout=15000):
            return await page_obj.wait_for_selector(selector, timeout=timeout)

        async def fake_submit(page_obj, selector, access_token, submit_mode="enter", submit_selector=None, attempted_codes=None):
            if page_obj.state == "code" and not attempted_codes:
                attempted_codes.add("111111")
                page_obj.state = "retry_error"
                return "111111"
            self.assertEqual(attempted_codes, {"111111"})
            page_obj.state = "consent"
            page_obj.url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
            return "222222"

        async def fake_retry(page_obj):
            if page_obj.state != "retry_error":
                return False
            page_obj.state = "code"
            page_obj.url = "https://auth.openai.com/email-verification"
            return True

        with patch.object(openai_flows, "wait_for_selector_with_rate_limit_retry", AsyncMock(side_effect=fake_wait)), patch.object(
            openai_flows,
            "human_type",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_click",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_delay",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "submit_verification_code_with_retry",
            AsyncMock(side_effect=fake_submit),
        ), patch.object(
            openai_flows,
            "wait_for_callback_url",
            AsyncMock(return_value="http://localhost:2456/auth/callback?code=ok"),
        ), patch.object(
            openai_flows,
            "retry_rate_limit_error_page",
            AsyncMock(side_effect=fake_retry),
        ):
            callback_url = await openai_flows.openai_second_login(
                page,
                "user@example.com",
                "Secret123",
                "token",
                "https://auth.openai.com/oauth/authorize?state=session",
                "http://localhost:2456/auth/callback",
            )

        self.assertEqual(callback_url, "http://localhost:2456/auth/callback?code=ok")

    async def test_openai_second_login_reports_max_check_attempts_after_repeated_retry_pages(self):
        page = _SecondLoginRetryLoopPage()

        async def fake_wait(page_obj, selector, timeout=15000):
            return await page_obj.wait_for_selector(selector, timeout=timeout)

        async def fake_submit(page_obj, selector, access_token, submit_mode="enter", submit_selector=None, attempted_codes=None):
            attempted_codes.add(str(len(attempted_codes) + 1))
            page_obj.state = "retry_error"
            page_obj.url = "https://auth.openai.com/email-verification"
            return "123456"

        async def fake_retry(page_obj):
            if page_obj.state != "retry_error":
                return False
            page_obj.state = "code"
            page_obj.url = "https://auth.openai.com/email-verification"
            return True

        with patch.object(openai_flows, "wait_for_selector_with_rate_limit_retry", AsyncMock(side_effect=fake_wait)), patch.object(
            openai_flows,
            "human_type",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_click",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_delay",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "submit_verification_code_with_retry",
            AsyncMock(side_effect=fake_submit),
        ), patch.object(
            openai_flows,
            "retry_rate_limit_error_page",
            AsyncMock(side_effect=fake_retry),
        ):
            with self.assertRaisesRegex(RuntimeError, "max_check_attempts"):
                await openai_flows.openai_second_login(
                    page,
                    "user@example.com",
                    "Secret123",
                    "token",
                    "https://auth.openai.com/oauth/authorize?state=session",
                    "http://localhost:2456/auth/callback",
                )

    async def test_openai_second_login_reclassifies_missing_code_after_retry_pages_as_remote_block(self):
        page = _SecondLoginRetryLoopPage()
        submit_calls = 0

        async def fake_wait(page_obj, selector, timeout=15000):
            return await page_obj.wait_for_selector(selector, timeout=timeout)

        async def fake_submit(page_obj, selector, access_token, submit_mode="enter", submit_selector=None, attempted_codes=None):
            nonlocal submit_calls
            submit_calls += 1
            if submit_calls == 1:
                attempted_codes.add("111111")
                page_obj.state = "retry_error"
                page_obj.url = "https://auth.openai.com/email-verification"
                return "111111"
            raise RuntimeError("Failed to find verification code after max retries")

        async def fake_retry(page_obj):
            if page_obj.state != "retry_error":
                return False
            page_obj.state = "code"
            page_obj.url = "https://auth.openai.com/email-verification"
            return True

        with patch.object(openai_flows, "wait_for_selector_with_rate_limit_retry", AsyncMock(side_effect=fake_wait)), patch.object(
            openai_flows,
            "human_type",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_click",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_delay",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "submit_verification_code_with_retry",
            AsyncMock(side_effect=fake_submit),
        ), patch.object(
            openai_flows,
            "retry_rate_limit_error_page",
            AsyncMock(side_effect=fake_retry),
        ):
            with self.assertRaisesRegex(RuntimeError, "max_check_attempts"):
                await openai_flows.openai_second_login(
                    page,
                    "user@example.com",
                    "Secret123",
                    "token",
                    "https://auth.openai.com/oauth/authorize?state=session",
                    "http://localhost:2456/auth/callback",
                )

    async def test_openai_second_login_falls_back_to_passwordless_code_when_password_is_rejected(self):
        page = _SecondLoginWrongPasswordPage()

        async def fake_wait(page_obj, selector, timeout=15000):
            return await page_obj.wait_for_selector(selector, timeout=timeout)

        async def fake_human_click(page_obj, selector):
            if selector == main.CSS_L_PASSWORDLESS_LOGIN_BTN:
                page_obj.state = "code"
            return None

        async def fake_submit(page_obj, selector, access_token, submit_mode="enter", submit_selector=None, attempted_codes=None):
            page_obj.state = "consent"
            page_obj.url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
            return "123456"

        with patch.object(openai_flows, "wait_for_selector_with_rate_limit_retry", AsyncMock(side_effect=fake_wait)), patch.object(
            openai_flows,
            "human_type",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "human_click",
            AsyncMock(side_effect=fake_human_click),
        ) as click_mock, patch.object(
            openai_flows,
            "human_delay",
            AsyncMock(),
        ), patch.object(
            openai_flows,
            "submit_verification_code_with_retry",
            AsyncMock(side_effect=fake_submit),
        ) as submit_mock, patch.object(
            openai_flows,
            "wait_for_callback_url",
            AsyncMock(return_value="http://localhost:2456/auth/callback?code=ok"),
        ), patch.object(
            openai_flows,
            "retry_rate_limit_error_page",
            AsyncMock(return_value=False),
        ):
            await openai_flows.openai_second_login(
                page,
                "user@example.com",
                "Secret123",
                "token",
                "https://auth.openai.com/oauth/authorize?state=session",
                "http://localhost:2456/auth/callback",
            )

        self.assertIn(main.CSS_L_PASSWORDLESS_LOGIN_BTN, [call.args[1] for call in click_mock.await_args_list])
        submit_mock.assert_awaited()

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

        with patch.object(openai_flows, "human_delay", AsyncMock()):
            await main.wait_for_selector_with_rate_limit_retry(page, "input[name='email']", timeout=1)

        self.assertEqual(page.retry_clicks, 1)
        self.assertEqual(page.wait_attempts, 2)

    async def test_retry_rate_limit_error_page_handles_localized_error_message(self):
        page = _LocalizedRateLimitPage()

        with patch.object(openai_flows, "human_delay", AsyncMock()):
            retried = await openai_flows.retry_rate_limit_error_page(page)

        self.assertTrue(retried)
        self.assertEqual(page.retry_clicks, 1)

    async def test_get_login_terminal_state_reports_account_deactivated_as_hard_failure(self):
        page = _AccountDeactivatedPage()

        state = await openai_flows.get_login_terminal_state(page, "http://localhost:2456/auth/callback")

        self.assertEqual(state.status, "hard_failure")
        self.assertIn("account_deactivated", state.detail)

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

    async def test_get_login_terminal_state_does_not_treat_password_page_continue_button_as_consent(self):
        page = _VisibleSelectorPage(
            "https://auth.openai.com/log-in/password",
            {main.CSS_L_CONSENT_BTN: True},
        )

        state = await main.get_login_terminal_state(page)

        self.assertIsNone(state)

    async def test_fill_profile_age_uses_age_input_when_year_input_is_missing(self):
        page = _ProfilePage(
            {
                main.CSS_OA_NAME_INPUT: True,
                main.CSS_OA_AGE_INPUT_SELECTORS[0]: True,
                main.CSS_OA_BIRTHDAY_YEAR: False,
            }
        )

        with patch.object(openai_flows, "human_delay", AsyncMock()):
            await main.fill_profile_age(page, "CroffMost", "32", "1994", "1994-06-02")

        self.assertEqual(
            page.fills,
            [
                (main.CSS_OA_NAME_INPUT, ""),
                (main.CSS_OA_AGE_INPUT_SELECTORS[0], ""),
            ],
        )
        self.assertIn((main.CSS_OA_NAME_INPUT, "CroffMost"), page.typed)
        self.assertIn((main.CSS_OA_AGE_INPUT_SELECTORS[0], "32"), page.typed)

    async def test_fill_profile_age_ignores_hidden_age_inputs(self):
        page = _StrictAgeProfilePage(
            {
                main.CSS_OA_NAME_INPUT: True,
                main.CSS_OA_AGE_INPUT_SELECTORS[0]: True,
                main.CSS_OA_BIRTHDAY_YEAR: False,
            }
        )

        with patch.object(openai_flows, "human_delay", AsyncMock()):
            await main.fill_profile_age(page, "SkibaFarug", "24", "2002", "2002-04-05")

        self.assertIn((main.CSS_OA_AGE_INPUT_SELECTORS[0], ""), page.fills)
        self.assertIn((main.CSS_OA_AGE_INPUT_SELECTORS[0], "24"), page.typed)

    async def test_fill_profile_age_confirms_birthday_dialog_when_present(self):
        page = _ProfilePage(
            {
                main.CSS_OA_NAME_INPUT: True,
                main.CSS_OA_AGE_INPUT_SELECTORS[0]: True,
                main.CSS_OA_BIRTHDAY_YEAR: False,
                'button:has-text("确定")': True,
            }
        )

        with patch.object(openai_flows, "human_delay", AsyncMock()):
            await main.fill_profile_age(page, "SkibaFarug", "24", "2002", "2002-04-05")

        self.assertIn('button:has-text("确定")', page.clicks)

    def test_generate_birth_profile_stays_within_required_year_range(self):
        years = set()
        for _ in range(200):
            _, birthday, age, year = openai_flows.generate_birth_profile("BadameWages1225")
            years.add(int(year))
            self.assertTrue(1980 <= int(year) <= 2006)
            self.assertTrue(birthday.startswith(year))
            self.assertGreaterEqual(int(age), 18)

        self.assertTrue(years)

    async def test_fill_profile_age_sets_hidden_birthday_value_for_dropdown_variant(self):
        page = _ProfilePage(
            {
                main.CSS_OA_NAME_INPUT: True,
                main.CSS_OA_BIRTHDAY_YEAR: False,
                main.CSS_OA_AGE_INPUT_SELECTORS[0]: False,
                main.CSS_OA_BIRTHDAY_HIDDEN_INPUT: True,
            }
        )

        with patch.object(openai_flows, "human_delay", AsyncMock()):
            await main.fill_profile_age(page, "BadameWages", "33", "1993", "1993-08-14")

        self.assertIn((main.CSS_OA_NAME_INPUT, "BadameWages"), page.typed)
        self.assertIn((main.CSS_OA_BIRTHDAY_HIDDEN_INPUT, "1993-08-14"), page.evaluated)

    async def test_fill_profile_age_types_visible_birthday_input_when_present(self):
        page = _ProfilePage(
            {
                main.CSS_OA_NAME_INPUT: True,
                main.CSS_OA_BIRTHDAY_YEAR: False,
                main.CSS_OA_AGE_INPUT_SELECTORS[0]: False,
                main.CSS_OA_BIRTHDAY_INPUT_SELECTORS[0]: True,
                main.CSS_OA_BIRTHDAY_HIDDEN_INPUT: False,
            }
        )

        with patch.object(openai_flows, "human_delay", AsyncMock()):
            await main.fill_profile_age(page, "KanoaDaggett", "27", "1999", "1999-04-07")

        self.assertIn((main.CSS_OA_NAME_INPUT, "KanoaDaggett"), page.typed)
        self.assertIn(main.CSS_OA_BIRTHDAY_INPUT_SELECTORS[0], page.clicks)
        self.assertIn((main.CSS_OA_BIRTHDAY_INPUT_SELECTORS[0], "Control+a"), page.presses)
        self.assertIn((main.CSS_OA_BIRTHDAY_INPUT_SELECTORS[0], "19990407"), page.typed)
        self.assertIn((main.CSS_OA_BIRTHDAY_INPUT_SELECTORS[0], "Tab"), page.presses)

    async def test_fill_profile_age_uses_digits_for_masked_birthday_input(self):
        page = _MaskedBirthdayProfilePage(
            {
                main.CSS_OA_NAME_INPUT: True,
                main.CSS_OA_BIRTHDAY_YEAR: False,
                main.CSS_OA_AGE_INPUT_SELECTORS[0]: False,
                main.CSS_OA_BIRTHDAY_INPUT_SELECTORS[0]: True,
                main.CSS_OA_BIRTHDAY_HIDDEN_INPUT: False,
            }
        )

        with patch.object(openai_flows, "human_delay", AsyncMock()):
            await main.fill_profile_age(page, "NormFoxwell", "31", "1993", "1993-04-07")

        self.assertEqual(page.birthday_value, "1993/04/07")

    async def test_fill_profile_age_uses_visible_year_spinbutton_when_present(self):
        page = _ProfilePage(
            {
                main.CSS_OA_NAME_INPUT: True,
                main.CSS_OA_BIRTHDAY_YEAR: True,
                main.CSS_OA_BIRTHDAY_HIDDEN_INPUT: True,
            },
            click_failures={main.CSS_OA_BIRTHDAY_YEAR},
        )

        with patch.object(openai_flows, "human_delay", AsyncMock()):
            await main.fill_profile_age(page, "WaddellFlavia", "27", "1999", "1999-04-04")

        self.assertIn(main.CSS_OA_BIRTHDAY_YEAR, page.focused)
        self.assertIn((main.CSS_OA_BIRTHDAY_YEAR, "1999"), page.typed)
        self.assertIn((main.CSS_OA_BIRTHDAY_YEAR, "Tab"), page.presses)


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
