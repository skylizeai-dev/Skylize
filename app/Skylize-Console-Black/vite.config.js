import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// The BFF this console talks to. Vite dev/preview forward /api/console/* there
// so the browser only ever makes SAME-ORIGIN calls -- no CORS, and no
// credential in this app. In production the same relative paths must be served
// by a reverse proxy or same-origin hosting (see README, "Backend calls").
const BFF_ORIGIN = process.env.CONSOLE_BFF_ORIGIN || 'http://localhost:3000';

// Anything Vite exposes as VITE_* is INLINED INTO THE BUNDLE and shipped to
// every visitor. A static SPA has nowhere safe to keep a credential, so rather
// than rely on nobody ever adding one, the build refuses to run when a VITE_
// variable is named like a secret. Mirrors the NEXT_PUBLIC_ guard the BFF uses
// on the other side (website/src/lib/skylize/config.ts:37-47).
const SECRET_NAME = /(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|AUTH)/i;

function assertNoInlinedSecrets(mode) {
  // loadEnv, not process.env: it also reads .env / .env.local, which is exactly
  // where someone would put a key without thinking about the bundle.
  const exposed = loadEnv(mode, process.cwd(), 'VITE_');
  const offenders = Object.keys(exposed).filter((name) => SECRET_NAME.test(name));
  if (offenders.length > 0) {
    throw new Error(
      `Refusing to build: ${offenders.join(', ')} would be inlined into the ` +
        'client bundle and served to every visitor. This console is a static ' +
        'SPA and must hold NO credential -- the service API key lives only in ' +
        'the BFF (website/src/app/api/console/*). Remove the variable and let ' +
        'the server-side proxy hold the credential.',
    );
  }
}

export default defineConfig(({ mode }) => {
  assertNoInlinedSecrets(mode);

  const proxy = {
    '/api/console': {
      target: BFF_ORIGIN,
      changeOrigin: true,
      // The BFF authenticates with its `skylize_console` session cookie, so the
      // cookie has to survive the hop. Same-origin from the browser's side
      // means it is sent and set without SameSite=None.
      cookieDomainRewrite: '',
    },
  };

  return {
    plugins: [react()],
    server: { proxy },
    preview: { proxy },
  };
});
