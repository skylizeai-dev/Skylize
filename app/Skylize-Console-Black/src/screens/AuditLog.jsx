import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

// TWO HEADINGS CHANGED, AND THE REASON MATTERS MORE THAN THE WORDS.
//
//   ACTOR -> SOURCE AGENT. The rows used to print "Chief Financial Officer" and
//   "Operator (human)" under ACTOR. `audit_log` has no human-actor column; it
//   has `source_agent_id`, an agent id or null. Attributing an agent action to
//   a person, on the screen whose entire job is attribution, is the worst claim
//   this console could make.
//
//   SIGNATURE -> INPUTS HASH. The rows used to print invented values like
//   "0x8F41...C2A9" under SIGNATURE, and a button offering "EXPORT · SIGNED".
//   The backend field is `inputs_hash`, a SHA-256 CONTENT hash. A hash proves a
//   payload is unaltered; a signature asserts who produced it. Nothing here is
//   signed, so nothing here says signed.
//
// "STREAMING" is also gone: this is a GET on mount with an explicit refresh,
// not a live stream, and a pulsing green "STREAMING" dot over a static list is
// a claim that the operator is seeing events as they happen.
export default function AuditLog({ vm }) {
  const COLS = 'display:grid;grid-template-columns:76px minmax(140px,1.4fr) 130px minmax(150px,1.8fr) minmax(130px,1.1fr);gap:0 12px';
  return (
    <div data-screen-label="Audit Log" style={sx('flex:1;min-height:0;display:flex;flex-direction:column;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:center;gap:10px;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em;margin-right:4px')}>Audit Log</h1>
        <div style={sx('display:flex;gap:4px;flex-wrap:wrap')}>
          {vm.logChips.map((lc, i) => (
            <Interactive
              key={i}
              as="button"
              onClick={lc.pick}
              style={sx(`height:29px;padding:0 10px;border-radius:6px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;cursor:pointer;transition:border-color .15s;border:1px solid ${lc.bd};background: ${lc.bg};color: ${lc.c}`)}
              hoverStyle={sx('border-color:#77809A')}
            >{lc.label}</Interactive>
          ))}
        </div>
        <span style={sx('margin-left:auto;display:flex;align-items:center;gap:10px')}>
          <Interactive as="button" onClick={vm.logRetry} style={sx("height:29px;padding:0 12px;border-radius:6px;border:1px solid #262D40;background:transparent;color:#C7CBD6;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;cursor:pointer;transition:border-color .15s")} hoverStyle={sx('border-color:var(--accent,#3D6BFF)')}>REFRESH</Interactive>
        </span>
      </div>
      <div style={sx('flex:1;min-height:0;background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden;display:flex;flex-direction:column')}>
        <div style={sx(COLS + ";padding:9px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#77809A;background:rgba(255,255,255,0.015);flex-shrink:0")}>
          <span>TIME</span><span>SOURCE AGENT</span><span>ACTION</span><span>RESULT</span><span title="SHA-256 content hash of the inputs — not a signature">INPUTS HASH</span>
        </div>
        <div style={sx('flex:1;overflow-y:auto')}>
          {vm.logLoading && (
            <div style={sx("padding:34px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#77809A;letter-spacing:0.06em")}>Reading the audit log…</div>
          )}
          {vm.logError && (
            <div style={sx('padding:28px 20px;text-align:center')}>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#E15A52;margin-bottom:8px")}>AUDIT LOG UNAVAILABLE</div>
              <div style={sx('font-size:12.5px;color:#9AA1B2;margin-bottom:14px')}>{vm.logError}</div>
              <Interactive as="button" onClick={vm.logRetry} style={sx("height:28px;padding:0 14px;border-radius:6px;border:1px solid #262D40;background:transparent;color:#C7CBD6;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.08em;cursor:pointer;transition:border-color .15s")} hoverStyle={sx('border-color:var(--accent,#3D6BFF)')}>RETRY</Interactive>
            </div>
          )}
          {vm.logEmpty && (
            <div style={sx("padding:34px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#616A82;letter-spacing:0.06em")}>No governed actions recorded yet.</div>
          )}
          {vm.logRows.map((lr, i) => (
            <Interactive
              key={i}
              style={sx(COLS + ';align-items:center;padding:7px 16px;border-bottom:1px solid #12151F;transition:background-color .12s')}
              hoverStyle={sx('background-color:rgba(255,255,255,0.02)')}
            >
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#616A82;font-variant-numeric:tabular-nums")}>{lr.time}</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#C7CBD6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{lr.agent}</span>
              <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.08em;padding:2px 7px;border-radius:4px;text-align:center;color: ${lr.actionColor};background: ${lr.actionBg};white-space:nowrap;overflow:hidden;text-overflow:ellipsis`)}>{lr.action}</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{lr.target}</span>
              <span title="SHA-256 content hash — not a signature" style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#616A82;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{lr.hash}</span>
            </Interactive>
          ))}
        </div>
      </div>
    </div>
  );
}
