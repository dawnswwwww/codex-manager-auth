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


class BrowserLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_launches_chromium_with_incognito_flag(self):
        page = _FakePage()
        context = _FakeContext(page)
        browser = _FakeBrowser(context)
        playwright_manager = _FakePlaywrightManager(browser)
        stealth = type("StealthStub", (), {"apply_stealth_async": AsyncMock()})()

        with patch.object(main, "async_playwright", return_value=playwright_manager), patch.object(
            main,
            "exchange_refresh_token",
            AsyncMock(return_value="access-token"),
        ), patch.object(main, "openai_register", AsyncMock()), patch.object(
            main,
            "openai_second_login",
            AsyncMock(),
        ), patch.object(main, "Stealth", return_value=stealth):
            await main.run("user@example.com", "Secret123", "refresh-token", "client-id")

        launch_kwargs = playwright_manager.chromium.launch.await_args.kwargs
        self.assertIn("--incognito", launch_kwargs["args"])
