from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent.parent / "app_config.toml"


@dataclass(frozen=True)
class AppConfig:
    account_file: Path
    openai_oauth_url: str


def load_app_config(config_file: Path) -> AppConfig:
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))

    account_file = Path(data["account_file"])
    if not account_file.is_absolute():
        account_file = (config_file.parent / account_file).resolve()

    return AppConfig(
        account_file=account_file,
        openai_oauth_url=data["openai_oauth_url"],
    )


APP_CONFIG = load_app_config(DEFAULT_CONFIG_FILE)

