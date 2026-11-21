#!/usr/bin/env python
"""CFO Test -- repeatable LOCAL demo run (register -> governed execute -> HITL
approve -> audit token -> kill switch).

    py -3.12 scripts/demo/cfo_test_local.py                 # run, stop uvicorn, leave DB up
    py -3.12 scripts/demo/cfo_test_local.py --keep-running  # leave backend UP (console demo)
    py -3.12 scripts/demo/cfo_test_local.py --teardown      # remove everything this kit made

What it does, in order (every step prints EXPECTED vs ACTUAL and PASS/FAIL; the
first mismatch skips the rest and the process exits non-zero):

  compose up (postgres+redis, own project) -> alembic upgrade head -> tenant SQL
  seed -> uvicorn (DEV_AUTH=false, demo LLM, governed org, ephemeral keys) ->
  register -> login -> cfo_agent governed execute (202 deferred_to_human) ->
  GET /hitl -> approve -> deliverable (recomputed total + flags) -> GET /audit
  (governance_token_id populated) -> issue API key -> kill switch engage ->
  approve (502 "kill switch engaged") -> disengage -> approve (200, recovered).

Hard rules this script enforces on itself:
  * NO real external call. The child process env is SCRUBBED of every inherited
    SKYLIZE_* variable and SKYLIZE_ANTHROPIC_API_KEY is forced empty, so the LLM
    is the deterministic DemoLLMAdapter (llm_demo_mode) -- never real Claude.
    No Stripe / Google / Slack / GitHub credential is ever set.
  * Signing key, Fernet key and JWT secret are generated IN MEMORY per run and
    handed to the uvicorn child through its environment only. They are written to
    no file, never printed, and die with that process.
  * The only literals below are PUBLIC local defaults (the compose `localdev` /
    `appdev` DB passwords from infra/docker-compose.yml, and a demo login).
  * stdlib only, plus `cryptography` (already a dependency) to mint ephemeral keys.
  * Runs the code of the checkout this file lives in: PYTHONPATH is pinned to
    <repo>/src and the resolved `skylize.__file__` is asserted before boot, because
    an editable install elsewhere on the machine would otherwise win silently.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILE = Path(__file__).resolve().with_name("docker-compose.demo.yml")
PROJECT = "skylize-demokit"
STATE_DIR = Path(tempfile.gettempdir()) / "skylize-demokit-state"  # OUTSIDE the repo
PID_FILE = STATE_DIR / "uvicorn.pid"
LOG_FILE = STATE_DIR / "uvicorn.log"
CONSOLE_PID_FILE = STATE_DIR / "console.pid"
CONSOLE_LOG_FILE = STATE_DIR / "console.log"

PG_PORT, REDIS_PORT, API_PORT, CONSOLE_PORT = 55432, 56379, 58000, 53000
API = f"http://127.0.0.1:{API_PORT}"
CONSOLE = f"http://127.0.0.1:{CONSOLE_PORT}"
# PUBLIC local defaults (same as infra/docker-compose.yml). Not secrets.
ADMIN_DSN = f"postgresql://skylize:localdev@127.0.0.1:{PG_PORT}/skylize"
APP_DSN = f"postgresql://skylize_app:appdev@127.0.0.1:{PG_PORT}/skylize"
REDIS_URL = f"redis://127.0.0.1:{REDIS_PORT}"

ORG = "demo-cfo"
EMAIL = "cfo.demo@acme.com"
# Public demo logins (local only, destroyed by --teardown). Overridable.
DEMO_PASSWORD = os.environ.get("SKYLIZE_DEMOKIT_PASSWORD", "DemoKit-Local-2026")
CONSOLE_PASSWORD = os.environ.get("SKYLIZE_DEMOKIT_CONSOLE_PASSWORD", "DemoKit-Console-2026")

AGENT = "cfo_agent"
CFO_INPUT = {
    "department": "marketing",
    "period": "2026-Q3",
    "line_items": [
        {"category": "paid_media", "amount": 60_000.0},
        {"category": "tooling", "amount": 25_000.0},
        {"category": "events", "amount": 15_000.0},
    ],
}
EXPECTED_TOTAL = "$100,000.00"


# --------------------------------------------------------------------------- util
def say(msg: str = "") -> None:
    print(msg, flush=True)


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n" + "\n".join(tail))
    return proc


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args], check=check)


def scrubbed_env() -> dict[str, str]:
    """Inherited environment minus anything that could reach a real service.

    Drops every SKYLIZE_* variable (a developer shell may hold real keys) and the
    common third-party credential names. pydantic-settings lets a process env var
    beat a .env file, and the child also runs with cwd=STATE_DIR (no .env there).
    """
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("SKYLIZE_")}
    for k in list(env):
        ku = k.upper()
        if any(tag in ku for tag in ("STRIPE", "ANTHROPIC", "OPENAI", "SLACK", "GOOGLE_APPLICATION",
                                     "GITHUB_APP", "LANGFUSE")):
            del env[k]
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO / "src")
    return env


def http_full(method: str, path: str, *, body: dict | None = None, headers: dict | None = None,
              timeout: int = 60, base: str = API) -> tuple[int, dict[str, str], object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw, hdrs = resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        status, raw, hdrs = exc.code, exc.read(), {k.lower(): v for k, v in exc.headers.items()}
    text = raw.decode("utf-8", errors="replace")
    try:
        return status, hdrs, (json.loads(text) if text else None)
    except json.JSONDecodeError:
        return status, hdrs, text


def http(method: str, path: str, *, body: dict | None = None, headers: dict | None = None,
         timeout: int = 60, base: str = API) -> tuple[int, object]:
    status, _hdrs, parsed = http_full(method, path, body=body, headers=headers, timeout=timeout, base=base)
    return status, parsed


# ------------------------------------------------------------------- step ledger
RESULTS: list[tuple[str, str, str, str]] = []  # (id, verdict, expected, actual)
_ABORTED = False


def check(step: str, expected: str, actual: str, ok: bool) -> bool:
    global _ABORTED
    verdict = "PASS" if ok else "FAIL"
    RESULTS.append((step, verdict, expected, actual))
    say(f"[{verdict}] {step}")
    say(f"        expected: {expected}")
    say(f"        actual  : {actual}")
    if not ok:
        _ABORTED = True
    return ok


def cut(text: object, n: int = 160) -> str:
    s = text if isinstance(text, str) else json.dumps(text, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


# ------------------------------------------------------------------- teardown
def uvicorn_stop() -> None:
    """Stop the backend AND the console dev server, if either was started by this kit."""
    for pid_file in (CONSOLE_PID_FILE, PID_FILE):
        if pid_file.exists():
            pid = pid_file.read_text().strip()
            if pid.isdigit():
                # /T kills the whole tree (uvicorn / npm may sit behind launcher processes).
                subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
            pid_file.unlink(missing_ok=True)


def teardown(quiet: bool = False) -> bool:
    """Remove the uvicorn process, the compose project (+ volumes) and the state dir.
    Returns True when verification finds nothing left behind."""
    uvicorn_stop()
    compose("down", "-v", "--remove-orphans", check=False)
    shutil.rmtree(STATE_DIR, ignore_errors=True)

    leftovers: list[str] = []
    ps = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}",
              "--format", "{{.Names}}"], check=False).stdout.split()
    leftovers += [f"container:{n}" for n in ps]
    vols = run(["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={PROJECT}",
                "--format", "{{.Name}}"], check=False).stdout.split()
    leftovers += [f"volume:{n}" for n in vols]
    nets = run(["docker", "network", "ls", "--filter", f"label=com.docker.compose.project={PROJECT}",
                "--format", "{{.Name}}"], check=False).stdout.split()
    leftovers += [f"network:{n}" for n in nets]
    if STATE_DIR.exists():
        leftovers.append(f"state-dir:{STATE_DIR}")
    for port in (API_PORT, CONSOLE_PORT):
        if port_open(port):
            leftovers.append(f"listener:{port}")
    if not quiet:
        say("teardown verification:")
        say(f"  containers (project={PROJECT}): {len(ps)}")
        say(f"  volumes    (project={PROJECT}): {len(vols)}")
        say(f"  networks   (project={PROJECT}): {len(nets)}")
        say(f"  temp state dir exists          : {STATE_DIR.exists()}")
        say(f"  API port {API_PORT} listening        : {port_open(API_PORT)}")
        say(f"  console port {CONSOLE_PORT} listening    : {port_open(CONSOLE_PORT)}")
        say("  RESULT: " + ("CLEAN" if not leftovers else "LEFTOVERS " + ", ".join(leftovers)))
    return not leftovers


def port_open(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ------------------------------------------------------------------- bring-up
def ephemeral_secrets() -> dict[str, str]:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = ec.generate_private_key(ec.SECP384R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM": pem,
        "SKYLIZE_CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "SKYLIZE_JWT_SECRET": secrets.token_urlsafe(48),
    }


def bring_up() -> bool:
    py = ["py", "-3.12"] if shutil.which("py") else [sys.executable]
    env = scrubbed_env()

    # 0. Which checkout is going to run?
    probe = run([*py, "-c", "import skylize;print(skylize.__file__)"], env=env, cwd=STATE_DIR, check=False)
    resolved = probe.stdout.strip().splitlines()[-1] if probe.stdout.strip() else probe.stderr.strip()
    ok = Path(resolved).resolve().is_relative_to(REPO / "src") if resolved.endswith("__init__.py") else False
    if not check("S0.0 code under test is THIS checkout", f"skylize imported from {REPO / 'src'}",
                 resolved, ok):
        return False

    # 1. docker compose up (postgres + redis), wait for healthchecks.
    t = time.time()
    try:
        compose("up", "-d", "--wait", "--wait-timeout", "120")
    except RuntimeError as exc:
        return check("S0.1 compose up (postgres+redis)", "both services healthy", cut(str(exc), 300), False)
    check("S0.1 compose up (postgres+redis)", "both services healthy",
          f"healthy in {time.time() - t:.0f}s (project={PROJECT}, pg:{PG_PORT}, redis:{REDIS_PORT})", True)

    # 2. migrate head as the admin role (migration 0003 creates skylize_app).
    menv = {**env, "SKYLIZE_DB_URL": ADMIN_DSN, "SKYLIZE_APP_DB_PASSWORD": "appdev"}
    t = time.time()
    try:
        run([*py, "-m", "alembic", "-c", str(REPO / "alembic.ini"), "upgrade", "head"],
            cwd=REPO, env=menv, timeout=300)
        head = run([*py, "-m", "alembic", "-c", str(REPO / "alembic.ini"), "current"],
                   cwd=REPO, env=menv, check=False).stdout.strip().splitlines()
    except RuntimeError as exc:
        return check("S0.2 alembic upgrade head", "migrations applied", cut(str(exc), 300), False)
    check("S0.2 alembic upgrade head", "migrations applied",
          f"{time.time() - t:.0f}s, current={cut(head[-1] if head else '?', 60)}", True)

    # 3. tenant seed. Self-serve register is a KNOWN DEFECT: users.org_id has an FK to
    #    tenants(org_id) (migration 0008) and nothing creates the tenant, so register
    #    500s. Same workaround as tests/integration/test_registration_org_claim_pg.py:67-73.
    sql = (f"INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES "
           f"('{ORG}', '{ORG}', 'https://issuer.example') ON CONFLICT (org_id) DO NOTHING;")
    try:
        compose("exec", "-T", "postgres", "psql", "-U", "skylize", "-d", "skylize",
                "-v", "ON_ERROR_STOP=1", "-c", sql)
        n = compose("exec", "-T", "postgres", "psql", "-U", "skylize", "-d", "skylize", "-tA", "-c",
                    f"SELECT count(*) FROM tenants WHERE org_id='{ORG}'").stdout.strip()
    except RuntimeError as exc:
        return check("S0.3 tenant SQL seed", "tenant row exists", cut(str(exc), 300), False)
    if not check("S0.3 tenant SQL seed (users_org_id_fkey workaround)", "1 tenants row for demo org",
                 f"{n} row(s)", n == "1"):
        return False

    # 4. uvicorn: DEV_AUTH=false, demo LLM, governed org, ephemeral keys.
    senv = {
        **env,
        **ephemeral_secrets(),
        "SKYLIZE_BACKEND": "postgres",
        "SKYLIZE_DB_URL": ADMIN_DSN,
        "SKYLIZE_DB_APP_URL": APP_DSN,
        "SKYLIZE_REDIS_URL": REDIS_URL,
        "SKYLIZE_DEV_AUTH": "false",
        "SKYLIZE_LLM_DEMO_MODE": "true",
        "SKYLIZE_ANTHROPIC_API_KEY": "",
        "SKYLIZE_DECISION_ENGINE": "inline",
        "SKYLIZE_DECISION_ENGINE_ORG_IDS": json.dumps([ORG]),
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "wb")
    proc = subprocess.Popen(
        [*py, "-m", "uvicorn", "skylize.edge.gateway:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
        cwd=str(STATE_DIR), env=senv, stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    PID_FILE.write_text(str(proc.pid))
    deadline = time.time() + 90
    status: object = None
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            status, _ = http("GET", "/health", timeout=3)
            if status == 200:
                break
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(1)
    if status != 200:
        tail = LOG_FILE.read_text(errors="replace").strip().splitlines()[-8:]
        return check("S0.4 uvicorn boot (DEV_AUTH=false, demo LLM)", "GET /health 200",
                     f"status={status} exit={proc.poll()} log tail: {cut(' | '.join(tail), 400)}", False)
    return check("S0.4 uvicorn boot (DEV_AUTH=false, demo LLM)", "GET /health 200",
                 f"200 (pid {proc.pid}, 127.0.0.1:{API_PORT}, llm=demo, org governed={ORG})", True)


# ------------------------------------------------------------------- the demo
def demo() -> None:
    # S1 register -----------------------------------------------------------
    st, body = http("POST", "/api/v1/auth/register",
                    body={"org_id": ORG, "email": EMAIL, "password": DEMO_PASSWORD,
                          "display_name": "Demo CFO"})
    roles = body.get("roles") if isinstance(body, dict) else None
    if not check("S1 register owner", "201, roles contain 'owner'",
                 f"{st} roles={roles}", st == 201 and isinstance(roles, list) and "owner" in roles):
        return

    st, body = http("POST", "/api/v1/auth/login", body={"email": EMAIL, "password": DEMO_PASSWORD})
    tok = body.get("access_token") if isinstance(body, dict) else None
    if not check("S2 login -> access JWT", "200 + access_token",
                 f"{st} access_token={'<present, not printed>' if tok else 'MISSING'}", st == 200 and bool(tok)):
        return
    bearer = {"Authorization": f"Bearer {tok}"}

    # S3 governed execute -> deferred ------------------------------------------
    st, body = http("POST", "/api/v1/agents/execute", headers=bearer,
                    body={"agent_id": AGENT, "input": CFO_INPUT})
    hitl_id = body.get("hitl_id") if isinstance(body, dict) else None
    if not check("S3 cfo_agent governed execute", "202 status=deferred_to_human + hitl_id",
                 f"{st} status={body.get('status') if isinstance(body, dict) else cut(body)} hitl_id={hitl_id}",
                 st == 202 and isinstance(body, dict) and body.get("status") == "deferred_to_human" and bool(hitl_id)):
        return

    # S4 HITL queue -------------------------------------------------------------
    st, body = http("GET", "/api/v1/hitl", headers=bearer)
    rows = body.get("data", []) if isinstance(body, dict) else []
    row = next((r for r in rows if r.get("hitl_id") == hitl_id), None)
    if not check("S4 GET /hitl lists the pending item",
                 "200, row present, status=pending, agent_id=cfo_agent",
                 f"{st} total={body.get('pagination', {}).get('total') if isinstance(body, dict) else '?'} "
                 f"row={cut({k: row.get(k) for k in ('status', 'agent_id', 'trigger_reason')} if row else None)}",
                 st == 200 and row is not None and row.get("status") == "pending"
                 and row.get("agent_id") == AGENT):
        return

    # S5 approve -> deliverable ---------------------------------------------------
    st, body = http("POST", f"/api/v1/hitl/{hitl_id}/approve", headers=bearer, body={"note": "demo: budget reviewed"})
    deliverable_id = body.get("deliverable_id") if isinstance(body, dict) else None
    if not check("S5 approve (human clears governance escalation)", "200 status=approved + deliverable_id",
                 f"{st} {cut(body, 140)}",
                 st == 200 and isinstance(body, dict) and body.get("status") == "approved" and bool(deliverable_id)):
        return

    # S6 deliverable: total + flags are RECOMPUTED in Python, not the model's ---------
    st, body = http("GET", f"/api/v1/deliverables/{deliverable_id}", headers=bearer)
    md = body.get("content_markdown", "") if isinstance(body, dict) else ""
    ok = (st == 200 and f"**Total:** {EXPECTED_TOTAL}" in md and "paid_media" in md)
    flag_line = next((ln.strip() for ln in md.splitlines() if "paid_media" in ln), "<none>")
    if not check("S6 deliverable total + flags",
                 f"200, markdown has '**Total:** {EXPECTED_TOTAL}' and a paid_media concentration flag",
                 f"{st} total_ok={f'**Total:** {EXPECTED_TOTAL}' in md} flag={cut(flag_line, 110)}", ok):
        return
    say("        --- deliverable markdown ---")
    for ln in md.splitlines():
        say("        | " + ln)
    say("        ----------------------------")

    # S7 audit: governance_token_id populated ----------------------------------------
    st, body = http("GET", "/api/v1/audit?limit=200", headers=bearer)
    entries = body.get("entries", []) if isinstance(body, dict) else []
    with_token = [e for e in entries if e.get("governance_token_id")]
    exec_rows = [e for e in with_token if e.get("action_type") == "agent.executed"]
    kinds = sorted({e["action_type"] for e in entries})
    if not check("S7 GET /audit carries governance_token_id",
                 "200, an 'agent.executed' row with governance_token_id set",
                 f"{st} entries={len(entries)} with_token={len(with_token)} "
                 f"agent.executed_with_token={len(exec_rows)} types={cut(kinds, 120)}",
                 st == 200 and len(exec_rows) >= 1):
        return

    # S8 second deferral so there is something to approve once the switch is on ------
    st, body = http("POST", "/api/v1/agents/execute", headers=bearer,
                    body={"agent_id": AGENT, "input": {**CFO_INPUT, "period": "2026-Q4"}})
    hitl2 = body.get("hitl_id") if isinstance(body, dict) else None
    if not check("S8 second cfo_agent request deferred (pre kill switch)", "202 deferred_to_human + hitl_id",
                 f"{st} {cut(body, 120)}", st == 202 and bool(hitl2)):
        return

    # S9 issue an API key (the kill-switch route is API-key/owner only, not JWT) ------
    st, body = http("POST", "/api/v1/api-keys", headers=bearer,
                    body={"name": "demokit-killswitch", "scopes": ["owner"], "expires_in_days": 1})
    api_key = body.get("api_key") if isinstance(body, dict) else None
    if not check("S9 issue owner API key", "201 + api_key (shown once)",
                 f"{st} api_key={'<present, not printed>' if api_key else 'MISSING'}",
                 st == 201 and bool(api_key)):
        return
    keyh = {"X-API-Key": api_key}

    # S9b baseline: a SYNCHRONOUS agent (no HITL trigger) runs straight through the gate.
    seo = {"agent_id": "seo_keyword_agent", "input": {"topic": "ai bookkeeping"}}
    st, body = http("POST", "/api/v1/agents/execute", headers=bearer, body=seo)
    if not check("S9b synchronous agent baseline (seo_keyword_agent, switch OFF)",
                 "201 + deliverable_id", f"{st} {cut(body, 110)}",
                 st == 201 and isinstance(body, dict) and bool(body.get("deliverable_id"))):
        return

    # S10 engage -----------------------------------------------------------------------
    ks = {"scope_type": "tenant", "scope_id": ORG, "reason": "demo-kit kill switch drill"}
    st, body = http("POST", "/api/v1/kill-switch/engage", headers=keyh, body=ks)
    if not check("S10 kill switch ENGAGE (tenant scope, API key)", "200 status=engaged",
                 f"{st} {cut(body, 120)}", st == 200 and isinstance(body, dict) and body.get("status") == "engaged"):
        return

    # S11 approve under the switch -> blocked ---------------------------------------------
    st, body = http("POST", f"/api/v1/hitl/{hitl2}/approve", headers=bearer, body={"note": "should be blocked"})
    detail = body.get("detail") if isinstance(body, dict) else body
    blocked = check("S11 approve while engaged is blocked", "502 and detail mentions 'kill switch engaged'",
                    f"{st} detail={cut(detail, 150)}", st == 502 and "kill switch engaged" in str(detail))

    # S11b the same synchronous agent is now refused by the governance gate (403, coded).
    st, body = http("POST", "/api/v1/agents/execute", headers=bearer, body=seo)
    blocked_sync = check("S11b synchronous agent while engaged is denied",
                         "403 code=governance_denied, 'kill switch engaged'",
                         f"{st} {cut(body, 130)}",
                         st == 403 and isinstance(body, dict) and body.get("code") == "governance_denied"
                         and "kill switch engaged" in str(body.get("detail")))

    # S12 disengage ALWAYS runs, even if S11/S11b failed, so the demo org is never left killed.
    st2, body2 = http("POST", "/api/v1/kill-switch/disengage", headers=keyh, body=ks)
    ok12 = st2 == 200 and isinstance(body2, dict) and body2.get("status") == "disengaged"
    check("S12 kill switch DISENGAGE", "200 status=disengaged", f"{st2} {cut(body2, 120)}", ok12)
    if not (blocked and blocked_sync and ok12):
        return

    # S13 recovery: the blocked row was released back to pending (K12), so approve now works --
    st, body = http("POST", f"/api/v1/hitl/{hitl2}/approve", headers=bearer, body={"note": "after disengage"})
    check("S13 approve after disengage succeeds (row was not lost)", "200 status=approved + deliverable_id",
          f"{st} {cut(body, 120)}",
          st == 200 and isinstance(body, dict) and body.get("status") == "approved")


# ------------------------------------------------------------------ console UI
def console_phase() -> None:
    """Point the website's dev server at THIS backend and probe the BFF routes the
    console screens call. Needs `npm ci` to have been run in website/.

    The BFF authenticates to the backend with a SERVICE API KEY (website/src/lib/
    skylize/client.ts attaches it as X-API-Key). We mint one through the API, hand it
    to `next dev` through its environment only, and never print or persist it.
    """
    st, body = http("POST", "/api/v1/auth/login", body={"email": EMAIL, "password": DEMO_PASSWORD})
    tok = body.get("access_token") if isinstance(body, dict) else None
    if not check("C0 re-login for console bring-up", "200 + access_token", f"{st}", st == 200 and bool(tok)):
        return
    bearer = {"Authorization": f"Bearer {tok}"}

    # A pending HITL row, so the Approvals panel has something real to show.
    st, body = http("POST", "/api/v1/agents/execute", headers=bearer,
                    body={"agent_id": AGENT, "input": {**CFO_INPUT, "period": "2026-Q1"}})
    pending_id = body.get("hitl_id") if isinstance(body, dict) else None
    if not check("C1 pending HITL item seeded for the Approvals panel", "202 deferred_to_human",
                 f"{st} hitl_id={pending_id}", st == 202 and bool(pending_id)):
        return

    st, body = http("POST", "/api/v1/api-keys", headers=bearer,
                    body={"name": "demokit-console-bff", "scopes": ["owner"], "expires_in_days": 1})
    api_key = body.get("api_key") if isinstance(body, dict) else None
    if not check("C2 mint BFF service API key", "201 + api_key",
                 f"{st} api_key={'<present, not printed>' if api_key else 'MISSING'}",
                 st == 201 and bool(api_key)):
        return

    site = REPO / "website"
    if not (site / "node_modules").is_dir():
        check("C3 website dependencies installed", "website/node_modules exists",
              "missing -- run `npm ci` in website/ first", False)
        return
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    cenv = {
        **scrubbed_env(),
        "SKYLIZE_BACKEND_URL": API,
        "SKYLIZE_SERVICE_API_KEY": api_key or "",
        "SKYLIZE_CONSOLE_PASSWORD": CONSOLE_PASSWORD,
        "SKYLIZE_CONSOLE_SESSION_SECRET": secrets.token_urlsafe(48),
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    cenv.pop("PYTHONPATH", None)
    log = open(CONSOLE_LOG_FILE, "wb")
    proc = subprocess.Popen(
        [npm, "run", "dev", "--", "--port", str(CONSOLE_PORT), "--hostname", "127.0.0.1"],
        cwd=str(site), env=cenv, stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    CONSOLE_PID_FILE.write_text(str(proc.pid))
    deadline, status = time.time() + 240, None
    while time.time() < deadline and proc.poll() is None:
        try:
            status, _ = http("GET", "/console/login", base=CONSOLE, timeout=30)
            if status == 200:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    if not check("C3 next dev up with SKYLIZE_BACKEND_URL -> local backend", "GET /console/login 200",
                 f"{status} ({CONSOLE}, backend={API})", status == 200):
        return

    # BFF session: the same call the login page makes.
    st, hdrs, _ = http_full("POST", "/api/console/session", body={"password": CONSOLE_PASSWORD}, base=CONSOLE)
    cookie_hdr = hdrs.get("set-cookie", "")
    cookie = cookie_hdr.split(";", 1)[0] if cookie_hdr else ""
    if not check("C4 POST /api/console/session (console login)", "204 + skylize_console cookie",
                 f"{st} cookie={'skylize_console=<set>' if cookie.startswith('skylize_console=') else 'MISSING'}",
                 st == 204 and cookie.startswith("skylize_console=")):
        return
    ch = {"Cookie": cookie}

    st, body = http("GET", "/api/console/health", headers=ch, base=CONSOLE)
    check("C5 BFF /health -> backend", "200 status ok", f"{st} {cut(body, 100)}", st == 200)
    st, body = http("GET", "/api/console/tenant", headers=ch, base=CONSOLE)
    check("C6 BFF /tenant -> backend", "200 org_id=demo org", f"{st} {cut(body, 100)}",
          st == 200 and isinstance(body, dict) and body.get("org_id") == ORG)
    st, body = http("GET", "/api/console/hitl?limit=50", headers=ch, base=CONSOLE)
    rows = body.get("data", []) if isinstance(body, dict) else []
    seen = any(r.get("hitl_id") == pending_id for r in rows)
    check("C7 BFF /hitl shows the pending item", "200, seeded hitl_id listed",
          f"{st} total={len(rows)} ids_match={seen}", st == 200 and seen)
    st, body = http("GET", "/api/console/audit?limit=50", headers=ch, base=CONSOLE)
    entries = body.get("entries", []) if isinstance(body, dict) else []
    with_token = sum(1 for e in entries if e.get("governance_token_id"))
    check("C8 BFF /audit returns the governed-action feed", "200, entries with governance_token_id",
          f"{st} entries={len(entries)} with_token={with_token}", st == 200 and with_token > 0)
    st, _h, html = http_full("GET", "/console", headers=ch, base=CONSOLE, timeout=120)
    check("C9 GET /console page renders", "200 'Operator console'",
          f"{st} has_text={'Operator console' in str(html)}", st == 200 and "Operator console" in str(html))


# ------------------------------------------------------------------------ main
def summary() -> int:
    say("")
    say("=" * 78)
    say("SUMMARY")
    say("=" * 78)
    for step, verdict, _exp, actual in RESULTS:
        say(f"  {verdict:<4}  {step}")
    failed = [r for r in RESULTS if r[1] == "FAIL"]
    say("-" * 78)
    say(f"  {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS" + ("" if not failed else f"  -- FAIL at: {failed[0][0]}"))
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--teardown", action="store_true",
                    help="stop uvicorn, remove compose project + volumes + temp state, then verify")
    ap.add_argument("--keep-running", action="store_true",
                    help="leave uvicorn (and DB/Redis) running after the steps")
    ap.add_argument("--console", action="store_true",
                    help="after the steps, also start the website dev server against this backend "
                         "(needs `npm ci` in website/), probe the BFF routes; implies --keep-running")
    args = ap.parse_args()
    if args.console:
        args.keep_running = True

    if args.teardown:
        return 0 if teardown() else 1

    say(f"repo      : {REPO}")
    say(f"base SHA  : {run(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], check=False).stdout.strip()}")
    say(f"project   : {PROJECT}  api={API}  (demo LLM, no external calls)")
    say("")
    # Always start from a clean slate so every run is the same run.
    teardown(quiet=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if bring_up() and not _ABORTED:
            demo()
            if args.console and not _ABORTED:
                console_phase()
    except Exception as exc:  # noqa: BLE001
        check("UNEXPECTED ERROR", "no exception", cut(f"{type(exc).__name__}: {exc}", 300), False)
    finally:
        if not args.keep_running:
            uvicorn_stop()
    rc = summary()
    if args.keep_running and rc == 0:
        say(f"\nbackend left RUNNING at {API} (org={ORG}, backend login={EMAIL}).")
        if args.console:
            say(f"console left RUNNING at {CONSOLE}/console/login "
                "(password = SKYLIZE_DEMOKIT_CONSOLE_PASSWORD default in this script).")
        say("Stop everything with: py -3.12 scripts/demo/cfo_test_local.py --teardown")
    elif rc != 0:
        say(f"\nuvicorn log: {LOG_FILE}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
