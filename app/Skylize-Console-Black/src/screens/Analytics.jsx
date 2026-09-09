import React from 'react';
import { sx } from '../lib/style.jsx';

export default function Analytics({ vm }) {
  return (
    <div data-screen-label="Analytics" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Cost Intelligence</h1>
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#77809A")}>LAST 30 DAYS · USD</span>
      </div>
      <div style={sx('display:grid;grid-template-columns:repeat(auto-fit,minmax(186px,1fr));gap:12px;margin-bottom:12px')}>
        {vm.anaCards.map((c, i) => (
          <div key={i} style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:13px 15px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#77809A;margin-bottom:8px")}>{c.label}</div>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:22px;font-weight:600;color:#E9EBF2;font-variant-numeric:tabular-nums;letter-spacing:-0.02em")}>{c.value}</div>
            <div style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;margin-top:6px;color: ${c.deltaColor}`)}>{c.delta}</div>
          </div>
        ))}
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:15px 18px;margin-bottom:12px')}>
        <div style={sx('display:flex;justify-content:space-between;margin-bottom:12px')}>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>DAILY SPEND · 30D</span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#77809A;font-variant-numeric:tabular-nums")}>PEAK {vm.spendPeak}</span>
        </div>
        <svg width="100%" height="150" viewBox="0 0 600 150" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="skv2spend" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent,#3D6BFF)" stopOpacity="0.35"></stop>
              <stop offset="100%" stopColor="var(--accent,#3D6BFF)" stopOpacity="0"></stop>
            </linearGradient>
          </defs>
          <polygon points={vm.spendArea} fill="url(#skv2spend)"></polygon>
          <polyline points={vm.spendPts} fill="none" stroke="var(--accent,#3D6BFF)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"></polyline>
        </svg>
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
        <div style={sx("display:grid;grid-template-columns:minmax(130px,1.6fr) 110px 90px 80px minmax(120px,1.4fr);gap:0 12px;padding:9px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#77809A;background:rgba(255,255,255,0.015)")}>
          <span>DEPARTMENT</span><span>TOKENS 24H</span><span>COST</span><span style={sx('text-align:right')}>TASKS</span><span>EFFICIENCY · TOK/TASK</span>
        </div>
        {vm.effRows.map((e, i) => (
          <div key={i} style={sx('display:grid;grid-template-columns:minmax(130px,1.6fr) 110px 90px 80px minmax(120px,1.4fr);gap:0 12px;align-items:center;padding:7px 16px;border-bottom:1px solid #12151F')}>
            <span style={sx('display:flex;align-items:center;gap:7px;min-width:0')}><span style={sx(`width:6px;height:6px;border-radius:2px;background: ${e.color};flex-shrink:0`)}></span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#C7CBD6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{e.name}</span></span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2;font-variant-numeric:tabular-nums")}>{e.tokFmt}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#E9EBF2;font-variant-numeric:tabular-nums")}>{e.cost}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2;text-align:right;font-variant-numeric:tabular-nums")}>{e.tasks}</span>
            <span style={sx('display:flex;align-items:center;gap:8px')}><span style={sx('flex:1;height:4px;background:#141826;border-radius:2px;overflow:hidden')}><span style={sx(`display:block;height:100%;background: ${e.effColor};width: ${e.effW}`)}></span></span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#9AA1B2;font-variant-numeric:tabular-nums;width:34px;text-align:right")}>{e.eff}</span></span>
          </div>
        ))}
      </div>
    </div>
  );
}
