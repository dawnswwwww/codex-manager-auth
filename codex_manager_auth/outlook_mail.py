import asyncio
import re

import httpx


TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
OUTLOOK_BASE = "https://outlook.office.com/api/v2.0"


async def exchange_refresh_token(refresh_token: str, client_id: str) -> str:
    print("[Token] Exchanging refresh token...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_URL, data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "https://outlook.office.com/.default openid profile offline_access",
        })
        if resp.status_code == 200:
            print("[Token] Success!")
            return resp.json()["access_token"]
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text}")


async def fetch_verification_code(
    access_token: str,
    max_retries: int = 10,
    interval: int = 5,
    exclude_codes: set[str] | None = None,
) -> str:
    """Poll Outlook inbox for the latest ChatGPT verification code."""
    exclude_codes = exclude_codes or set()
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(base_url=OUTLOOK_BASE, headers=headers, timeout=30) as client:
        for attempt in range(max_retries):
            print(f"[Outlook] Checking inbox (attempt {attempt + 1}/{max_retries})...")
            try:
                resp = await client.get("/me/messages?$top=10&$orderby=ReceivedDateTime desc")
                if resp.status_code == 200:
                    for msg in resp.json().get("value", []):
                        subject = msg.get("Subject", "")
                        body = msg.get("BodyPreview", "")
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
                            print(f"[Outlook] Found latest code: {code}")
                            return code
            except Exception as exc:
                print(f"[Outlook] Error: {exc}")
            if attempt < max_retries - 1:
                await asyncio.sleep(interval)
    raise RuntimeError("Failed to find verification code after max retries")

