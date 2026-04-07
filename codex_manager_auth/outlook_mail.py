"""Backward-compatible exports for Microsoft OAuth and mail API helpers."""

from .microsoft_mail_api import (
    GRAPH_BASE,
    OUTLOOK_REST_BASE,
    fetch_verification_code,
    get_mail_api_spec,
    normalize_mail_api_provider,
)
from .microsoft_oauth import TOKEN_URL, exchange_refresh_token
