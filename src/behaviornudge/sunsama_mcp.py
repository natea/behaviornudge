"""Read the Sunsama active timer through Sunsama's official remote MCP server.

https://api.sunsama.com/mcp uses OAuth. The first run (`behaviornudge sunsama-timer --login`)
opens a browser to sign in; tokens are cached in data/sunsama_oauth.json (mode 600, git-ignored)
and refreshed automatically. This module only ever *reads* `sunsama://active-timer`.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SERVER_URL = "https://api.sunsama.com/mcp"
TIMER_URI = "sunsama://active-timer"
CALLBACK_PORT = 33418
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}/callback"


class LoginRequired(RuntimeError):
    """No cached Sunsama tokens; run `behaviornudge sunsama-timer --login` once."""


class SunsamaUnreachable(RuntimeError):
    """Sunsama's OAuth metadata couldn't be loaded; a transient failure, not an expired login."""


def needs_login(exc: BaseException) -> bool:
    """True if `exc` is, wraps or groups a LoginRequired (the MCP client raises from task groups)."""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, LoginRequired):
            return True
        stack.extend(getattr(e, "exceptions", ()) or ())
        stack.extend([e.__cause__, e.__context__])
    return False


class FileTokenStorage:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(d))
        os.chmod(self.path, 0o600)

    async def get_tokens(self):
        """Stored tokens, with `expires_in` recomputed from the saved absolute expiry so an
        expired access token is refreshed (with the refresh token) instead of re-authorized."""
        import time
        from mcp.shared.auth import OAuthToken
        d = dict(self._load().get("tokens") or {})
        if not d:
            return None
        expires_at = d.pop("expires_at", None)
        if expires_at is not None:
            d["expires_in"] = max(0, int(expires_at - time.time()))
        return OAuthToken(**d)

    async def set_tokens(self, tokens) -> None:
        import time
        d = self._load()
        t = tokens.model_dump(mode="json", exclude_none=True)
        if t.get("expires_in") is not None:
            t["expires_at"] = time.time() + int(t["expires_in"])
        d["tokens"] = t
        self._save(d)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        d = self._load().get("client")
        return OAuthClientInformationFull(**d) if d else None

    async def set_client_info(self, info) -> None:
        d = self._load()
        d["client"] = info.model_dump(mode="json", exclude_none=True)
        self._save(d)

    def has_tokens(self) -> bool:
        return bool(self._load().get("tokens"))

    def auth_metadata(self):
        """Sunsama's OAuth server metadata: fetched fresh and cached here, or the cached copy if the
        fetch fails. Without it the SDK refreshes against a guessed `/token` URL, gets a 404 and
        falls back to a full browser login, which looks like an expired sign-in but isn't."""
        from mcp.shared.auth import OAuthMetadata
        fresh = _auth_server_metadata()
        if fresh is not None:
            d = self._load()
            dumped = fresh.model_dump(mode="json", exclude_none=True)
            if d and d.get("auth_metadata") != dumped:
                d["auth_metadata"] = dumped
                self._save(d)
            return fresh
        cached = self._load().get("auth_metadata")
        return OAuthMetadata.model_validate(cached) if cached else None

    @contextlib.contextmanager
    def locked(self):
        """Hold an exclusive lock (across threads and processes) for one Sunsama call.

        Access tokens last 10 minutes; a refresh token may only be used once. Each call builds a
        new OAuth provider that loads tokens from disk on its first request, so holding this lock
        for the whole call means a second caller reads the token the first one just renewed
        instead of spending the old refresh token again (engine-reliability D4)."""
        lock = self.path.with_suffix(".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


class _Callback:
    """One-shot local HTTP listener for the OAuth redirect."""

    def __init__(self):
        self.result: dict = {}
        self.done = threading.Event()

    def serve(self) -> None:
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = parse_qs(urlparse(self.path).query)
                outer.result = {k: v[0] for k, v in q.items()}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<p>behaviornudge is connected to Sunsama. You can close this tab.</p>".encode())
                outer.done.set()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", CALLBACK_PORT), H)
        threading.Thread(target=lambda: (srv.handle_request(), srv.server_close()), daemon=True).start()


def _provider(storage: FileTokenStorage, interactive: bool, metadata=None):
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

    cb = _Callback()

    async def redirect_handler(url: str) -> None:
        if not interactive:
            raise LoginRequired("Sunsama login required: run `uv run behaviornudge sunsama-timer --login` once")
        cb.serve()
        print(f"Opening your browser to sign in to Sunsama…\nIf it doesn't open, visit:\n{url}")
        webbrowser.open(url)

    async def callback_handler() -> AuthorizationCodeResult:
        await asyncio.get_running_loop().run_in_executor(None, cb.done.wait, 300)
        if "code" not in cb.result:
            raise RuntimeError(f"Sunsama sign-in did not complete: {cb.result.get('error', 'timed out')}")
        return AuthorizationCodeResult(code=cb.result["code"], state=cb.result.get("state"))

    meta = OAuthClientMetadata(client_name="behaviornudge (read-only timer)", redirect_uris=[REDIRECT_URI],
                               grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                               token_endpoint_auth_method="none")

    class Provider(OAuthClientProvider):
        async def _initialize(self) -> None:
            # The SDK doesn't derive the expiry of tokens loaded from storage, so an expired access
            # token would trigger a full browser login on 401 instead of a refresh. Set it here.
            await super()._initialize()
            if self.context.current_tokens is not None:
                self.context.update_token_expiry(self.context.current_tokens)
            # Refresh needs the real token endpoint; in a fresh process the SDK only discovers it
            # after a 401 and otherwise guesses `/token` (404 on Sunsama). Load the metadata up front.
            if self.context.oauth_metadata is None:
                self.context.oauth_metadata = metadata

    provider = Provider(SERVER_URL, meta, storage, redirect_handler, callback_handler)
    provider.signed_in_via_browser = lambda: cb.done.is_set()
    return provider


AUTH_SERVER = "https://api.sunsama.com"


def _auth_server_metadata():
    """Sunsama's OAuth authorization-server metadata (RFC 8414), or None if unreachable."""
    import httpx
    from mcp.shared.auth import OAuthMetadata
    try:
        r = httpx.get(f"{AUTH_SERVER}/.well-known/oauth-authorization-server", timeout=10)
        r.raise_for_status()
        return OAuthMetadata.model_validate(r.json())
    except Exception:
        return None


def parse_timer(result) -> dict:
    """ReadResourceResult -> {"hasActiveTimer": bool, "activeTimer": {...} | None}."""
    for c in getattr(result, "contents", []) or []:
        text = getattr(c, "text", None)
        if text:
            return json.loads(text)
    raise RuntimeError("Sunsama returned no timer content")


async def read_json_resource(storage: FileTokenStorage, uri: str, interactive: bool = False) -> dict:
    """Read any Sunsama MCP resource that returns JSON text (read-only)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    with storage.locked():
        if not interactive and not storage.has_tokens():
            raise LoginRequired("Sunsama login required: run `uv run behaviornudge sunsama-timer --login` once")
        metadata = storage.auth_metadata()
        if metadata is None and storage.has_tokens() and not interactive:
            raise SunsamaUnreachable("Couldn't load Sunsama's sign-in settings; will retry")
        provider = _provider(storage, interactive, metadata)
        http = create_mcp_http_client(auth=provider)
        async with streamable_http_client(SERVER_URL, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = parse_timer(await session.read_resource(uri))
        if interactive and not provider.signed_in_via_browser():
            import sys
            print("Already signed in to Sunsama; no browser sign-in needed.", file=sys.stderr)
        return result


def fetch_resource(token_path: Path, uri: str) -> dict:
    return asyncio.run(read_json_resource(FileTokenStorage(token_path), uri))


async def read_active_timer(storage: FileTokenStorage, interactive: bool = False) -> dict:
    return await read_json_resource(storage, TIMER_URI, interactive)


def fetch(token_path: Path, interactive: bool = False) -> dict:
    return asyncio.run(read_active_timer(FileTokenStorage(token_path), interactive))
