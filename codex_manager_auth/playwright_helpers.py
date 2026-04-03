import asyncio
import random

from playwright_stealth import Stealth


async def human_delay(min_s: float = 0.5, max_s: float = 2.0):
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_type(page, selector: str, text: str):
    await page.locator(selector).press_sequentially(text, delay=random.randint(50, 150))
    await human_delay(0.3, 0.8)


async def human_click(page, selector: str):
    await human_delay(0.5, 1.5)
    await page.locator(selector).click()


async def close_page_quietly(page):
    if page is None or not hasattr(page, "close"):
        return
    try:
        await page.close()
    except Exception:
        pass


async def new_stealth_page(context):
    page = await context.new_page()
    stealth = Stealth()
    await stealth.apply_stealth_async(page)
    return page


async def is_selector_visible(page, selector: str) -> bool:
    try:
        locator = page.locator(selector)
        return await locator.count() > 0 and await locator.first.is_visible(timeout=1000)
    except Exception:
        return False


async def find_visible_selector(page, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        if await is_selector_visible(page, selector):
            return selector
    return None


async def launch_browser_and_context(playwright_manager):
    browser = await playwright_manager.chromium.launch(
        headless=False,
        args=[
            '--incognito',
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
        ],
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
    )
    return browser, context

