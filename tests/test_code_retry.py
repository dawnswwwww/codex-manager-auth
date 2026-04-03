import unittest
from unittest.mock import AsyncMock, patch

import main
from codex_manager_auth import openai_flows


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    async def fill(self, value):
        self.page.filled.append((self.selector, value))

    async def press(self, key):
        self.page.pressed.append((self.selector, key))
        self.page.submit_count += 1
        if self.page.submit_count == 1:
            self.page.invalid_code_visible = True
        else:
            self.page.invalid_code_visible = False

    async def click(self):
        self.page.clicked.append(self.selector)
        self.page.submit_count += 1
        if self.page.submit_count == 1:
            self.page.invalid_code_visible = True
        else:
            self.page.invalid_code_visible = False

    async def count(self):
        return 1 if self.page.has_selector(self.selector) else 0

    async def is_visible(self, timeout=None):
        return self.page.has_selector(self.selector)


class FakePage:
    def __init__(self, invalid_selector):
        self.invalid_selector = invalid_selector
        self.invalid_code_visible = False
        self.submit_count = 0
        self.filled = []
        self.pressed = []
        self.clicked = []

    def locator(self, selector):
        return FakeLocator(self, selector)

    def has_selector(self, selector):
        if selector == self.invalid_selector:
            return self.invalid_code_visible
        if selector == main.CSS_OA_CODE_INPUT:
            return True
        if selector == main.CSS_L_CODE:
            return True
        return False


class SubmitVerificationCodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_with_newer_code_when_page_reports_invalid_code(self):
        page = FakePage(main.CSS_INVALID_CODE_ERROR)

        with patch.object(
            openai_flows,
            "fetch_verification_code",
            AsyncMock(side_effect=["111111", "222222"]),
        ) as fetch_mock, patch.object(openai_flows, "human_type", AsyncMock()) as type_mock, patch.object(
            openai_flows, "human_delay", AsyncMock()
        ):
            final_code = await main.submit_verification_code_with_retry(
                page=page,
                selector=main.CSS_OA_CODE_INPUT,
                access_token="token",
                submit_mode="enter",
            )

        self.assertEqual(final_code, "222222")
        self.assertEqual(fetch_mock.await_count, 2)
        self.assertEqual(type_mock.await_args_list[0].args[2], "111111")
        self.assertEqual(type_mock.await_args_list[1].args[2], "222222")
        self.assertEqual(fetch_mock.await_args_list[1].kwargs["exclude_codes"], {"111111"})
        self.assertEqual(
            page.filled,
            [(main.CSS_OA_CODE_INPUT, ""), (main.CSS_OA_CODE_INPUT, "")],
        )
        self.assertEqual(page.pressed, [(main.CSS_OA_CODE_INPUT, "Enter"), (main.CSS_OA_CODE_INPUT, "Enter")])

    async def test_click_submit_mode_retries_with_newer_code(self):
        page = FakePage(main.CSS_INVALID_CODE_ERROR)

        async def fake_human_click(_page, selector):
            await page.locator(selector).click()

        with patch.object(
            openai_flows,
            "fetch_verification_code",
            AsyncMock(side_effect=["333333", "444444"]),
        ) as fetch_mock, patch.object(openai_flows, "human_type", AsyncMock()), patch.object(
            openai_flows, "human_delay", AsyncMock()
        ), patch.object(openai_flows, "human_click", AsyncMock(side_effect=fake_human_click)) as click_mock:
            final_code = await main.submit_verification_code_with_retry(
                page=page,
                selector=main.CSS_L_CODE,
                access_token="token",
                submit_mode="click",
                submit_selector=main.CSS_L_CONTINUE_CODE,
            )

        self.assertEqual(final_code, "444444")
        self.assertEqual(fetch_mock.await_count, 2)
        self.assertEqual(click_mock.await_count, 2)
        self.assertEqual(click_mock.await_args_list[0].args[1], main.CSS_L_CONTINUE_CODE)
        self.assertEqual(fetch_mock.await_args_list[1].kwargs["exclude_codes"], {"333333"})
        self.assertEqual(page.clicked, [main.CSS_L_CONTINUE_CODE, main.CSS_L_CONTINUE_CODE])


if __name__ == "__main__":
    unittest.main()
