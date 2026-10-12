"""A one-shot local web server for the manifest flow (manifest.py).

It serves one page that POSTs the manifest to GitHub, and receives GitHub's
redirect back with the one-time code. A random `state` value travels with
the request and must come back unchanged, so a stray or forged redirect to
the callback can't make us create or save anything.
"""

import html
import json
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.github.manifest import AppCredentials, build_manifest, new_app_url, save_credentials

Convert = Callable[[str], AppCredentials]  # one-time code -> the new App's credentials


class Registration:
    def __init__(
        self,
        *,
        name: str,
        webhook_url: str,
        convert: Convert,
        secrets_dir: Path,
        org: str | None = None,
        port: int = 0,  # 0: any free port
    ) -> None:
        self.state = secrets.token_urlsafe(16)
        self.done = threading.Event()
        self.saved: tuple[AppCredentials, Path, Path] | None = None
        self.error: str | None = None
        self._convert = convert
        self._secrets_dir = secrets_dir
        self._server = ThreadingHTTPServer(("127.0.0.1", port), _handler(self))
        self.manifest = build_manifest(
            name=name, webhook_url=webhook_url, redirect_url=f"{self.url}/callback"
        )
        self._action = f"{new_app_url(org)}?state={self.state}"

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def start_page(self) -> str:
        manifest = html.escape(json.dumps(self.manifest), quote=True)
        return _page(
            "Create the CodeLens GitHub App",
            f"""<p>This sends CodeLens's App manifest to GitHub, which asks you to
confirm the App's name and creates it with exactly these permissions:
<code>{html.escape(json.dumps(self.manifest["default_permissions"]))}</code>.</p>
<form id="manifest" method="post" action="{html.escape(self._action, quote=True)}">
<input type="hidden" name="manifest" value="{manifest}">
<button type="submit">Continue to GitHub</button>
</form>""",
        )

    def callback(self, query: dict[str, list[str]]) -> tuple[int, str]:
        if query.get("state") != [self.state] or not query.get("code"):
            return 400, _page("Refused", "<p>Unexpected callback: the state did not match.</p>")
        try:
            creds = self._convert(query["code"][0])
            key_path, env_path = save_credentials(creds, self._secrets_dir)
        except Exception as exc:  # shown to the user who ran the command
            self.error = str(exc)
            self.done.set()
            return 500, _page("Failed", f"<p>{html.escape(self.error)}</p>")
        self.saved = (creds, key_path, env_path)
        self.done.set()
        install = html.escape(f"{creds.html_url}/installations/new", quote=True)
        return 200, _page(
            f"Created {html.escape(creds.slug)}",
            f"""<p>The App's private key and webhook secret were saved to
<code>{html.escape(str(env_path.parent))}</code>. You can close this tab.</p>
<p>Next: <a href="{install}">install the App</a> on a test repository.</p>""",
        )


def _handler(registration: Registration) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, registration.start_page())
            elif url.path == "/callback":
                self._send(*registration.callback(parse_qs(url.query)))
            else:
                self._send(404, _page("Not found", ""))

        def _send(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass  # the callback URL carries the one-time code: keep it out of the terminal

    return Handler


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head>"
        f"<body style='font-family:system-ui;max-width:40rem;margin:3rem auto'>"
        f"<h1>{title}</h1>{body}</body></html>"
    )
