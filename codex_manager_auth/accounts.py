from pathlib import Path

from .models import AccountRecord


ACCOUNT_LINE_SEPARATOR = "----"


def normalize_password(password: str) -> str:
    normalized = password.strip()
    if len(normalized) < 12:
        normalized = normalized + "0" * (12 - len(normalized))
    return normalized


def parse_account_line(line: str, line_number: int) -> AccountRecord:
    parts = [part.strip() for part in line.split(ACCOUNT_LINE_SEPARATOR, maxsplit=3)]
    if len(parts) != 4 or any(not part for part in parts):
        raise ValueError(
            f"Invalid account format on line {line_number}: "
            "expected email----password----client_id----refresh_token"
        )
    email, password, client_id, refresh_token = parts
    return AccountRecord(
        email=email,
        password=password,
        client_id=client_id,
        refresh_token=refresh_token,
    )


def iter_accounts(path: Path):
    seen_emails: set[str] = set()
    found_account = False
    with path.open("r", encoding="utf-8") as fh:
        for line_number, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            found_account = True
            account = parse_account_line(line, line_number)
            if account.email in seen_emails:
                raise ValueError(f"Duplicate account email in account file: {account.email}")
            seen_emails.add(account.email)
            yield account

    if not found_account:
        raise ValueError(f"No accounts found in {path}")


def load_accounts(path: Path) -> list[AccountRecord]:
    return list(iter_accounts(path))

