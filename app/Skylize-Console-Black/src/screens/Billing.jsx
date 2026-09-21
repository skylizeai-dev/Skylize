import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

const UNAVAILABLE_LABEL = {
  plan_tier: 'Plan tier',
  invoices: 'Invoices',
  seats: 'Operator seats',
  agent_slots: 'Agent slots',
};

// REAL ai_cost_ledger usage only. Plan tier, invoices, seats and agent-slot
// caps have NO backing table anywhere in this repo, so they render as an
// explicit "not available yet" list rather than the old Enterprise-Plan card
// and invoice table. A zero-spend period is a real, true answer -- see
// useConsoleState.js's `billingModelRows`/`billingHistoryRows`.
export default function Billing({ vm }) {
  return (
    <div data-screen-label="Billing" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Billing &amp; Usage</h1>
        {vm.billingPeriod && (
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#77809A")}>PERIOD {vm.billingPeriod}</span>
        )}
      </div>

      {vm.billingLoading ? (
        <div style={sx('font-size:12px;color:#77809A')}>Reading usage&hellip;</div>
      ) : vm.billingError ? (
        <div role="alert" style={sx("background:#0C0F16;border:1px solid rgba(225,90,82,0.35);border-radius:10px;padding:12px 14px;font-size:11.5px;color:#E9A9A4;margin-bottom:12px")}>{vm.billingError}</div>
      ) : (
        <>
          <div style={sx('display:grid;grid-template-columns:repeat(auto-fit,minmax(186px,1fr));gap:12px;margin-bottom:12px')}>
            <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:13px 15px')}>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#77809A;margin-bottom:8px")}>THIS PERIOD</div>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:22px;font-weight:600;color:#E9EBF2;font-variant-numeric:tabular-nums")}>{vm.spendMTD}</div>
            </div>
            <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:13px 15px')}>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#77809A;margin-bottom:8px")}>GOVERNANCE CEILING</div>
              {vm.billingCeilingConfigured ? (
                <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:22px;font-weight:600;color:#E9EBF2;font-variant-numeric:tabular-nums")}>{vm.billingCeiling}</div>
              ) : (
                <div style={sx('font-size:12px;color:#77809A')}>No spend ceiling configured</div>
              )}
            </div>
          </div>

          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden;margin-bottom:12px')}>
            <div style={sx("padding:10px 16px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>SPEND BY MODEL &middot; THIS PERIOD</div>
            {vm.billingModelRows.length === 0 ? (
              <div style={sx('padding:16px;font-size:12px;color:#77809A')}>No spend recorded this period.</div>
            ) : (
              vm.billingModelRows.map((m, i) => (
                <Interactive key={i} style={sx('display:flex;justify-content:space-between;padding:9px 16px;border-bottom:1px solid #12151F')}>
                  <span style={sx('font-size:12px;color:#C7CBD6')}>{m.name}</span>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#E9EBF2;font-variant-numeric:tabular-nums")}>{m.cost}</span>
                </Interactive>
              ))
            )}
          </div>

          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden;margin-bottom:12px')}>
            <div style={sx("padding:10px 16px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>HISTORY</div>
            {vm.billingHistoryRows.map((h, i) => (
              <div key={i} style={sx('display:flex;justify-content:space-between;padding:9px 16px;border-bottom:1px solid #12151F')}>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#77809A")}>{h.period}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#C7CBD6;font-variant-numeric:tabular-nums")}>{h.cost}</span>
              </div>
            ))}
          </div>
        </>
      )}

      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:14px 16px')}>
        <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;margin-bottom:8px")}>NOT AVAILABLE YET</div>
        {vm.billingUnavailable.map((key, i) => (
          <div key={i} style={sx('font-size:11.5px;color:#616A82;padding:4px 0')}>{UNAVAILABLE_LABEL[key] || key}</div>
        ))}
      </div>
    </div>
  );
}
