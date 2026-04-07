OUTLOOK_REST_PROVIDER = "outlook_rest"
GRAPH_PROVIDER = "graph"

MAIL_API_PROVIDER_ALIASES = {
    "outlook_rest": OUTLOOK_REST_PROVIDER,
    "outlook": OUTLOOK_REST_PROVIDER,
    "oauth": OUTLOOK_REST_PROVIDER,
    "graph": GRAPH_PROVIDER,
    "microsoft_graph": GRAPH_PROVIDER,
}


def normalize_mail_api_provider(provider: str) -> str:
    normalized = MAIL_API_PROVIDER_ALIASES.get((provider or "").strip().lower())
    if not normalized:
        raise ValueError(f"Unsupported mail API provider: {provider}")
    return normalized
