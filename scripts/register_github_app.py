"""One-time, HUMAN-IN-THE-LOOP registration of Skylize's GitHub App.

WHY THIS IS A SCRIPT AN OPERATOR RUNS AND NOT AN AUTOMATED CALL
--------------------------------------------------------------
It is not a design preference. GitHub offers no non-interactive path to create an
App, and this was re-verified before this script was written.

    `[LIVE-VERIFIED]` 2026-09-05, docs.github.com/en/apps/sharing-github-apps/
    registering-a-github-app-from-a-manifest. The manifest flow requires all
    three of: (1) redirecting a PERSON in a browser to
    github.com/settings/apps/new with the manifest POSTed as a form parameter,
    (2) that person clicking "Create GitHub App" on GitHub's own page, and
    (3) exchanging the temporary `code` GitHub redirects back with, via
    POST /app-manifests/{code}/conversions, WITHIN ONE HOUR.

    There is no REST endpoint that creates an App. Probing the only App-creation
    endpoint that exists returns HTTP 404 pointing at "create a GitHub App from a
    manifest" — i.e. the manifest exchange is the sole route in.

So this script does the parts a machine can do (render the manifest, serve the
redirect, exchange the code, report the credentials) and stops at the part a
machine cannot (the human click). That is also a genuine product fact, not just a
build-time inconvenience: Skylize registers ONE App, once, manually. Customers
never register anything — they only *install* the App that already exists, which
is why `github_app_installations` stores an installation id and not an app id.

WHAT THIS SCRIPT DELIBERATELY DOES NOT DO
-----------------------------------------
It does not write the private key to disk, and it does not put it in an env file.
The key authenticates Skylize to EVERY customer installation at once (it is
platform-level — see `app/github/keys.py`), so it is printed once, to stdout, with
instructions to place it in the secrets manager. Persisting it here would leave the
platform's highest-value secret in a developer's working tree, which is precisely
the failure this codebase's key-custody discipline exists to avoid.

USAGE
    python scripts/register_github_app.py                 # print instructions
    python scripts/register_github_app.py --serve         # capture + exchange

The --serve mode binds localhost only, for the duration of the one exchange, and
exits immediately afterwards.
"""

from __future__ import annotations

import argparse
import http.server
import json
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "config" / "github_app_manifest.json"
GITHUB_NEW_APP_URL = "https://github.com/settings/apps/new"
GITHUB_ORG_NEW_APP_URL = "https://github.com/organizations/{org}/settings/apps/new"
CONVERSION_URL = "https://api.github.com/app-manifests/{code}/conversions"

#: Permissions that must never appear in the manifest. Kept here as well as in the
#: unit test so an operator running this script against a hand-edited manifest is
#: stopped before a browser is ever opened. Tier 1 is only structural if every
#: path that could widen it refuses to.
FORBIDDEN_PERMISSIONS = ("administration", "secrets", "actions", "environments")


def load_manifest(*, url: str, redirect_url: str, name: str | None) -> dict[str, Any]:
    """Read the manifest, strip the README, substitute operator-supplied values."""
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest = {k: v for k, v in raw.items() if not k.startswith("_")}
    manifest["url"] = url
    manifest["redirect_url"] = redirect_url
    if name:
        manifest["name"] = name

    perms = manifest.get("default_permissions") or {}
    offending = sorted(set(perms) & set(FORBIDDEN_PERMISSIONS))
    if offending:
        raise SystemExit(
            f"REFUSING TO REGISTER: manifest requests {offending}.\n"
            "integration_inputs.md 2.4 Tier 1 (APPROVED 2026-09-05) forbids "
            "these permissions. 'administration' would allow deleting a "
            "repository and editing the very branch-protection rulesets Tier 2 "
            "depends on; the others allow tampering with CI. Their absence is the "
            "entire Tier 1 guarantee.\n"
            f"Fix {MANIFEST_PATH} or get a new 2.4 approval."
        )
    return manifest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def print_instructions(manifest: dict[str, Any], target_url: str) -> None:
    print("=" * 78)
    print("Skylize GitHub App registration - MANUAL STEP REQUIRED")
    print("=" * 78)
    print()
    print("GitHub has no API to create an App. A person must click 'Create GitHub")
    print("App' in a browser (verified 2026-09-05). This is expected and is a")
    print("one-time operation for the whole platform.")
    print()
    print("Permissions this manifest requests (Tier 1 - note what is ABSENT):")
    for k, v in sorted((manifest.get("default_permissions") or {}).items()):
        print(f"    {k}: {v}")
    print("    (no administration -> cannot delete repos or edit branch protection)")
    print("    (no secrets/actions -> cannot touch CI credentials)")
    print()
    print("1. POST this manifest to GitHub. Easiest: open the URL below, paste the")
    print("   manifest into a form, and submit. Or use --serve to have this script")
    print("   host the form and capture the result automatically.")
    print()
    print(f"   Target: {target_url}")
    print()
    print("2. GitHub redirects back with a ?code=... parameter, valid ONE HOUR.")
    print("3. Exchange it:")
    print("     curl -X POST -H 'Accept: application/vnd.github+json' \\")
    print("       https://api.github.com/app-manifests/<CODE>/conversions")
    print()
    print("4. From the response, store in the SECRETS MANAGER (not a file):")
    print("     SKYLIZE_GITHUB_APP_ID              <- 'id'")
    print("     SKYLIZE_GITHUB_APP_PRIVATE_KEY_PEM <- 'pem'")
    print("     SKYLIZE_GITHUB_APP_SLUG            <- 'slug'  (cosmetic)")
    print()
    print("   The 'pem' authenticates Skylize to EVERY customer installation at")
    print("   once. Treat it like the governance signing key.")
    print()
    print("Manifest that will be submitted:")
    print(json.dumps(manifest, indent=2))
    print("=" * 78)


