from dataclasses import dataclass


@dataclass(frozen=True)
class AccountRecord:
    email: str
    password: str
    client_id: str
    refresh_token: str


@dataclass(frozen=True)
class RegistrationFlowOutcome:
    registration_status: str
    should_verify_registration: bool = True


@dataclass(frozen=True)
class PageTerminalState:
    status: str
    detail: str = ""


@dataclass(frozen=True)
class StageExecutionResult:
    status: str
    attempts: int
    error: str = ""
    value: object | None = None


@dataclass(frozen=True)
class AccountExecutionResult:
    email: str
    password: str
    registration_status: str
    login_status: str
    error_reason: str = ""
    registration_attempts: int = 0
    login_attempts: int = 0
    overall_status: str = ""


class NonRetryableStageError(RuntimeError):
    pass

