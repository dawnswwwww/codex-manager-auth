import httpx


TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


async def exchange_refresh_token(
    refresh_token: str,
    client_id: str,
    scope: str = "",
) -> str:
    print("[Token] Exchanging refresh token...")
    body = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    normalized_scope = (scope or "").strip()
    if normalized_scope:
        body["scope"] = normalized_scope

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_URL, data=body)
        if resp.status_code == 200:
            print("[Token] Success!")
            return resp.json()["access_token"]
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text}")
