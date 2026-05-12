#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


CLIENT_ID = os.environ["X_CLIENT_ID"]
CLIENT_SECRET = os.environ.get("X_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("X_REDIRECT_URI", "http://127.0.0.1:8765/callback")
SCOPES = os.environ.get("X_SCOPES", "tweet.read users.read bookmark.read offline.access")
OUT = Path(os.environ.get("X_TOKEN_OUT", ".x-user-token.json"))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def build_basic_auth() -> str:
    raw = f"{urllib.parse.quote(CLIENT_ID)}:{urllib.parse.quote(CLIENT_SECRET)}".encode()
    return "Basic " + base64.b64encode(raw).decode()


code_verifier = b64url(secrets.token_bytes(32))
code_challenge = b64url(hashlib.sha256(code_verifier.encode()).digest())
state = secrets.token_urlsafe(24)
done = threading.Event()
result: dict | None = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        global result
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("state", [""])[0] != state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid state")
            done.set()
            return
        code = query.get("code", [""])[0]
        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code")
            done.set()
            return
        try:
            result = exchange_code(code)
            OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(OUT, 0o600)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"Token saved to {OUT}. You can close this tab.".encode())
        except Exception as exc:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(exc).encode())
        finally:
            done.set()


def exchange_code(code: str) -> dict:
    body = urllib.parse.urlencode(
        {
            "code": code,
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        }
    ).encode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "tg-archive-bot-oauth/0.1",
    }
    if CLIENT_SECRET:
        headers["Authorization"] = build_basic_auth()
    req = urllib.request.Request("https://api.x.com/2/oauth2/token", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        },
        quote_via=urllib.parse.quote,
    )
    auth_url = f"https://twitter.com/i/oauth2/authorize?{params}"
    print("Open this URL and authorize:", flush=True)
    print(auth_url, flush=True)
    print(f"Waiting on {REDIRECT_URI} ...", flush=True)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    server = HTTPServer(("127.0.0.1", 8765), Handler)
    while not done.is_set():
        server.handle_request()
    if result:
        print(f"Saved token JSON to {OUT}", flush=True)


if __name__ == "__main__":
    main()
