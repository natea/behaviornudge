"""Read the Sunsama active timer through Sunsama's official remote MCP server.

https://api.sunsama.com/mcp uses OAuth. The first run (`behaviornudge sunsama-timer --login`)
opens a browser to sign in; tokens are cached in data/sunsama_oauth.json (mode 600, git-ignored)
and refreshed automatically. This module only ever *reads* `sunsama://active-timer`.
"""

from __future__ import annotations

import asyncio
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
        from mcp.shared.auth import OAuthToken
        d = self._load().get("tokens")
        return OAuthToken(**d) if d else None

    async def set_tokens(self, tokens) -> None:
        d = self._load()
        d["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
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


def _provider(storage: FileTokenStorage, interactive: bool):
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
    return OAuthClientProvider(SERVER_URL, meta, storage, redirect_handler, callback_handler)


def parse_timer(result) -> dict:
    """ReadResourceResult -> {"hasActiveTimer": bool, "activeTimer": {...} | None}."""
    for c in getattr(result, "contents", []) or []:
        text = getattr(c, "text", None)
        if text:
            return json.loads(text)
    raise RuntimeError("Sunsama returned no timer content")


async def read_active_timer(storage: FileTokenStorage, interactive: bool = False) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    if not interactive and not storage.has_tokens():
        raise LoginRequired("Sunsama login required: run `uv run behaviornudge sunsama-timer --login` once")
    http = create_mcp_http_client(auth=_provider(storage, interactive))
    async with streamable_http_client(SERVER_URL, http_client=http) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            return parse_timer(await session.read_resource(TIMER_URI))


def fetch(token_path: Path, interactive: bool = False) -> dict:
    return asyncio.run(read_active_timer(FileTokenStorage(token_path), interactive))