def exchange_code(code: str) -> dict[str, Any]:
    """Trade the temporary manifest code for the App's credentials."""
    req = urllib.request.Request(
        CONVERSION_URL.format(code=urllib.parse.quote(code)),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "skylize-app-registration",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed host
            return dict(json.load(resp))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise SystemExit(
            f"code exchange failed: HTTP {exc.code}\n{body}\n\n"
            "The code is single-use and expires one hour after GitHub issued it. "
            "If it expired, start the flow again."
        ) from exc


def report_credentials(payload: dict[str, Any]) -> None:
    pem = payload.get("pem") or ""
    print()
    print("=" * 78)
    print("App registered. Store these in your secrets manager NOW.")
    print("They are printed once and deliberately NOT written to disk.")
    print("=" * 78)
    print(f"SKYLIZE_GITHUB_APP_ID={payload.get('id')}")
    print(f"SKYLIZE_GITHUB_APP_SLUG={payload.get('slug')}")
    print(f"  (client_id: {payload.get('client_id')})")
    print(f"  (webhook_secret: {'present' if payload.get('webhook_secret') else 'none'}"
          " - needed by the Q2.4e webhook pass, not by this one)")
    print()
    print("SKYLIZE_GITHUB_APP_PRIVATE_KEY_PEM (whole block, newlines included):")
    print(pem if pem else "  !! NO PEM IN RESPONSE - registration incomplete")
    print("=" * 78)
    print()
    print("Granted permissions as GitHub recorded them:")
    print(json.dumps(payload.get("permissions") or {}, indent=2))
    perms = payload.get("permissions") or {}
    unexpected = sorted(set(perms) & set(FORBIDDEN_PERMISSIONS))
    if unexpected:
        print()
        print(f"WARNING: GitHub recorded {unexpected}, which Tier 1 forbids.")
        print("The App was edited during creation. Delete it and re-register.")


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves the auto-submitting manifest form, then captures the redirect."""

    manifest_json: str = "{}"
    target_url: str = GITHUB_NEW_APP_URL
    captured_code: str | None = None

    def log_message(self, *_: Any) -> None:  # keep stdout for our own output
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            type(self).captured_code = params["code"][0]
            self._respond(
                "<h2>Received. Return to your terminal.</h2>"
                "<p>This window can be closed.</p>"
            )
            return
        # Auto-submitting form: the manifest must reach GitHub as a POST parameter.
        body = (
            "<h3>Submitting Skylize App manifest to GitHub...</h3>"
            f'<form id="f" method="post" action="{self.target_url}">'
            f'<input type="hidden" name="manifest" value=\''
            f"{self.manifest_json.replace(chr(39), '&#39;')}'>"
            "</form><script>document.getElementById('f').submit()</script>"
        )
        self._respond(body)

    def _respond(self, html: str) -> None:
        raw = f"<!doctype html><meta charset=utf-8>{html}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve_and_exchange(*, org: str | None, url: str, name: str | None) -> None:
    port = _free_port()
    redirect = f"http://127.0.0.1:{port}/callback"
    manifest = load_manifest(url=url, redirect_url=redirect, name=name)
    target = GITHUB_ORG_NEW_APP_URL.format(org=org) if org else GITHUB_NEW_APP_URL

    _Handler.manifest_json = json.dumps(manifest)
    _Handler.target_url = target
    _Handler.captured_code = None

    httpd = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print_instructions(manifest, target)
    print()
    print(f"Local capture server: http://127.0.0.1:{port}/")
    print("Open that URL in a browser. It will submit the manifest to GitHub;")
    print("click 'Create GitHub App', and this script will finish automatically.")
    print("Ctrl-C to abort.")
    try:
        while _Handler.captured_code is None:
            threading.Event().wait(0.5)
    except KeyboardInterrupt:
        httpd.shutdown()
        raise SystemExit("aborted before GitHub returned a code.") from None

    code = _Handler.captured_code
    httpd.shutdown()
    print(f"\nReceived code ({len(code)} chars). Exchanging...")
    report_credentials(exchange_code(code))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--serve",
        action="store_true",
        help="host the manifest form on localhost and exchange the returned code",
    )
    ap.add_argument("--org", help="register under this organization instead of the user")
    ap.add_argument("--url", default="https://skylize.com", help="App homepage URL")
    ap.add_argument("--name", help="override the App name")
    ap.add_argument("--code", help="exchange a code you already captured, then exit")
    args = ap.parse_args(argv)

    if args.code:
        report_credentials(exchange_code(args.code))
        return 0
    if args.serve:
        serve_and_exchange(org=args.org, url=args.url, name=args.name)
        return 0

    manifest = load_manifest(url=args.url, redirect_url=f"{args.url}/setup", name=args.name)
    target = GITHUB_ORG_NEW_APP_URL.format(org=args.org) if args.org else GITHUB_NEW_APP_URL
    print_instructions(manifest, target)
    print("Re-run with --serve to have this script capture the code for you.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
