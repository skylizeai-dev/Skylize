import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

// WHAT THIS SCREEN NO LONGER CLAIMS.
//
// The design had a HIGH/MED/LOW risk badge on every row, a money column and a
// "CHAIN Dir -> VP Marketing -> CMO -> YOU" line. The backend that fills this
// screen (GET /api/v1/hitl) has no risk taxonomy, no typed money field and no
// principal chain -- HitlItemResponse is hitl_id, agent_id, trigger_reason,
// status, created_at, expires_at, proposal_summary, request_input.
//
// Rather than fill three governance-shaped columns with browser-side guesses,
// the row now shows what the platform actually recorded: WHY the item is
// waiting (`trigger_reason`), which agent proposed it, how long it has waited,
// and the proposal summary itself. The filter chips filter on the real
// `status`.
export default function Approvals({ vm }) {
  return (
    <div data-screen-label="Approvals" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:center;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Approvals</h1>
        <div style={sx('display:flex;gap:4px')}>
          {vm.apRiskChips.map((rc, i) => (
            <Interactive
              key={i}
              as="button"
              onClick={rc.pick}
              style={sx(`height:29px;padding:0 10px;border-radius:6px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;cursor:pointer;transition:border-color .15s;border:1px solid ${rc.bd};background: ${rc.bg};color: ${rc.c}`)}
              hoverStyle={sx('border-color:#77809A')}
            >{rc.label}</Interactive>
          ))}
        </div>
      </div>
      <div style={sx('display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:12px')}>
        {vm.apStats.map((s, i) => (
          <div key={i} style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:13px 15px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#77809A;margin-bottom:8px")}>{s.label}</div>
            <div style={sx('display:flex;align-items:baseline;gap:6px')}><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:22px;font-weight:600;color: ${s.color};font-variant-numeric:tabular-nums`)}>{s.value}</span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A")}>{s.sub}</span></div>
          </div>
        ))}
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
        {vm.apLoading && (
          <div style={sx("padding:34px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#77809A;letter-spacing:0.06em")}>Reading the approvals queue…</div>
        )}
        {/* A FAILED READ IS NOT AN EMPTY QUEUE. Showing "queue clear" here when
            the backend is unreachable would tell an operator there is nothing
            waiting on them when the console simply cannot see. */}
        {vm.apError && (
          <div style={sx('padding:28px 20px;text-align:center')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#E15A52;margin-bottom:8px")}>QUEUE UNAVAILABLE</div>
            <div style={sx('font-size:12.5px;color:#9AA1B2;margin-bottom:14px')}>{vm.apError}</div>
            <Interactive as="button" onClick={vm.apRetry} style={sx("height:28px;padding:0 14px;border-radius:6px;border:1px solid #262D40;background:transparent;color:#C7CBD6;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.08em;cursor:pointer;transition:border-color .15s")} hoverStyle={sx('border-color:var(--accent,#3D6BFF)')}>RETRY</Interactive>
          </div>
        )}
        {vm.apEmpty && (
          <div style={sx("padding:34px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#616A82;letter-spacing:0.06em")}>Queue clear. Nothing is waiting on a human.</div>
        )}
        {vm.apRows.map((ap, i) => (
          <div
            key={i}
            style={sx(`display:flex;align-items:flex-start;gap:14px;padding:12px 16px;border-bottom:1px solid #141826;opacity: ${ap.busy ? '0.5' : '1'}`)}
          >
            {/* The real, recorded status -- not an invented risk tier. */}
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:3px 7px;border-radius:4px;color:#D19A3F;background:rgba(209,154,63,0.08);border:1px solid rgba(209,154,63,0.27);flex-shrink:0;min-width:58px;text-align:center")}>{(ap.status || '').toUpperCase()}</span>
            <div style={sx('flex:1;min-width:0')}>
              <div style={sx('font-size:13px;font-weight:500;color:#E9EBF2')}>{ap.title}</div>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;margin-top:3px")}>{ap.meta}</div>
              {ap.summary && (
                <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#8B93A7;margin-top:5px;line-height:1.55;word-break:break-word")}>{ap.summary}</div>
              )}
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;color:#4A5162;margin-top:5px;letter-spacing:0.06em")}>{ap.hitlId}</div>
            </div>
            <div style={sx('display:flex;gap:6px;flex-shrink:0')}>
              <Interactive as="button" disabled={ap.busy} onClick={() => ap.decline()} style={sx("height:28px;padding:0 12px;border-radius:6px;border:1px solid #262D40;background:transparent;color:#8B93A7;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:#E15A52;color:#E15A52')}>REJECT</Interactive>
              <Interactive as="button" disabled={ap.busy} onClick={() => ap.approve()} style={sx("height:28px;padding:0 12px;border-radius:6px;border:none;background:var(--accent,#3D6BFF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;cursor:pointer;box-shadow:0 0 14px color-mix(in oklab,var(--accent,#3D6BFF) 35%,transparent);transition:filter .15s")} hoverStyle={sx('filter:brightness(1.15)')}>APPROVE</Interactive>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
