"""Forwards an agent's API requests and adds the API key, so the key never enters the agent's container.

Runs in its own small container next to the campaign container (see ``DockerBackend``), with
the standard library only:

    AURORA_UPSTREAM=https://openrouter.ai/api AURORA_KEY=sk-... AURORA_TOKEN=<random> \\
        python3 key_proxy.py

The agent is given ``ANTHROPIC_BASE_URL=http://aurora-proxy:8080`` and the campaign's random
token instead of the key.  Requests that do not carry the token, or that are not API calls
(``/v1/...``), are refused.  Responses, including streamed ones, are passed through as they come.
"""

from __future__ import annotations

import hmac
import http.client
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = urllib.parse.urlsplit(os.environ.get("AURORA_UPSTREAM", "https://openrouter.ai/api"))
KEY = os.environ.get("AURORA_KEY", "")
TOKEN = os.environ.get("AURORA_TOKEN", "")
# headers not passed on: hop-by-hop ones, and the agent's own credentials
_DROP = {"host", "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
         "proxy-authorization", "proxy-connection", "authorization", "x-api-key", "content-length"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"     # the body ends when the connection closes: streams pass as they are

    def _authorised(self) -> bool:
        given = self.headers.get("x-api-key") or self.headers.get("authorization", "").removeprefix("Bearer ")
        return bool(TOKEN) and hmac.compare_digest(given.strip(), TOKEN)

    def _forward(self) -> None:
        if not self.path.startswith("/v1/") or not self._authorised():
            self.send_error(403, "not allowed")
            return
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _DROP}
        headers["Authorization"] = f"Bearer {KEY}"
        conn_cls = http.client.HTTPSConnection if UPSTREAM.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(UPSTREAM.netloc, timeout=600)
        try:
            conn.request(self.command, UPSTREAM.path.rstrip("/") + self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in _DROP:
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError as exc:
            try:
                self.send_error(502, f"upstream: {exc}")
            except OSError:
                pass
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = _forward

    def log_message(self, fmt: str, *args) -> None:   # no request log: it would carry nothing useful
        pass


def main() -> None:
    if not KEY or not TOKEN:
        sys.exit("AURORA_KEY and AURORA_TOKEN must be set")
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("AURORA_PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
