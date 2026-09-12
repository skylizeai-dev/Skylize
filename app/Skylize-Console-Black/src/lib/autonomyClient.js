// The console's ONLY backend call, and the shape of every future one.
//
// WHY IT GOES THROUGH A PROXY. This app is a static bundle: every byte of it is
// served to every visitor, so it can hold no credential. It therefore talks to
// the server-side BFF at /api/console/* (website/src/app/api/console/autonomy/
// route.ts), which holds the service API key and forwards to the FastAPI
// backend. The calls below are deliberately RELATIVE -- same-origin, so there
// is no CORS to open and no origin to configure in the bundle.
//
// The BFF's own contract, which these functions rely on:
//   * 200 -> { mode, configured }
//   * 401 -> the console session expired (NOT a credential problem)
//   * 502 -> the backend rejected the SERVER's credential, or is unreachable
//   * 4xx -> a real client error, message in { error }

// Kept in step with src/skylize/contracts/base.py `AutonomyMode` and with the
// BFF's zod enum. A value outside this set is not displayable, so it is treated
// as unreadable rather than shown.
export const AUTONOMY_MODES = [
  'observe',
  'propose',
  'act_within_budget',
  'act_and_reallocate',
  'act_governed',
];

// Ruling 7. An org whose posture nobody has set, AND any answer this console
// cannot understand, both resolve to the mode where every action needs a human.
export const DEFAULT_AUTONOMY_MODE = 'observe';

const ENDPOINT = '/api/console/autonomy';
const TIMEOUT_MS = 10000;

export function isAutonomyMode(value) {
  return typeof value === 'string' && AUTONOMY_MODES.indexOf(value) >= 0;
}

// A human-readable cause, never the raw body. Keeps a 502's meaning ("the
// server's credential was refused") distinct from a 401's ("your session
// ended"), because the operator can act on one and not the other.
function describe(status, body) {
  if (status === 401) return 'Console session expired — sign in again.';
  if (status === 403) return 'Your account is not permitted to change this.';
  if (status === 502) return 'Backend unreachable or the server credential was refused.';
  if (status === 503) return 'Autonomy storage is unavailable on this deployment.';
  if (status === 504) return 'The backend timed out.';
  if (status === 429) return 'Rate limited — try again shortly.';
  if (body && typeof body.error === 'string' && body.error) return body.error;
  return `Request failed (HTTP ${status}).`;
}

async function call(options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let response;
  try {
    response = await fetch(ENDPOINT, {
      method: options.method,
      headers: options.body
        ? { 'Content-Type': 'application/json', Accept: 'application/json' }
        : { Accept: 'application/json' },
      body: options.body ? JSON.stringify(options.body) : undefined,
      // The session cookie is what the BFF authenticates on.
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (e) {
    throw new Error(
      e && e.name === 'AbortError'
        ? 'The request timed out.'
        : 'Could not reach the console server.',
    );
  } finally {
    clearTimeout(timer);
  }

  let body = null;
  try { body = await response.json(); } catch (e) { body = null; }

  if (!response.ok) throw new Error(describe(response.status, body));

  // A 200 whose body is not the shape we expect is a failure, not a default:
  // silently substituting `observe` here would be indistinguishable from the
  // backend actually reporting `observe`.
  if (!body || !isAutonomyMode(body.mode)) {
    throw new Error('The server returned an autonomy mode this console cannot read.');
  }
  return { mode: body.mode, configured: body.configured === true };
}

/** Read the org-wide mode. Throws on any failure -- the CALLER fails closed. */
export function fetchAutonomyMode() {
  return call({ method: 'GET' });
}

/**
 * Set the org-wide mode. Resolves with what the backend PERSISTED, which the
 * caller must display instead of the requested value.
 */
export function putAutonomyMode(mode) {
  if (!isAutonomyMode(mode)) {
    return Promise.reject(new Error(`Not an autonomy mode: ${String(mode)}`));
  }
  return call({ method: 'PUT', body: { mode } });
}
