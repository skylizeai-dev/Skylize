import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

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
        {vm.apEmpty && (
          <div style={sx("padding:34px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#616A82;letter-spacing:0.06em")}>Queue clear. All actions within autonomy envelope.</div>
        )}
        {vm.apRows.map((ap, i) => (
          <Interactive
            key={i}
            style={sx('display:flex;align-items:center;gap:14px;padding:12px 16px;border-bottom:1px solid #141826;transition:background-color .12s')}
            hoverStyle={sx('background-color:rgba(255,255,255,0.02)')}
          >
            <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:3px 7px;border-radius:4px;color: ${ap.riskColor};background: ${ap.riskBg};border:1px solid ${ap.riskBd};flex-shrink:0;width:52px;text-align:center`)}>{ap.risk}</span>
            <div style={sx('flex:1;min-width:0')}>
              <div style={sx('font-size:13px;font-weight:500;color:#E9EBF2')}>{ap.title}</div>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;margin-top:3px")}>{ap.meta}</div>
              <div style={sx("display:flex;align-items:center;gap:6px;margin-top:5px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#8B93A7")}><span style={sx('color:#616A82')}>CHAIN</span><span>{ap.chain}</span></div>
            </div>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:13px;font-weight:600;color:#E9EBF2;font-variant-numeric:tabular-nums;flex-shrink:0")}>{ap.amount}</span>
            <div style={sx('display:flex;gap:6px;flex-shrink:0')}>
              <Interactive as="button" onClick={ap.decline} style={sx("height:28px;padding:0 12px;border-radius:6px;border:1px solid #262D40;background:transparent;color:#8B93A7;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:#E15A52;color:#E15A52')}>DECLINE</Interactive>
              <Interactive as="button" onClick={ap.approve} style={sx("height:28px;padding:0 12px;border-radius:6px;border:none;background:var(--accent,#3D6BFF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;cursor:pointer;box-shadow:0 0 14px color-mix(in oklab,var(--accent,#3D6BFF) 35%,transparent);transition:filter .15s")} hoverStyle={sx('filter:brightness(1.15)')}>APPROVE</Interactive>
            </div>
          </Interactive>
        ))}
      </div>
    </div>
  );
}
