import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Models({ vm }) {
  return (
    <div data-screen-label="Models" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Model Registry</h1>
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#77809A")}>ROUTING POLICY · ACTIVE</span>
      </div>
      <div style={sx('display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px;margin-bottom:12px;perspective:1200px')}>
        {vm.modelCards.map((mc, i) => (
          <Interactive
            key={i}
            onMouseMove={vm.tiltMove}
            onMouseLeave={vm.tiltLeave}
            style={sx('background:linear-gradient(180deg,#0E1119,#0B0D14);border:1px solid #1B2130;border-radius:10px;padding:15px 16px;will-change:transform;transition:border-color .2s,box-shadow .2s')}
            hoverStyle={sx('border-color:#2B3450;box-shadow:0 14px 34px rgba(0,0,0,0.45)')}
          >
            <div style={sx('display:flex;align-items:center;justify-content:space-between;margin-bottom:9px')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:12.5px;font-weight:600;color:#E9EBF2")}>{mc.name}</span>
              <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:2px 7px;border-radius:4px;color: ${mc.tagColor};border:1px solid ${mc.tagBd}`)}>{mc.tag}</span>
            </div>
            <div style={sx('font-size:11.5px;color:#8B93A7;line-height:1.5;margin-bottom:12px;min-height:34px')}>{mc.desc}</div>
            <div style={sx('display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px')}>
              <div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8px;letter-spacing:0.12em;color:#616A82")}>CTX</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#C7CBD6;margin-top:2px")}>{mc.ctx}</div></div>
              <div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8px;letter-spacing:0.12em;color:#616A82")}>P50 LAT</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#C7CBD6;margin-top:2px")}>{mc.latency}</div></div>
              <div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8px;letter-spacing:0.12em;color:#616A82")}>$/1M</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#C7CBD6;margin-top:2px")}>{mc.cost}</div></div>
            </div>
            <div style={sx('display:flex;align-items:center;gap:8px')}>
              <span style={sx('flex:1;height:4px;background:#141826;border-radius:2px;overflow:hidden')}><span style={sx(`display:block;height:100%;background:var(--accent,#3D6BFF);width: ${mc.shareW};box-shadow:0 0 8px color-mix(in oklab,var(--accent,#3D6BFF) 60%,transparent)`)}></span></span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#77809A;font-variant-numeric:tabular-nums")}>{mc.share} OF TRAFFIC</span>
            </div>
          </Interactive>
        ))}
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
        <div style={sx("padding:10px 16px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>ROUTING POLICY</div>
        <div style={sx("display:grid;grid-template-columns:minmax(150px,1.6fr) 130px 130px minmax(160px,2fr);gap:0 12px;padding:8px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#77809A;background:rgba(255,255,255,0.015)")}>
          <span>TASK CLASS</span><span>PRIMARY</span><span>FALLBACK</span><span>POLICY NOTE</span>
        </div>
        {vm.routeRows.map((rr, i) => (
          <div key={i} style={sx('display:grid;grid-template-columns:minmax(150px,1.6fr) 130px 130px minmax(160px,2fr);gap:0 12px;align-items:center;padding:8px 16px;border-bottom:1px solid #12151F')}>
            <span style={sx('font-size:12px;font-weight:500;color:#E9EBF2')}>{rr.cls}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:var(--accent,#3D6BFF)")}>{rr.model}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#9AA1B2")}>{rr.fallback}</span>
            <span style={sx('font-size:11px;color:#8B93A7')}>{rr.note}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
