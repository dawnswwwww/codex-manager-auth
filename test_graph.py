import asyncio
import sys

import httpx


TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
OUTLOOK_BASE = "https://outlook.office.com/api/v2.0"


async def exchange_refresh_token(refresh_token: str, client_id: str) -> str:
    print(f"[Token] Exchanging refresh token (client_id={client_id})...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_URL, data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "https://outlook.office.com/.default openid profile offline_access",
        })
        if resp.status_code == 200:
            data = resp.json()
            access_token = data["access_token"]
            print(f"[Token] Success! Scope: {data.get('scope', 'N/A')}")
            return access_token
        else:
            print(f"[Token] Failed ({resp.status_code}): {resp.text}")
            raise RuntimeError(f"Token exchange failed")


async def fetch_inbox(access_token: str):
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(base_url=OUTLOOK_BASE, headers=headers, timeout=30) as client:
        print("[Outlook] Fetching inbox messages...")
        resp = await client.get("/me/messages?$top=10&$orderby=ReceivedDateTime desc")
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            messages = resp.json().get("value", [])
            if not messages:
                print("  No messages found.")
            for i, msg in enumerate(messages):
                print(f"  [{i+1}] Subject: {msg.get('Subject', '(no subject)')}")
                print(f"      Preview: {msg.get('BodyPreview', '')[:120]}")
                print(f"      Received: {msg.get('ReceivedDateTime')}")
                print()
        else:
            print(f"  Error: {resp.text}")


async def exchange_and_fetch(refresh_token: str, client_id: str):
    access_token = await exchange_refresh_token(refresh_token, client_id)
    await fetch_inbox(access_token)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: uv run python test_graph.py <refresh_token> <client_id>")
        sys.exit(1)
    asyncio.run(exchange_and_fetch(sys.argv[1], sys.argv[2]))
