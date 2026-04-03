## Project Layout

```text
main.py
app_config.py
app_config.toml
codex_manager_auth/
  app.py
  config.py
  models.py
  accounts.py
  checkpoint.py
  outlook_mail.py
  openai_oauth.py
  openai_selectors.py
tests/
```

## Module Roles

- `main.py`: thin compatibility entrypoint. Script execution still goes through `uv run python main.py`.
- `app_config.py`: compatibility wrapper around package config loading.
- `codex_manager_auth/app.py`: browser automation flows and single-process orchestration.
- `codex_manager_auth/config.py`: TOML config loading.
- `codex_manager_auth/models.py`: shared dataclasses and non-retryable error type.
- `codex_manager_auth/accounts.py`: account-file parsing and password normalization.
- `codex_manager_auth/checkpoint.py`: checkpoint CSV read/write and atomic upsert.
- `codex_manager_auth/outlook_mail.py`: Outlook refresh-token exchange and verification-code polling.
- `codex_manager_auth/openai_oauth.py`: OAuth PKCE generation, callback parsing, token exchange, token file output.
- `codex_manager_auth/openai_selectors.py`: OpenAI page selectors and hard-failure URL keywords.

## Run

```bash
uv run python main.py
```

Successful OAuth completion now writes token files into the configured `token_output_dir` (default: `tokens/`).

## Test

```bash
uv run python -m unittest discover -s tests
```
