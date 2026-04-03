import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import secrets
from urllib.parse import urlencode, urlparse, parse_qs

import httpx


OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
DEFAULT_SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"


@dataclass(frozen=True)
class OpenAIOAuthSession:
    code_verifier: str
    code_challenge: str
    state: str


class OpenAIOAuthClient:
    def __init__(self, client_id: str, redirect_port: int, token_output_dir: Path, scope: str = DEFAULT_SCOPE):
        self.client_id = client_id
        self.redirect_port = redirect_port
        self.redirect_uri = f"http://localhost:{redirect_port}/auth/callback"
        self.token_output_dir = Path(token_output_dir)
        self.scope = scope

    def create_session(self) -> OpenAIOAuthSession:
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("utf-8")).digest()
        ).rstrip(b"=").decode("utf-8")
        state = secrets.token_hex(16)
        return OpenAIOAuthSession(
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            state=state,
        )

    def build_auth_url(self, session: OpenAIOAuthSession) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "code_challenge": session.code_challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": session.state,
            "originator": "codex_cli_rs",
        }
        return f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

    def get_expected_callback_url(self) -> str:
        return self.redirect_uri

    def extract_callback_params(self, callback_url: str, session: OpenAIOAuthSession) -> dict | None:
        try:
            if not isinstance(callback_url, str):
                return None
            parsed = urlparse(callback_url)
            params = {
                "code": parse_qs(parsed.query).get("code", [None])[0],
                "state": parse_qs(parsed.query).get("state", [None])[0],
                "error": parse_qs(parsed.query).get("error", [None])[0],
                "error_description": parse_qs(parsed.query).get("error_description", [None])[0],
            }
            if params["state"] and params["state"] != session.state:
                return None
            return params
        except Exception:
            return None

    async def exchange_token_and_save(self, code: str, email: str, session: OpenAIOAuthSession) -> dict:
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": session.code_verifier,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                OAUTH_TOKEN_URL,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if response.status_code != 200:
            raise RuntimeError(f"OAuth token exchange failed ({response.status_code}): {response.text}")

        tokens = response.json()
        now = datetime.now().astimezone()
        expires_at = now + timedelta(seconds=int(tokens.get("expires_in", 0)))
        token_data = {
            "access_token": tokens["access_token"],
            "account_id": self._extract_account_id(tokens["access_token"]),
            "disabled": False,
            "email": email,
            "expired": expires_at.isoformat(),
            "id_token": tokens.get("id_token", ""),
            "last_refresh": now.isoformat(),
            "refresh_token": tokens.get("refresh_token", ""),
            "type": "codex",
        }

        self.token_output_dir.mkdir(parents=True, exist_ok=True)
        token_path = self.token_output_dir / f"token_{int(now.timestamp() * 1000)}.json"
        token_path.write_text(json.dumps(token_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return token_data

    def _extract_account_id(self, access_token: str) -> str:
        try:
            payload = access_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8")
            token_payload = json.loads(decoded)
            api_auth = token_payload.get("https://api.openai.com/auth", {})
            return api_auth.get("chatgpt_account_id", "")
        except Exception:
            return ""
