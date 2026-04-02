# Codex Auto-Auth CLI — Design Spec

## Overview

A CLI tool that automates OpenAI account registration and OAuth authorization using Playwright. Processes Hotmail accounts serially, registers them on OpenAI, and completes OAuth flow to trigger a callback.

## Requirements

- Input: JSON file with list of `{email, password}` (Hotmail accounts)
- Output: JSON results file with `{email, status, error?}` per account
- Serial processing, headed browser mode by default
- Random human-like delays between every interaction
- Single account failure does not stop the batch

## Tech Stack

- Python 3.12, managed by uv
- `playwright` — browser automation
- `click` — CLI argument parsing
- `aiohttp` — local HTTP callback server (receives OAuth redirect)

## CLI Usage

```bash
uv run python main.py --accounts accounts.json [--headed] [--delay 5]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--accounts` | required | Path to JSON file with account list |
| `--headed` | True | Show browser window |
| `--delay` | 3 | Base delay (seconds) between accounts |

## Input Format

```json
[
  {"email": "user1@hotmail.com", "password": "pass1"},
  {"email": "user2@hotmail.com", "password": "pass2"}
]
```

## Output

Written to `results/<timestamp>.json`:

```json
[
  {"email": "user1@hotmail.com", "status": "success"},
  {"email": "user2@hotmail.com", "status": "failed", "error": "timeout waiting for verification email"}
]
```

## OAuth Callback Server

A local HTTP server (default port 1455) runs in the background to receive OAuth redirects.

**URL construction:**
- Base template: `https://auth.openai.com/oauth/authorize?response_type=code&client_id=app_EMoamEEZ73f0CkXaXp7hrann&redirect_uri={LOCAL_CALLBACK}&scope=openid%20profile%20email%20offline_access%20api.connectors.read%20api.connectors.invoke&code_challenge_method=S256&codex_cli_simplified_flow=true&originator=codex_cli_rs`
- `redirect_uri` replaced with `http://localhost:{port}/auth/callback`
- `code_challenge` and `state` generated per-session (PKCE flow)

The callback server captures the authorization code from the redirect, confirming the account was successfully authorized.

## Pipeline (per account)

### Phase 0: Start Callback Server

1. Start local HTTP server on port 1455 (or first available port)
2. Construct OAuth URL with local redirect_uri
3. Server waits for callback with `?code=...` parameter

### Phase 1: OpenAI OAuth Registration

1. Open constructed OAuth URL in browser
2. Click "Sign up" button
3. Enter email address (hotmail)
4. Enter password
5. Submit

### Phase 2: Retrieve Verification Code

1. Open new tab in same browser
2. Navigate to Hotmail, sign in with account credentials
3. Go to inbox, find verification email from OpenAI
4. Extract numeric verification code
5. Close Hotmail tab, switch back to OpenAI tab

### Phase 3: Complete Registration

1. Enter verification code
2. Set username — randomly generated (unique per account)
3. Set birthday — random: year 1990-1999, month 1-12, day 1-25
4. Complete registration

### Phase 4: Second OAuth Pass (Consent)

1. Sign out of OpenAI
2. Re-visit OAuth URL
3. Go through login flow again
4. Handle consent/authorization confirmation screen (button XPath TBD)
5. Callback triggers — local server receives auth code → account authorized

## Human-Like Behavior

Every interaction step includes randomized delays:

- **`human_delay(min_s=0.5, max_s=2.0)`**: Random wait before clicks and navigation
- **`human_type(page, selector, text)`**: Type character-by-character with 50-150ms per character
- Base delay between accounts configurable via `--delay` flag
- Additional random jitter (0-30%) on inter-account delays

## Code Structure

```
main.py
├── generate_oauth_url(port, code_challenge, state) -> str
├── start_callback_server(port) -> asyncio.Server
├── generate_username() -> str
├── generate_birthday() -> str  # MM/DD/YYYY, year 1990-1999
├── human_delay(min_s, max_s)
├── human_type(page, selector, text)
├── step_openai_register(page, email, password)
├── step_hotmail_get_code(page, email, password) -> str
├── step_complete_register(page, code, username, birthday)
├── step_second_oauth(page, email, password) -> dict
├── process_account(browser, account) -> dict
└── main(accounts_file, headed, delay, port)  # Click CLI entry
```

## Error Handling

- Per-step timeout: 30 seconds (configurable)
- Single account failure: log error, continue to next account
- No retry on failure — just record and move on
- Browser context fully closed between accounts to ensure clean state

## Open Questions (to be resolved during implementation)

- Hotmail login URL and page selectors (user will provide)
- OpenAI signup/login page selectors (user will provide)
- Consent confirmation button XPath (user will provide after walking the flow)
- Username generation strategy (random string? word combo? TBD)
