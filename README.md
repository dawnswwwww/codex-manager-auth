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
- `codex_manager_auth/outlook_mail.py`: Outlook refresh-token exchange and verification-code polling.
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

## Test

```bash
uv run python -m unittest discover -s tests
```
