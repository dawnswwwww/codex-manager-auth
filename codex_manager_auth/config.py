from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import tomllib

from .mail_providers import OUTLOOK_REST_PROVIDER, normalize_mail_api_provider


DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent.parent / "app_config.toml"
DEFAULT_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_OAUTH_REDIRECT_PORT = 1455
DEFAULT_TOKEN_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "tokens"


@dataclass(frozen=True)
class AppConfig:
    account_file: Path
    oauth_client_id: str
    oauth_redirect_port: int
    token_output_dir: Path
    mail_api_provider: str
    mail_refresh_scope: str


def _resolve_path(config_file: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (config_file.parent / path).resolve()
    return path


def _load_legacy_oauth_values(data: dict) -> tuple[str, int]:
    oauth_url = data.get("openai_oauth_url", "")
    if not oauth_url:
        return DEFAULT_OAUTH_CLIENT_ID, DEFAULT_OAUTH_REDIRECT_PORT

    parsed = urlparse(oauth_url)
    query = parse_qs(parsed.query)
    client_id = query.get("client_id", [DEFAULT_OAUTH_CLIENT_ID])[0]
    redirect_uri = query.get("redirect_uri", [""])[0]
    redirect_port = DEFAULT_OAUTH_REDIRECT_PORT
    if redirect_uri:
        redirect_port = urlparse(redirect_uri).port or DEFAULT_OAUTH_REDIRECT_PORT
    return client_id, redirect_port


def load_app_config(config_file: Path) -> AppConfig:
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))

    account_file = _resolve_path(config_file, data["account_file"])
    legacy_client_id, legacy_redirect_port = _load_legacy_oauth_values(data)
    token_output_value = data.get("token_output_dir", "tokens")
    mail_api_provider = normalize_mail_api_provider(data.get("mail_api_provider", OUTLOOK_REST_PROVIDER))
    mail_refresh_scope = (data.get("mail_refresh_scope", "") or "").strip()

    return AppConfig(
        account_file=account_file,
        oauth_client_id=data.get("oauth_client_id", legacy_client_id),
        oauth_redirect_port=int(data.get("oauth_redirect_port", legacy_redirect_port)),
        token_output_dir=_resolve_path(config_file, token_output_value),
        mail_api_provider=mail_api_provider,
        mail_refresh_scope=mail_refresh_scope,
    )


APP_CONFIG = load_app_config(DEFAULT_CONFIG_FILE)
