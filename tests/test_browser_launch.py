import unittest
from unittest.mock import AsyncMock, patch

import main


class _FakePage:
    pass


class _FakeContext:
    def __init__(self, page):
        self.new_page = AsyncMock(return_value=page)


class _FakeBrowser:
    def __init__(self, context):
        self.new_context = AsyncMock(return_value=context)
        self.close = AsyncMock()


class _FakePlaywrightManager:
    def __init__(self, browser):
        self.chromium = type("Chromium", (), {"launch": AsyncMock(return_value=browser)})()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


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
        return {"code": "auth-code", "state": "session"}


class BrowserLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_launches_chromium_with_incognito_flag(self):
        page = _FakePage()
        context = _FakeContext(page)
        browser = _FakeBrowser(context)
        playwright_manager = _FakePlaywrightManager(browser)
        stealth = type("StealthStub", (), {"apply_stealth_async": AsyncMock()})()
        oauth_client = _FakeOAuthClient()

        with patch.object(main, "async_playwright", return_value=playwright_manager), patch.object(
            main,
            "exchange_refresh_token",
            AsyncMock(return_value="access-token"),
        ), patch.object(
            main,
            "verify_registration_complete",
            AsyncMock(),
        ), patch.object(main, "openai_register", AsyncMock()), patch.object(
            main,
            "openai_second_login",
            AsyncMock(return_value=f"{oauth_client.redirect_uri}?code=auth-code&state=session"),
        ), patch.object(main, "Stealth", return_value=stealth), patch.object(main, "OAUTH_CLIENT", oauth_client):
            await main.run("user@example.com", "Secret123", "refresh-token", "client-id")

        launch_kwargs = playwright_manager.chromium.launch.await_args.kwargs
        self.assertIn("--incognito", launch_kwargs["args"])
