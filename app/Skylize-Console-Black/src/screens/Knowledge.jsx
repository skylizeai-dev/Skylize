import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Knowledge({ vm }) {
  return (
    <div data-screen-label="Knowledge" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Knowledge & Memory</h1>
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#77809A")}>GOVERNED MEMORY · {vm.kbTotalDocs} OBJECTS</span>
      </div>
      <div style={sx('display:grid;grid-template-columns:repeat(auto-fit,minmax(186px,1fr));gap:12px;margin-bottom:12px')}>
        {vm.kbStats.map((s, i) => (
          <div key={i} style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:13px 15px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#77809A;margin-bottom:8px")}>{s.label}</div>
            <div style={sx('display:flex;align-items:baseline;gap:6px')}><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:22px;font-weight:600;color: ${s.color};font-variant-numeric:tabular-nums`)}>{s.value}</span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A")}>{s.sub}</span></div>
          </div>
        ))}
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
        <div style={sx("display:grid;grid-template-columns:minmax(150px,1.8fr) 100px 90px 100px minmax(140px,1.6fr) 90px;gap:0 12px;padding:9px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#77809A;background:rgba(255,255,255,0.015)")}>
          <span>SOURCE</span><span>TYPE</span><span>OBJECTS</span><span>FRESHNESS</span><span>EMBEDDING COVERAGE</span><span style={sx('text-align:right')}>STATUS</span>
        </div>
        {vm.kbRows.map((k, i) => (
          <Interactive
            key={i}
            style={sx('display:grid;grid-template-columns:minmax(150px,1.8fr) 100px 90px 100px minmax(140px,1.6fr) 90px;gap:0 12px;align-items:center;padding:9px 16px;border-bottom:1px solid #12151F;transition:background-color .12s')}
            hoverStyle={sx('background-color:rgba(255,255,255,0.02)')}
          >
            <span style={sx('font-size:12.5px;font-weight:500;color:#E9EBF2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{k.name}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color:#8B93A7")}>{k.type}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2;font-variant-numeric:tabular-nums")}>{k.docs}</span>
            <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color: ${k.freshColor};font-variant-numeric:tabular-nums`)}>{k.fresh}</span>
            <span style={sx('display:flex;align-items:center;gap:8px')}><span style={sx('flex:1;height:4px;background:#141826;border-radius:2px;overflow:hidden')}><span style={sx(`display:block;height:100%;background:var(--accent,#3D6BFF);width: ${k.covW}`)}></span></span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#9AA1B2;font-variant-numeric:tabular-nums;width:32px;text-align:right")}>{k.cov}</span></span>
            <span style={sx('display:flex;align-items:center;gap:5px;justify-content:flex-end')}><span style={sx(`width:5px;height:5px;border-radius:50%;background: ${k.stColor};box-shadow:0 0 6px ${k.stColor}`)}></span><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color: ${k.stColor}`)}>{k.status}</span></span>
          </Interactive>
        ))}
      </div>
    </div>
  );
}
