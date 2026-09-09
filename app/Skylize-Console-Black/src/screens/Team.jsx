import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Team({ vm }) {
  return (
    <div data-screen-label="Team" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Team & Access</h1>
        <button style={sx("height:29px;padding:0 12px;border-radius:6px;border:none;background:var(--accent,#3D6BFF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;cursor:pointer;box-shadow:0 0 14px color-mix(in oklab,var(--accent,#3D6BFF) 35%,transparent)")}>+ INVITE</button>
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden;margin-bottom:12px')}>
        <div style={sx("display:grid;grid-template-columns:minmax(180px,2fr) minmax(160px,1.6fr) 110px 90px 70px;gap:0 12px;padding:9px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#77809A;background:rgba(255,255,255,0.015)")}>
          <span>MEMBER</span><span>EMAIL</span><span>ROLE</span><span>LAST ACTIVE</span><span style={sx('text-align:right')}>MFA</span>
        </div>
        {vm.memberRows.map((mr, i) => (
          <Interactive
            key={i}
            style={sx('display:grid;grid-template-columns:minmax(180px,2fr) minmax(160px,1.6fr) 110px 90px 70px;gap:0 12px;align-items:center;padding:8px 16px;border-bottom:1px solid #12151F;transition:background-color .12s')}
            hoverStyle={sx('background-color:rgba(255,255,255,0.02)')}
          >
            <span style={sx('display:flex;align-items:center;gap:10px;min-width:0')}>
              <span style={sx("display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:6px;border:1px solid #232939;background:rgba(255,255,255,0.03);font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#C7CBD6;flex-shrink:0")}>{mr.initials}</span>
              <span style={sx('font-size:12.5px;font-weight:500;color:#E9EBF2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{mr.name}</span>
            </span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#77809A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{mr.email}</span>
            <span><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:2px 7px;border-radius:4px;color: ${mr.roleColor};background: ${mr.roleBg}`)}>{mr.role}</span></span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;font-variant-numeric:tabular-nums")}>{mr.last}</span>
            <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;text-align:right;color: ${mr.mfaColor}`)}>{mr.mfa}</span>
          </Interactive>
        ))}
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
        <div style={sx("padding:10px 16px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>ROLE PERMISSIONS</div>
        <div style={sx("display:grid;grid-template-columns:minmax(190px,2fr) 90px 90px 90px 90px;gap:0 10px;padding:8px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#77809A;background:rgba(255,255,255,0.015)")}>
          <span>PERMISSION</span><span style={sx('text-align:center')}>OWNER</span><span style={sx('text-align:center')}>ADMIN</span><span style={sx('text-align:center')}>OPERATOR</span><span style={sx('text-align:center')}>AUDITOR</span>
        </div>
        {vm.permRows.map((pr, i) => (
          <div key={i} style={sx('display:grid;grid-template-columns:minmax(190px,2fr) 90px 90px 90px 90px;gap:0 10px;align-items:center;padding:8px 16px;border-bottom:1px solid #12151F')}>
            <span style={sx('font-size:12px;color:#C7CBD6')}>{pr.name}</span>
            {pr.cells.map((cell, ci) => (
              <span key={ci} style={sx('display:flex;justify-content:center')}><span style={sx(`width:7px;height:7px;border-radius:50%;background: ${cell.bg};box-shadow: ${cell.sh}`)}></span></span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
