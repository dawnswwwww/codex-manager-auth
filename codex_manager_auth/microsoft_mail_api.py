import asyncio
from dataclasses import dataclass
import re

import httpx

from .config import APP_CONFIG
from .mail_providers import GRAPH_PROVIDER, OUTLOOK_REST_PROVIDER, normalize_mail_api_provider


OUTLOOK_REST_BASE = "https://outlook.office.com/api/v2.0"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@dataclass(frozen=True)
class MailApiSpec:
    provider: str
    base_url: str
    messages_path: str
    subject_key: str
    preview_key: str
    log_label: str


MAIL_API_SPECS = {
    OUTLOOK_REST_PROVIDER: MailApiSpec(
        provider=OUTLOOK_REST_PROVIDER,
        base_url=OUTLOOK_REST_BASE,
        messages_path="/me/messages?$top=10&$orderby=ReceivedDateTime desc",
        subject_key="Subject",
        preview_key="BodyPreview",
        log_label="Outlook",
    ),
    GRAPH_PROVIDER: MailApiSpec(
        provider=GRAPH_PROVIDER,
        base_url=GRAPH_BASE,
        messages_path="/me/messages?$top=10&$orderby=receivedDateTime desc",
        subject_key="subject",
        preview_key="bodyPreview",
        log_label="Graph",
    ),
}


def get_mail_api_spec(provider: str | None = None) -> MailApiSpec:
    normalized_provider = normalize_mail_api_provider(provider or APP_CONFIG.mail_api_provider)
    return MAIL_API_SPECS[normalized_provider]


async def fetch_verification_code(
    access_token: str,
    max_retries: int = 10,
    interval: int = 5,
    exclude_codes: set[str] | None = None,
    provider: str | None = None,
) -> str:
    """Poll the configured Microsoft mail API for the latest ChatGPT verification code."""
    exclude_codes = exclude_codes or set()
    spec = get_mail_api_spec(provider)
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(base_url=spec.base_url, headers=headers, timeout=30) as client:
        for attempt in range(max_retries):
            print(f"[{spec.log_label}] Checking inbox (attempt {attempt + 1}/{max_retries})...")
            try:
                resp = await client.get(spec.messages_path)
                if resp.status_code == 200:
                    for msg in resp.json().get("value", []):
                        subject = msg.get(spec.subject_key, "")
                        body = msg.get(spec.preview_key, "")
                        combined = f"{subject}\n{body}"
                        match = re.search(r'代码为\s*(\d{6})', combined)
                        if not match:
                            match = re.search(r'code\s*(?:is\s*)?(\d{6})', combined, re.IGNORECASE)
                        if not match:
                            match = re.search(r'\b(\d{6})\b', combined)
                        if match:
                            code = match.group(1)
                            if code in exclude_codes:
                                continue
                            print(f"[{spec.log_label}] Found latest code: {code}")
                            return code
            except Exception as exc:
                print(f"[{spec.log_label}] Error: {exc}")
            if attempt < max_retries - 1:
                await asyncio.sleep(interval)
    raise RuntimeError("Failed to find verification code after max retries")
