"""A one-shot local web server for the manifest flow (manifest.py).

It serves one page that POSTs the manifest to GitHub, and receives GitHub's
redirect back with the one-time code. A random `state` value travels with
the request and must come back unchanged, so a stray or forged redirect to
the callback can't make us create or save anything.

The state alone isn't enough: every local user can reach 127.0.0.1, and the
start page that carries it used to be served to anyone who asked. Another
user could read it and finish the flow with an App of their own, which we'd
then save (D132). So the flow is bound to the browser the CLI opens: the
start page needs a launch token from the URL we open, is served once, and
sets a cookie the callback must bring back; requests for any other Host are
refused (DNS rebinding); and the first good callback is the only one.
"""

import html
import json
import secrets
import threading
from collections.abc import Callable
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.github.manifest import AppCredentials, build_manifest, new_app_url, save_credentials

Convert = Callable[[str], AppCredentials]  # one-time code -> the new App's credentials
Response = tuple[int, str, dict[str, str]]  # status, HTML, extra headers

COOKIE = "codelens_registration"


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
        # In the URL the CLI opens (start_url): only that browser gets the page.
        self.launch = secrets.token_urlsafe(16)
        # Set on that browser with the page; the callback must bring it back.
        self._binding = secrets.token_urlsafe(16)
        self._served = False
        self._lock = threading.Lock()
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

    @property
    def start_url(self) -> str:
        """What the CLI opens in the user's browser."""
        return f"{self.url}/?t={self.launch}"

    def host_ok(self, host: str | None) -> bool:
        """Only our own address: a page on another site that rebinds its name
        to 127.0.0.1 still sends its own name as the Host."""
        port = self._server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def open_start(self, query: dict[str, list[str]], cookie: str | None) -> Response:
        """The start page, which carries the state: for the launch token, once,
        or again for the browser that already has it (a reload)."""
        with self._lock:
            if cookie is not None and secrets.compare_digest(cookie, self._binding):
                return 200, self.start_page(), {}
            if query.get("t") != [self.launch] or self.done.is_set():
                return 403, _page("Refused", "<p>Open the link the command printed.</p>"), {}
            if self._served:
                # Someone else used our link before this browser did: stop, so
                # whatever they started can't finish and be saved.
                self.error = "the start page was opened by another client first; run it again"
                self.done.set()
                return 409, _page("Stopped", f"<p>{html.escape(self.error)}.</p>"), {}
            self._served = True
            cookie_header = f"{COOKIE}={self._binding}; HttpOnly; SameSite=Lax; Path=/"
            return 200, self.start_page(), {"Set-Cookie": cookie_header}

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

    def callback(self, query: dict[str, list[str]], cookie: str | None = None) -> Response:
        with self._lock:
            bound = cookie is not None and secrets.compare_digest(cookie, self._binding)
            if self.done.is_set() or not bound:
                return 400, _page("Refused", "<p>Unexpected callback.</p>"), {}
            if query.get("state") != [self.state] or not query.get("code"):
                return (
                    400,
                    _page("Refused", "<p>Unexpected callback: the state did not match.</p>"),
                    {},
                )
            try:
                creds = self._convert(query["code"][0])
                key_path, env_path = save_credentials(creds, self._secrets_dir)
            except Exception as exc:  # shown to the user who ran the command
                self.error = str(exc)
                self.done.set()
                return 500, _page("Failed", f"<p>{html.escape(self.error)}</p>"), {}
            self.saved = (creds, key_path, env_path)
            self.done.set()
        install = html.escape(f"{creds.html_url}/installations/new", quote=True)
        page = _page(
            f"Created {html.escape(creds.slug)}",
            f"""<p>The App's private key and webhook secret were saved to
<code>{html.escape(str(env_path.parent))}</code>. You can close this tab.</p>
<p>Next: <a href="{install}">install the App</a> on a test repository.</p>""",
        )
        return 200, page, {}


def _handler(registration: Registration) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if not registration.host_ok(self.headers.get("Host")):
                self._send(403, _page("Refused", ""), {})
                return
            url = urlparse(self.path)
            query, cookie = parse_qs(url.query), self._cookie()
            if url.path == "/":
                self._send(*registration.open_start(query, cookie))
            elif url.path == "/callback":
                self._send(*registration.callback(query, cookie))
            else:
                self._send(404, _page("Not found", ""), {})

        def _cookie(self) -> str | None:
            jar: SimpleCookie = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except CookieError:
                return None
            morsel = jar.get(COOKIE)
            return morsel.value if morsel is not None else None

        def _send(self, status: int, body: str, headers: dict[str, str]) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for name, value in headers.items():
                self.send_header(name, value)
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
