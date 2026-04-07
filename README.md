## Project Layout

```text
main.py
app_config.py
app_config.toml
codex_manager_auth/
  app.py
  runner.py
  config.py
  models.py
  accounts.py
  checkpoint.py
  microsoft_oauth.py
  microsoft_mail_api.py
  mail_providers.py
  outlook_mail.py
  openai_oauth.py
  openai_flows.py
  playwright_helpers.py
  openai_selectors.py
tests/
```

## Module Roles

- `main.py`: thin compatibility entrypoint. Script execution still goes through `uv run python main.py`.
- `app_config.py`: compatibility wrapper around package config loading.
- `codex_manager_auth/app.py`: compatibility export layer so the current `main.py` and tests keep working without knowing internal module splits.
- `codex_manager_auth/runner.py`: single-process full-chain orchestration. Each account completes registration → login → token persistence before the next account starts.
- `codex_manager_auth/config.py`: TOML config loading.
- `codex_manager_auth/models.py`: shared dataclasses and non-retryable error type.
- `codex_manager_auth/accounts.py`: account-file parsing and password normalization.
- `codex_manager_auth/checkpoint.py`: checkpoint CSV read/write and atomic upsert.
- `codex_manager_auth/microsoft_oauth.py`: Microsoft OAuth2 refresh-token exchange.
- `codex_manager_auth/microsoft_mail_api.py`: mailbox polling via Outlook REST or Microsoft Graph.
- `codex_manager_auth/mail_providers.py`: provider normalization and aliases (`outlook_rest` / `graph`, with `oauth` accepted as the legacy Outlook REST alias).
- `codex_manager_auth/outlook_mail.py`: backward-compatible export shim for the split Microsoft auth/mail helpers.
- `codex_manager_auth/openai_oauth.py`: OAuth PKCE generation, callback parsing, token exchange, token file output.
- `codex_manager_auth/openai_flows.py`: OpenAI page flow logic, retries, hard-failure detection, verification handling.
- `codex_manager_auth/playwright_helpers.py`: browser/context creation, stealth page setup, and low-level page interaction helpers.
- `codex_manager_auth/openai_selectors.py`: OpenAI page selectors and hard-failure URL keywords.

## Run

```bash
uv run python main.py
```

The default entrypoint now runs the **full chain per account**. It does not batch registration for every row first and login later.

Successful OAuth completion now writes token files under a batch-specific subdirectory inside the configured `token_output_dir` root (default: `tokens/`).
For an account file like `a.txt`, a run will write tokens to a directory like `tokens/a-20260407-153045-tokens/`.

Microsoft mailbox access is now split cleanly:

- OAuth2 refresh-token exchange is handled separately from mailbox API access.
- `mail_api_provider` selects the mailbox backend: `outlook_rest` or `graph` (`oauth` is accepted as a legacy alias for `outlook_rest`).
- `mail_refresh_scope` is optional. Leave it empty to refresh with the original grant. Set it explicitly only when your Microsoft app registration really needs a specific scope string.

## Test

```bash
uv run python -m unittest discover -s tests
```
