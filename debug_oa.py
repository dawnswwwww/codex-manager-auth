import asyncio
from playwright.async_api import async_playwright

OPENAI_OAUTH_URL = (
    "https://auth.openai.com/oauth/authorize"
    "?response_type=code"
    "&client_id=app_EMoamEEZ73f0CkXaXp7hrann"
    "&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback"
    "&scope=openid%20profile%20email%20offline_access%20api.connectors.read%20api.connectors.invoke"
    "&code_challenge=eIqXnpzcbqDoJMEQ0wfICGkVjCN3xImfz1qsLTygoSE"
    "&code_challenge_method=S256"
    "&id_token_add_organizations=true"
    "&codex_cli_simplified_flow=true"
    "&state=-Xfx2Gr4g51W52DY1dR1Rqrx6SawslWkl-9kJ-tWMDI"
    "&originator=codex_cli_rs"
)


async def dump_elements(page, label=""):
    elements = await page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('input, button, a').forEach(el => {
            results.push({
                tag: el.tagName,
                type: el.type || '',
                id: el.id || '',
                name: el.name || '',
                placeholder: el.placeholder || '',
                text: el.textContent?.trim().slice(0, 80) || '',
                href: el.href || '',
            });
        });
        return results;
    }""")
    print(f"\n{'='*60}")
    print(f" {label}")
    print(f" URL: {page.url}")
    print(f" Found {len(elements)} elements:")
    for el in elements:
        parts = [f"  <{el['tag']}"]
        if el['id']: parts.append(f"id={el['id']}")
        if el['type']: parts.append(f"type={el['type']}")
        if el['name']: parts.append(f"name={el['name']}")
        if el['placeholder']: parts.append(f'placeholder="{el["placeholder"]}"')
        if el['text']: parts.append(f'text="{el["text"]}"')
        if el['href']: parts.append(f"href={el['href']}")
        print(" ".join(parts) + ">")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        await page.goto(OPENAI_OAUTH_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await dump_elements(page, "Login page")

        # Click sign up
        signup_link = page.locator('a[href*="create-account"]')
        await signup_link.click()
        await asyncio.sleep(3)
        await dump_elements(page, "Registration page")

        input("\nPress Enter to close...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
