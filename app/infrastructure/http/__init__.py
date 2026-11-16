"""HTTP adapters (M0: diagnostics only)."""

from app.infrastructure.http.httpx_client import HttpxClient, make_http_client

__all__ = ["HttpxClient", "make_http_client"]
