"""Local ATS-like fixture site that counts every submission (spec §13.5.7).

The safety suite needs a real HTTP surface, not just HTML on disk: the network
guard can only be proven by watching what a browser actually sends. The server
binds loopback on an ephemeral port, serves the fixtures, and records every
POST so a test can assert that autofill produced none.

It is a test helper only. Nothing in ``app/`` imports it.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from urllib.parse import urlparse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "application_site"

INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Fixture ATS</title></head>
<body>
  <h1>Fixture application site</h1>
  <ul>
    <li><a href="/generic.html">generic</a></li>
    <li><a href="/multistep.html">multistep</a></li>
    <li><a href="/adversarial.html">adversarial</a></li>
    <li><a href="/autosubmit.html">auto-submit</a></li>
    <li><a href="/delayed.html">delayed auto-submit</a></li>
    <li><a href="/weird.html">weird selectors</a></li>
    <li><a href="/choices.html">multiple choice</a></li>
    <li><a href="/styled-upload.html">styled upload</a></li>
  </ul>
</body></html>
"""

#: A form whose own handler submits on change: the app must never let the
#: automation trigger it, while a human click on Submit still works.
AUTOSUBMIT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Auto-submit form</title></head>
<body>
  <h1>Backend Engineer</h1>
  <form action="/apply" method="post">
    <p>
      <label for="first_name">First name</label>
      <input id="first_name" name="first_name" autocomplete="given-name" required>
    </p>
    <p>
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="email"
             oninput="fetch('/apply', {method: 'POST',
               headers: {'Content-Type': 'application/x-www-form-urlencoded'},
               body: 'email=' + encodeURIComponent(this.value)})">
    </p>
    <p>
      <label><input id="certify" name="certify" type="checkbox">
        I certify that the information provided is true and complete.</label>
    </p>
    <p><label for="pronouns">Pronouns</label>
      <select id="pronouns" name="pronouns">
        <option value="">Decline to answer</option>
        <option value="she/her">she/her</option>
      </select></p>
    <p><button type="submit" id="submit_application">Submit application</button></p>
  </form>
</body></html>
"""

#: Like AUTOSUBMIT, but the deferred POST fires 2.5 s after the input event, so
#: a short post-fill window would let it through. The guard must be armed for
#: the whole session, not for a few seconds around the write.
DELAYED = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Delayed auto-submit</title></head>
<body>
  <h1>Backend Engineer</h1>
  <form id="application" action="/apply" method="post">
    <p>
      <label for="first_name">First name</label>
      <input id="first_name" name="first_name" autocomplete="given-name">
    </p>
    <p>
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="email">
    </p>
    <p><button type="submit" id="submit_application">Submit application</button></p>
  </form>
  <script>
    var armed = false;
    document.addEventListener('input', function () {
      if (armed) return;
      armed = true;
      setTimeout(function () {
        fetch('/apply', {method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: 'first_name=auto'});
      }, 2500);
    });
  </script>
</body></html>
"""

#: Hostile attribute values: quotes, a selector-looking name and an id that
#: would break an unescaped CSS selector.
WEIRD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Weird selectors</title></head>
<body>
  <form action="/apply" method="post">
    <p>
      <label for='odd"id'>First name</label>
      <input id='odd"id' name='odd"name' autocomplete="given-name">
    </p>
    <p>
      <label for="email">Email</label>
      <input id="email" name="email" type="email">
    </p>
    <p>
      <input type="text" name='a"]input[type=password]' aria-label="Notes">
    </p>
    <p><button type="submit">Submit application</button></p>
  </form>
</body></html>
"""

#: A card question answered with radios whose values differ from the answer a
#: user would store ("yes" must reach an option whose value is "Yes").
CHOICES = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Multiple choice</title></head>
<body>
  <h1>Backend Engineer</h1>
  <form action="/apply" method="post">
    <div class="question-card">
      <div class="application-label">
        <div class="text">Are you authorized to work in the United Kingdom?</div>
      </div>
      <div class="application-field">
        <label><input type="radio" name="auth" value="Yes"><span>Yes</span></label>
        <label><input type="radio" name="auth" value="No"><span>No</span></label>
      </div>
    </div>
    <p><button type="submit">Submit application</button></p>
  </form>
</body></html>
"""

#: The common ATS upload widget: a styled label whose native file input is
#: hidden by CSS. A human clicks the label; the filler sets files directly.
STYLED_UPLOAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Styled upload</title></head>
<body>
  <h1>Backend Engineer</h1>
  <form action="/apply" method="post">
    <p>
      <label for="first_name">First name</label>
      <input id="first_name" name="first_name" autocomplete="given-name">
    </p>
    <label class="upload-button">Attach your resume
      <input type="file" id="resume" name="resume" class="invisible-resume-upload">
    </label>
    <p><button type="submit">Submit application</button></p>
  </form>
</body></html>
"""

LOGIN = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sign in</title></head>
<body>
  <h1>Sign in to apply</h1>
  <form action="/session" method="post">
    <p><label for="username">Email</label>
      <input id="username" name="username" type="email"></p>
    <p><label for="password">Password</label>
      <input id="password" name="password" type="password"></p>
    <button type="submit">Sign in</button>
  </form>
</body></html>
"""

GENERATED = {
    "/": INDEX,
    "/autosubmit.html": AUTOSUBMIT,
    "/delayed.html": DELAYED,
    "/weird.html": WEIRD,
    "/choices.html": CHOICES,
    "/styled-upload.html": STYLED_UPLOAD,
    "/login.html": LOGIN,
}

RECEIPT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Received</title></head>
<body><h1>Application received by the fixture site</h1></body></html>
"""


class FixtureServer(ThreadingHTTPServer):
    """Threaded server carrying the submission log."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.submissions: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str]] = []
        self.lock = threading.Lock()


class FixtureHandler(BaseHTTPRequestHandler):
    server: FixtureServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        with self.server.lock:
            self.server.requests.append(("GET", path))
        if path in GENERATED:
            self._send(GENERATED[path])
            return
        candidate = (FIXTURES / path.lstrip("/")).resolve()
        if candidate.is_file() and candidate.parent == FIXTURES.resolve():
            self._send(candidate.read_text(encoding="utf-8"))
            return
        self._send("<h1>Not found</h1>", status=404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        with self.server.lock:
            self.server.submissions.append((path, body))
            self.server.requests.append(("POST", path))
        self._send(RECEIPT)

    def _send(self, html: str, *, status: int = 200) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep test output quiet; requests are recorded in ``server.requests``."""


class FixtureApplicationServer:
    """Context manager around the fixture site."""

    def __init__(self) -> None:
        self._server = FixtureServer()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> FixtureApplicationServer:
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fixture-ats", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def submissions(self) -> list[tuple[str, str]]:
        with self._server.lock:
            return list(self._server.submissions)

    @property
    def requests(self) -> list[tuple[str, str]]:
        with self._server.lock:
            return list(self._server.requests)

    def reset(self) -> None:
        with self._server.lock:
            self._server.submissions.clear()
            self._server.requests.clear()
