import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

// REAL KEYS, AND ONE HONEST SECRET MOMENT.
//
// The three rows here used to be invented ("Production - orchestrator /
// sk-live-****4F2A / FULL / 2 min ago"). On a key-management screen that is the
// most dangerous kind of fiction: an operator could believe a key exists, or
// believe one was revoked. They now come from GET /api/v1/api-keys.
//
// `masked` is the backend `prefix` -- the real non-secret identifying fragment,
// not a row of bullets standing in for one. `scope` is the key's actual
// `scopes`, which is load-bearing rather than cosmetic: a key's scopes BECOME
// its roles (app/auth/service.py:95).
//
// THE MINTED SECRET is shown once, in a panel the operator must dismiss,
// because the backend will never return it again (api_keys.py:6-8). Nothing
// persists it.
export default function Developers({ vm }) {
  return (
    <div data-screen-label="Developers" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Developers</h1>
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#77809A")}>API v1</span>
      </div>

      {vm.mintedKey && (
        <div style={sx('background:rgba(52,197,121,0.05);border:1px solid rgba(52,197,121,0.35);border-radius:10px;padding:14px 15px;margin-bottom:12px')}>
          <div style={sx('display:flex;align-items:center;justify-content:space-between;margin-bottom:9px')}>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#34C579")}>KEY CREATED — COPY IT NOW</span>
            <Interactive as="button" onClick={vm.dismissMintedKey} style={sx("height:24px;padding:0 10px;border-radius:5px;border:1px solid #262D40;background:transparent;color:#8B93A7;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;cursor:pointer")} hoverStyle={sx('border-color:#77809A;color:#E9EBF2')}>DISMISS</Interactive>
          </div>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11.5px;color:#E9EBF2;background:#07080C;border:1px solid #161A26;border-radius:7px;padding:11px 12px;word-break:break-all;user-select:all")}>{vm.mintedKey.secret}</div>
          <div style={sx('font-size:11.5px;color:#8B93A7;margin-top:8px;line-height:1.6')}>This is the only time the secret is shown. It is not stored by this console and the backend will not return it again.</div>
        </div>
      )}

      <div style={sx('display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,1fr);gap:12px;align-items:start')}>
        <div style={sx('display:flex;flex-direction:column;gap:12px;min-width:0')}>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
            <div style={sx('display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid #161A26')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>API KEYS</span>
              <Interactive
                as="button"
                onClick={() => {
                  const name = window.prompt('Name for the new key');
                  if (name) vm.mintKey(name);
                }}
                style={sx("height:25px;padding:0 10px;border-radius:5px;border:1px solid #262D40;background:transparent;color:#C7CBD6;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;cursor:pointer;transition:border-color .15s")}
                hoverStyle={sx('border-color:var(--accent,#3D6BFF)')}
              >+ NEW KEY</Interactive>
            </div>
            {vm.keysLoading && (
              <div style={sx("padding:26px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#77809A;letter-spacing:0.06em")}>Reading keys…</div>
            )}
            {vm.keysError && (
              <div style={sx('padding:22px 16px')}>
                <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#E15A52;margin-bottom:7px")}>KEYS UNAVAILABLE</div>
                <div style={sx('font-size:12.5px;color:#9AA1B2')}>{vm.keysError}</div>
              </div>
            )}
            {vm.keysEmpty && (
              <div style={sx("padding:26px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#616A82;letter-spacing:0.06em")}>No API keys issued for this org.</div>
            )}
            {vm.keyRows.map((kr, i) => (
              <div key={i} style={sx(`display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid #12151F;opacity: ${kr.revoked ? '0.45' : '1'}`)}>
                <div style={sx('flex:1;min-width:0')}>
                  <div style={sx('font-size:12.5px;font-weight:500;color:#E9EBF2')}>{kr.name}</div>
                  <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;margin-top:2px")}>{kr.masked} · created {kr.created}</div>
                </div>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.08em;color:#8B93A7;border:1px solid #232939;border-radius:4px;padding:2px 7px;white-space:nowrap;max-width:190px;overflow:hidden;text-overflow:ellipsis")}>{kr.scope}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;font-variant-numeric:tabular-nums;white-space:nowrap")}>{kr.lastUsed}</span>
                {!kr.revoked && (
                  <Interactive
                    as="button"
                    onClick={() => { if (window.confirm('Revoke "' + kr.name + '"? This cannot be undone.')) kr.revoke(); }}
                    style={sx("height:24px;padding:0 9px;border-radius:5px;border:1px solid #262D40;background:transparent;color:#8B93A7;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.06em;cursor:pointer;flex-shrink:0;transition:border-color .15s,color .15s")}
                    hoverStyle={sx('border-color:#E15A52;color:#E15A52')}
                  >REVOKE</Interactive>
                )}
              </div>
            ))}
          </div>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
            <div style={sx("padding:10px 14px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>CORE ENDPOINTS</div>
            {vm.endRows.map((er, i) => (
              <div key={i} style={sx('display:flex;align-items:center;gap:12px;padding:8px 14px;border-bottom:1px solid #12151F')}>
                <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;font-weight:600;letter-spacing:0.06em;width:52px;text-align:center;padding:2px 0;border-radius:4px;color: ${er.mColor};background: ${er.mBg};flex-shrink:0`)}>{er.method}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#E9EBF2;flex-shrink:0")}>{er.path}</span>
                <span style={sx('font-size:11px;color:#77809A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{er.desc}</span>
              </div>
            ))}
          </div>
        </div>
        <div style={sx('display:flex;flex-direction:column;gap:12px')}>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:14px 15px')}>
            <div style={sx('display:flex;align-items:center;justify-content:space-between;margin-bottom:10px')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>WEBHOOK EVENTS</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;color:#4A5162")}>DESIGNED · NOT BUILT</span>
            </div>
            <div style={sx('font-size:11.5px;color:#8B93A7;line-height:1.6')}>No webhook delivery exists yet; these event names are a design sketch, not subscriptions you can rely on.</div>
            <div style={sx('display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;opacity:0.45')}>
              {vm.hookEvents.map((he, i) => (
                <span key={i} style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#C7CBD6;border:1px solid #232939;border-radius:4px;padding:3px 8px;background:rgba(255,255,255,0.02)")}>{he.name}</span>
              ))}
            </div>
          </div>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:14px 15px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;margin-bottom:10px")}>QUICKSTART</div>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;line-height:1.7;color:#9AA1B2;background:#07080C;border:1px solid #161A26;border-radius:7px;padding:11px 12px;white-space:pre-wrap")}>{vm.quickstart}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
