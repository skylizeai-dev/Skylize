import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Integrations({ vm }) {
  return (
    <div data-screen-label="Integrations" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Integrations</h1>
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#77809A")}>{vm.integConnN} CONNECTED · SCOPED VIA TOOL PROXY</span>
      </div>
      <div style={sx('display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;perspective:1200px')}>
        {vm.integCards.map((ic, i) => (
          <Interactive
            key={i}
            onMouseMove={vm.tiltMove}
            onMouseLeave={vm.tiltLeave}
            style={sx('background:linear-gradient(180deg,#0E1119,#0B0D14);border:1px solid #1B2130;border-radius:10px;padding:14px 15px;will-change:transform;transition:border-color .2s,box-shadow .2s')}
            hoverStyle={sx('border-color:#2B3450;box-shadow:0 12px 30px rgba(0,0,0,0.4)')}
          >
            <div style={sx('display:flex;align-items:center;justify-content:space-between;margin-bottom:8px')}>
              <div style={sx('display:flex;align-items:center;gap:9px')}>
                <div style={sx(`display:flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:7px;border:1px solid #232939;background:rgba(255,255,255,0.03);font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;font-weight:600;color: ${ic.mColor}`)}>{ic.mono}</div>
                <span style={sx('font-size:12.5px;font-weight:600;color:#E9EBF2')}>{ic.name}</span>
              </div>
              <span style={sx('display:flex;align-items:center;gap:5px')}><span style={sx(`width:5px;height:5px;border-radius:50%;background: ${ic.stColor};box-shadow:0 0 6px ${ic.stColor}`)}></span></span>
            </div>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.08em;color:#77809A;margin-bottom:7px")}>{ic.cat}</div>
            <div style={sx('font-size:11px;color:#8B93A7;line-height:1.5;margin-bottom:11px;min-height:32px')}>{ic.scopes}</div>
            <Interactive
              as="button"
              style={sx(`height:26px;width:100%;border-radius:6px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;cursor:pointer;border:1px solid ${ic.btnBd};background: ${ic.btnBg};color: ${ic.btnC};transition:border-color .15s`)}
              hoverStyle={sx('border-color:#77809A')}
            >{ic.btnLabel}</Interactive>
          </Interactive>
        ))}
      </div>
    </div>
  );
}
