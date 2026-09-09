import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function TopBar({ vm }) {
  return (
    <header style={sx('position:relative;display:flex;align-items:center;justify-content:space-between;height:50px;flex-shrink:0;padding:0 16px 0 14px;border-bottom:1px solid #E4E6EA;background:#F7F8F8;z-index:40')}>
      <div style={sx('display:flex;align-items:center;gap:11px;min-width:0')}>
        <div style={sx('display:flex;align-items:center;gap:9px')}>
          <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="0.5" y="0.5" width="19" height="19" rx="4.5" fill="var(--accent,#0047FF)"></rect><path d="M5 6.5h10M7.2 10h7.8M9.4 13.5h5.6" stroke="#FFFFFF" strokeWidth="1.5" strokeLinecap="round"></path></svg>
          <span style={sx('font-size:13.5px;font-weight:600;letter-spacing:-0.01em')}>Skylize</span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#6E7378;border:1px solid #DADDE2;border-radius:4px;padding:2px 6px;background:rgba(8,9,10,0.02);white-space:nowrap")}>MY DAY</span>
        </div>
        <span style={sx('color:#C6CAD1')}>/</span>
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;letter-spacing:0.1em;color:#55595E;text-transform:uppercase")}>{vm.crumb}</span>
      </div>
      <Interactive
        as="button"
        onClick={vm.goChat}
        style={sx("display:flex;align-items:center;gap:8px;height:29px;padding:0 12px;border:1px solid #DADDE2;border-radius:6px;background:rgba(8,9,10,0.025);color:#6E7378;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.08em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s")}
        hoverStyle={sx('border-color:var(--accent,#0047FF);color:#08090A')}
      >
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 2.5h10a1.5 1.5 0 0 1 1.5 1.5v5.5a1.5 1.5 0 0 1-1.5 1.5H7.2L4 13.5v-2.5H3a1.5 1.5 0 0 1-1.5-1.5V4A1.5 1.5 0 0 1 3 2.5z"></path></svg>
        ASK YOUR AGENT
        <span style={sx('border:1px solid #DADDE2;border-radius:3px;padding:1px 4px;font-size:9px;color:#8A8F96')}>⌘K</span>
      </Interactive>
      <div style={sx('display:flex;align-items:center;gap:14px')}>
        {vm.annotate && <span style={sx('display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--accent,#0047FF);color:#FFFFFF;font-family:\'Geist Mono\',ui-monospace,monospace;font-size:9px;flex-shrink:0')}>7</span>}
        <span className="skt">
          <span tabIndex={0} style={sx(`display:flex;align-items:center;gap:7px;height:25px;padding:0 9px;border-radius:5px;cursor:help;white-space:nowrap;border:1px solid ${vm.modeBd};background: ${vm.modeBg}`)}>
            <span style={sx(`width:6px;height:6px;border-radius:50%;flex-shrink:0;background: ${vm.agentDotC};animation: ${vm.agentDotAnim}`)}></span>
            <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;line-height:1;color: ${vm.modeC}`)}>{vm.modeGlyph}</span>
            <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.09em;font-variant-numeric:tabular-nums;color: ${vm.modeC}`)}>{vm.modeLabel}</span>
          </span>
          <span className="sktip" style={sx("left:auto;right:-8px;transform:none;background:#FFFFFF;border:1px solid #08090A;border-radius:4px;padding:8px 10px;font-family:'Geist',system-ui,sans-serif;font-size:11.5px;font-weight:400;line-height:1.5;color:#26292D;letter-spacing:0;text-transform:none;text-align:left;white-space:normal")}>{vm.modeTip}</span>
        </span>
        <span style={sx('width:1px;height:15px;background:#E4E6EA')}></span>
        <span style={sx('display:flex;align-items:baseline;gap:5px')}>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#08090A;font-variant-numeric:tabular-nums;font-weight:500")}>{vm.topBudget}</span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#8A8F96")}>LEFT THIS WEEK</span>
          <span className="skt">
            <span tabIndex={0} style={sx("display:inline-flex;align-items:center;justify-content:center;width:13px;height:13px;border:1px solid #C6CAD1;border-radius:3px;color:#8A8F96;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;cursor:help;line-height:1")}>i</span>
            <span className="sktip" style={sx("left:auto;right:-8px;transform:none;background:#FFFFFF;border:1px solid #08090A;border-radius:4px;padding:8px 10px;font-family:'Geist',system-ui,sans-serif;font-size:11.5px;font-weight:400;line-height:1.5;color:#26292D;letter-spacing:0;text-transform:none;text-align:left;white-space:normal")}>Your agent's spending headroom for the week. It can never spend past this — the same limit you have.</span>
          </span>
        </span>
        <span style={sx('width:1px;height:15px;background:#E4E6EA')}></span>
        <div style={sx("display:flex;align-items:center;justify-content:center;width:27px;height:27px;border:1px solid #DADDE2;border-radius:6px;background:color-mix(in srgb, var(--accent,#0047FF) 8%, #FFFFFF);font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;font-weight:500;color:#08090A")}>{vm.initials}</div>
      </div>
    </header>
  );
}
