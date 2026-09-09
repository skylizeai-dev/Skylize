import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function AuditLog({ vm }) {
  return (
    <div data-screen-label="Audit Log" style={sx('flex:1;min-height:0;display:flex;flex-direction:column;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:center;gap:10px;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em;margin-right:4px')}>Audit Log</h1>
        <div style={sx('display:flex;gap:4px')}>
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
          <span style={sx("display:flex;align-items:center;gap:5px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;color:#34C579")}><span style={sx('width:5px;height:5px;border-radius:50%;background:#34C579;box-shadow:0 0 8px rgba(52,197,121,0.8);animation:pulseDot 1.6s ease-in-out infinite')}></span>STREAMING</span>
          <Interactive as="button" style={sx("height:29px;padding:0 12px;border-radius:6px;border:1px solid #262D40;background:transparent;color:#C7CBD6;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;cursor:pointer;transition:border-color .15s")} hoverStyle={sx('border-color:var(--accent,#3D6BFF)')}>EXPORT · SIGNED</Interactive>
        </span>
      </div>
      <div style={sx('flex:1;min-height:0;background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden;display:flex;flex-direction:column')}>
        <div style={sx("display:grid;grid-template-columns:76px minmax(140px,1.5fr) 120px minmax(150px,1.8fr) minmax(120px,1.2fr);gap:0 12px;padding:9px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#77809A;background:rgba(255,255,255,0.015);flex-shrink:0")}>
          <span>TIME</span><span>ACTOR</span><span>ACTION</span><span>TARGET</span><span>SIGNATURE</span>
        </div>
        <div style={sx('flex:1;overflow-y:auto')}>
          {vm.logRows.map((lr, i) => (
            <Interactive
              key={i}
              style={sx('display:grid;grid-template-columns:76px minmax(140px,1.5fr) 120px minmax(150px,1.8fr) minmax(120px,1.2fr);gap:0 12px;align-items:center;padding:7px 16px;border-bottom:1px solid #12151F;transition:background-color .12s')}
              hoverStyle={sx('background-color:rgba(255,255,255,0.02)')}
            >
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#616A82;font-variant-numeric:tabular-nums")}>{lr.time}</span>
              <span style={sx('font-size:12px;font-weight:500;color:#C7CBD6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{lr.actor}</span>
              <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.08em;padding:2px 7px;border-radius:4px;text-align:center;color: ${lr.actionColor};background: ${lr.actionBg}`)}>{lr.action}</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{lr.target}</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#616A82;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{lr.sig}</span>
            </Interactive>
          ))}
        </div>
      </div>
    </div>
  );
}
