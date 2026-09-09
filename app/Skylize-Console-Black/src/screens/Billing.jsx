import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Billing({ vm }) {
  return (
    <div data-screen-label="Billing" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Billing & Usage</h1>
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#77809A")}>CYCLE RENEWS {vm.planRenews}</span>
      </div>
      <div style={sx('display:grid;grid-template-columns:minmax(280px,1.1fr) minmax(0,1.6fr);gap:12px;align-items:start;margin-bottom:12px')}>
        <div style={sx('position:relative;background:linear-gradient(135deg,color-mix(in oklab,var(--accent,#3D6BFF) 16%,#0C0F16),#0B0D14);border:1px solid #2B3450;border-radius:10px;padding:17px 18px;overflow:hidden')}>
          <div style={sx('position:absolute;inset:0;background:radial-gradient(300px 160px at 85% 0%, color-mix(in oklab,var(--accent,#3D6BFF) 22%,transparent), transparent 70%)')}></div>
          <div style={sx('position:relative')}>
            <div style={sx('display:flex;align-items:center;justify-content:space-between')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.14em;color:color-mix(in oklab,var(--accent,#3D6BFF) 70%,#FFFFFF)")}>ENTERPRISE PLAN</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.08em;color:#34C579")}>ACTIVE</span>
            </div>
            <div style={sx('font-size:21px;font-weight:600;letter-spacing:-0.02em;margin-top:10px')}>Custom agreement</div>
            <div style={sx('font-size:11.5px;color:#9AA1B2;margin-top:4px;line-height:1.55')}>Annual commit · dedicated capacity · EU data residency · 99.9% SLA</div>
            <div style={sx('display:flex;gap:14px;margin-top:14px')}>
              <div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.12em;color:#77809A")}>MTD SPEND</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:15px;font-weight:600;margin-top:3px;font-variant-numeric:tabular-nums")}>{vm.spendMTD}</div></div>
              <div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.12em;color:#77809A")}>FORECAST</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:15px;font-weight:600;margin-top:3px;font-variant-numeric:tabular-nums")}>{vm.forecastFmt}</div></div>
            </div>
          </div>
        </div>
        <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:15px 18px;display:flex;flex-direction:column;gap:14px')}>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>COMMIT UTILIZATION</span>
          {vm.meters.map((mt, i) => (
            <div key={i}>
              <div style={sx("display:flex;justify-content:space-between;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color:#9AA1B2;margin-bottom:6px")}><span>{mt.label}</span><span style={sx('font-variant-numeric:tabular-nums')}>{mt.used} / {mt.cap}</span></div>
              <div style={sx('height:6px;background:#141826;border-radius:3px;overflow:hidden')}><div style={sx(`height:100%;border-radius:3px;background: ${mt.color};width: ${mt.w};box-shadow:0 0 10px ${mt.color}`)}></div></div>
            </div>
          ))}
        </div>
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
        <div style={sx("padding:10px 16px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>INVOICES</div>
        {vm.invoices.map((inv, i) => (
          <Interactive
            key={i}
            style={sx('display:grid;grid-template-columns:120px minmax(140px,1.5fr) 110px 90px 80px;gap:0 12px;align-items:center;padding:9px 16px;border-bottom:1px solid #12151F;transition:background-color .12s')}
            hoverStyle={sx('background-color:rgba(255,255,255,0.02)')}
          >
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#C7CBD6")}>{inv.id}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A")}>{inv.period}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;font-weight:600;color:#E9EBF2;font-variant-numeric:tabular-nums")}>{inv.amount}</span>
            <span style={sx('display:flex;align-items:center;gap:5px')}><span style={sx(`width:5px;height:5px;border-radius:50%;background: ${inv.stColor}`)}></span><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color: ${inv.stColor}`)}>{inv.status}</span></span>
            <Interactive as="button" style={sx("height:24px;padding:0 10px;border-radius:5px;border:1px solid #262D40;background:transparent;color:#8B93A7;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.06em;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:#77809A;color:#E9EBF2')}>PDF ↓</Interactive>
          </Interactive>
        ))}
      </div>
    </div>
  );
}
