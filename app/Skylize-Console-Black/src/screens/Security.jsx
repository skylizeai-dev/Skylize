import React from 'react';
import { sx } from '../lib/style.jsx';

export default function Security({ vm }) {
  return (
    <div data-screen-label="Security" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Security & Compliance</h1>
        <div style={sx('display:flex;gap:6px')}>
          {vm.secBadges.map((sb, i) => (
            <span key={i} style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#34C579;border:1px solid rgba(52,197,121,0.35);border-radius:4px;padding:3px 8px;background:rgba(52,197,121,0.06)")}>{sb.name}</span>
          ))}
        </div>
      </div>
      <div style={sx('display:grid;grid-template-columns:280px minmax(0,1fr);gap:12px;align-items:start')}>
        <div style={sx('display:flex;flex-direction:column;gap:12px')}>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:18px;display:flex;flex-direction:column;align-items:center')}>
            <div style={sx('position:relative;width:130px;height:130px')}>
              <svg width="130" height="130" viewBox="0 0 130 130">
                <circle cx="65" cy="65" r="56" fill="none" stroke="#141826" strokeWidth="9"></circle>
                <circle cx="65" cy="65" r="56" fill="none" stroke="var(--accent,#3D6BFF)" strokeWidth="9" strokeLinecap="round" strokeDasharray={vm.secDash} transform="rotate(-90 65 65)" style={sx('filter:drop-shadow(0 0 8px color-mix(in oklab,var(--accent,#3D6BFF) 60%,transparent))')}></circle>
              </svg>
              <div style={sx('position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center')}>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:27px;font-weight:600;color:#E9EBF2;font-variant-numeric:tabular-nums")}>{vm.secScore}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.14em;color:#77809A;margin-top:2px")}>POSTURE</span>
              </div>
            </div>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#77809A;margin-top:12px;letter-spacing:0.06em")}>LAST ASSESSMENT · 02:00 UTC</div>
          </div>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
            <div style={sx("padding:10px 14px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>SECURITY EVENTS · 7D</div>
            {vm.secEvents.map((se, i) => (
              <div key={i} style={sx('display:flex;align-items:baseline;gap:8px;padding:8px 14px;border-bottom:1px solid #12151F')}>
                <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${se.sevColor};flex-shrink:0;position:relative;top:-1px;box-shadow:0 0 6px ${se.sevColor}`)}></span>
                <span style={sx('flex:1;font-size:11.5px;line-height:1.5;color:#C7CBD6')}>{se.text}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#616A82;font-variant-numeric:tabular-nums")}>{se.time}</span>
              </div>
            ))}
          </div>
        </div>
        <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
          <div style={sx("padding:10px 16px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>CONTROL PLANE</div>
          {vm.secControls.map((ct, i) => (
            <div key={i} style={sx('display:flex;align-items:center;gap:12px;padding:11px 16px;border-bottom:1px solid #12151F')}>
              <div style={sx('flex:1;min-width:0')}>
                <div style={sx('font-size:12.5px;font-weight:500;color:#E9EBF2')}>{ct.name}</div>
                <div style={sx('font-size:11px;color:#77809A;margin-top:2px')}>{ct.desc}</div>
              </div>
              <span style={sx('display:flex;align-items:center;gap:6px;flex-shrink:0')}><span style={sx(`width:5px;height:5px;border-radius:50%;background: ${ct.stColor};box-shadow:0 0 6px ${ct.stColor}`)}></span><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;color: ${ct.stColor}`)}>{ct.stLabel}</span></span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
