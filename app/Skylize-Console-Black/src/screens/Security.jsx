import React from 'react';
import { sx } from '../lib/style.jsx';

// REAL audit_log-derived counts only. THE SCORE GAUGE, THE CONTROL CHECKLIST
// AND THE COMPLIANCE BADGE ROW ARE GONE, not reshaped: none has a scoring
// methodology, a control inventory or a compliance auditor behind it
// anywhere in this system. See useConsoleState.js's `secByResult`/`secEvents`
// for the full accounting of what this screen used to claim and cannot.
export default function Security({ vm }) {
  return (
    <div data-screen-label="Security" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Security &amp; Compliance</h1>
      </div>
      <div style={sx("font-size:11px;color:#77809A;margin-bottom:13px")}>{vm.secScoreUnavailable}</div>
      <div style={sx('display:grid;grid-template-columns:280px minmax(0,1fr);gap:12px;align-items:start')}>
        <div style={sx('display:flex;flex-direction:column;gap:12px')}>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:18px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;margin-bottom:10px")}>GOVERNED ACTIONS &middot; WINDOW</div>
            <div style={sx('display:flex;flex-direction:column;gap:8px')}>
              <div style={sx('display:flex;justify-content:space-between')}><span style={sx('font-size:11.5px;color:#8B93A7')}>Total</span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:12px;color:#E9EBF2")}>{vm.secTotalActions}</span></div>
              <div style={sx('display:flex;justify-content:space-between')}><span style={sx('font-size:11.5px;color:#8B93A7')}>Success</span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:12px;color:#34C579")}>{vm.secSuccess}</span></div>
              <div style={sx('display:flex;justify-content:space-between')}><span style={sx('font-size:11.5px;color:#8B93A7')}>Denied</span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:12px;color:#E15A52")}>{vm.secDenied}</span></div>
              <div style={sx('display:flex;justify-content:space-between')}><span style={sx('font-size:11.5px;color:#8B93A7')}>Escalated</span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:12px;color:#D19A3F")}>{vm.secEscalated}</span></div>
              <div style={sx('display:flex;justify-content:space-between')}><span style={sx('font-size:11.5px;color:#8B93A7')}>Failed</span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:12px;color:#E15A52")}>{vm.secFailed}</span></div>
            </div>
          </div>
          {vm.secError && (
            <div role="alert" style={sx("background:#0C0F16;border:1px solid rgba(225,90,82,0.35);border-radius:10px;padding:12px 14px;font-size:11.5px;color:#E9A9A4")}>{vm.secError}</div>
          )}
        </div>
        <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
          <div style={sx("padding:10px 14px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>RECENT NON-SUCCESS EVENTS</div>
          {vm.secLoading ? (
            <div style={sx('padding:16px;font-size:12px;color:#77809A')}>Reading recent activity&hellip;</div>
          ) : vm.secEmpty ? (
            <div style={sx('padding:16px;font-size:12px;color:#77809A')}>No denied, escalated or failed actions in this window.</div>
          ) : (
            vm.secEvents.map((se, i) => (
              <div key={i} style={sx('display:flex;align-items:baseline;gap:8px;padding:8px 14px;border-bottom:1px solid #12151F')}>
                <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${se.sevColor};flex-shrink:0;position:relative;top:-1px;box-shadow:0 0 6px ${se.sevColor}`)}></span>
                <span style={sx('flex:1;font-size:11.5px;line-height:1.5;color:#C7CBD6')}>{se.text}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#616A82;font-variant-numeric:tabular-nums")}>{se.time}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
