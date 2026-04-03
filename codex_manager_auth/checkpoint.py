import csv
from pathlib import Path
import tempfile

from .accounts import normalize_password
from .models import AccountExecutionResult, AccountRecord


RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
RESULT_CSV_HEADERS = (
    "email",
    "password",
    "registration_status",
    "login_status",
    "error_reason",
)


def build_checkpoint_csv_path(accounts_file: Path) -> Path:
    return RESULTS_DIR / f"{accounts_file.stem}_checkpoint.csv"


def normalize_csv_field(value) -> str:
    text = str(value).replace("\r", "\n")
    parts = [part.strip() for part in text.split("\n") if part.strip()]
    return " | ".join(parts)


def create_pending_account_result(account: AccountRecord) -> AccountExecutionResult:
    return AccountExecutionResult(
        email=account.email,
        password=normalize_password(account.password),
        registration_status="pending",
        login_status="pending",
        error_reason="",
        overall_status="pending",
    )


def load_account_results(csv_path: Path) -> list[AccountExecutionResult]:
    if not csv_path.exists():
        return []

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        missing_headers = [header for header in RESULT_CSV_HEADERS if header not in fieldnames]
        if missing_headers:
            raise ValueError(f"Results CSV is missing headers: {', '.join(missing_headers)}")

        results: list[AccountExecutionResult] = []
        for row in reader:
            if not row:
                continue
            results.append(
                AccountExecutionResult(
                    email=(row.get("email") or "").strip(),
                    password=(row.get("password") or "").strip(),
                    registration_status=(row.get("registration_status") or "").strip(),
                    login_status=(row.get("login_status") or "").strip(),
                    error_reason=(row.get("error_reason") or "").strip(),
                )
            )
        return results


def write_account_results_atomic(csv_path: Path, results: list[AccountExecutionResult]):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=csv_path.parent,
        prefix=f"{csv_path.stem}_",
        suffix=".tmp",
        delete=False,
    ) as tmp_file:
        writer = csv.writer(tmp_file)
        writer.writerow(list(RESULT_CSV_HEADERS))
        for result in results:
            if not isinstance(result, AccountExecutionResult):
                raise TypeError(
                    f"write_account_results_atomic expects AccountExecutionResult, got {type(result).__name__}"
                )
            writer.writerow(
                [
                    normalize_csv_field(result.email),
                    normalize_csv_field(result.password),
                    normalize_csv_field(result.registration_status),
                    normalize_csv_field(result.login_status),
                    normalize_csv_field(result.error_reason),
                ]
            )
        tmp_path = Path(tmp_file.name)

    tmp_path.replace(csv_path)


def upsert_account_result(csv_path: Path, result: AccountExecutionResult):
    if not isinstance(result, AccountExecutionResult):
        raise TypeError(f"upsert_account_result expects AccountExecutionResult, got {type(result).__name__}")

    results = load_account_results(csv_path)
    updated = False
    for index, existing in enumerate(results):
        if existing.email == result.email:
            results[index] = result
            updated = True
            break
    if not updated:
        results.append(result)

    write_account_results_atomic(csv_path, results)


def find_account_result(csv_path: Path, email: str) -> AccountExecutionResult | None:
    for result in load_account_results(csv_path):
        if result.email == email:
            return result
    return None

