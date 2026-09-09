import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function TopBar({ vm }) {
  return (
    <header style={sx('position:relative;display:flex;align-items:center;justify-content:space-between;height:50px;flex-shrink:0;padding:0 16px 0 14px;border-bottom:1px solid #161A26;background:rgba(10,12,18,0.72);backdrop-filter:blur(14px);z-index:40')}>
      <div style={sx('display:flex;align-items:center;gap:11px;min-width:0')}>
        <div style={sx('display:flex;align-items:center;gap:9px')}>
          <div style={sx('position:relative;width:20px;height:20px')}>
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="0.5" y="0.5" width="19" height="19" rx="4.5" fill="var(--accent,#3D6BFF)"></rect><path d="M5 6.5h10M7.2 10h7.8M9.4 13.5h5.6" stroke="#FFFFFF" strokeWidth="1.5" strokeLinecap="round"></path></svg>
            <div style={sx('position:absolute;inset:-4px;border-radius:8px;background:var(--accent,#3D6BFF);opacity:0.28;filter:blur(10px);z-index:-1')}></div>
          </div>
          <span style={sx('font-size:13.5px;font-weight:600;letter-spacing:-0.01em')}>Skylize</span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#8B93A7;border:1px solid #222839;border-radius:4px;padding:2px 6px;background:rgba(255,255,255,0.02)")}>CONSOLE v2</span>
        </div>
        <span style={sx('color:#2A3040')}>/</span>
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;letter-spacing:0.1em;color:#9AA1B2;text-transform:uppercase")}>{vm.crumb}</span>
      </div>
      <Interactive
        as="button"
        onClick={vm.goChat}
        style={sx("display:flex;align-items:center;gap:8px;height:29px;padding:0 12px;border:1px solid #222839;border-radius:6px;background:rgba(255,255,255,0.025);color:#8B93A7;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.08em;cursor:pointer;transition:border-color .15s,color .15s")}
        hoverStyle={sx('border-color:var(--accent,#3D6BFF);color:#E9EBF2')}
      >
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><circle cx="7" cy="7" r="4"></circle><path d="M10 10l3.5 3.5"></path></svg>
        ISSUE A DIRECTIVE
        <span style={sx('border:1px solid #262c3e;border-radius:3px;padding:1px 4px;font-size:9px;color:#77809A')}>⌘K</span>
      </Interactive>
      <div style={sx('display:flex;align-items:center;gap:14px')}>
        <span style={sx('display:flex;align-items:center;gap:6px')}>
          <span style={sx('width:6px;height:6px;border-radius:50%;background:#34C579;box-shadow:0 0 8px rgba(52,197,121,0.7);animation:pulseDot 2.2s ease-in-out infinite')}></span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#9AA1B2;font-variant-numeric:tabular-nums")}>{vm.activeCount} executing</span>
        </span>
        <span style={sx('width:1px;height:15px;background:#1B2130')}></span>
        <span style={sx('display:flex;align-items:baseline;gap:5px')}>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#E9EBF2;font-variant-numeric:tabular-nums;font-weight:500")}>{vm.tokRateFmt}</span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#77809A")}>TOK/HR</span>
        </span>
        <span style={sx('display:flex;align-items:baseline;gap:5px')}>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#E9EBF2;font-variant-numeric:tabular-nums;font-weight:500")}>{vm.costToday}</span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#77809A")}>TODAY</span>
        </span>
        <span style={sx('width:1px;height:15px;background:#1B2130')}></span>
        <Interactive
          as="button"
          onClick={vm.toggleNotif}
          aria-label="Notifications"
          style={sx('display:flex;align-items:center;justify-content:center;width:27px;height:27px;border:1px solid transparent;border-radius:6px;background:transparent;color:#8B93A7;cursor:pointer;transition:border-color .15s,color .15s;padding:0')}
          hoverStyle={sx('border-color:#222839;color:#E9EBF2')}
        >
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"><path d="M8 2.5a4 4 0 0 1 4 4v2.6l1 1.9H3l1-1.9V6.5a4 4 0 0 1 4-4z"></path><path d="M6.8 13a1.3 1.3 0 0 0 2.4 0"></path></svg>
        </Interactive>
        <div style={sx("display:flex;align-items:center;justify-content:center;width:27px;height:27px;border:1px solid #222839;border-radius:6px;background:linear-gradient(135deg,color-mix(in oklab,var(--accent,#3D6BFF) 30%,transparent),rgba(255,255,255,0.03));font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;font-weight:500;color:#E9EBF2")}>OP</div>
      </div>
      {vm.notifOpen && (
        <div style={sx('position:absolute;top:46px;right:16px;width:310px;background:rgba(14,17,25,0.92);backdrop-filter:blur(18px);border:1px solid #232939;border-radius:8px;box-shadow:0 18px 50px rgba(0,0,0,0.55);animation:fadeSlide .16s ease-out;overflow:hidden;z-index:60')}>
          <div style={sx("padding:9px 12px 7px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.14em;color:#77809A;border-bottom:1px solid #1B2130")}>NOTIFICATIONS</div>
          {vm.notifItems.map((n, i) => (
            <div key={i} style={sx('display:flex;align-items:baseline;gap:8px;padding:9px 12px;border-bottom:1px solid #141826;font-size:12px;color:#C7CBD6')}>
              <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${n.dot};flex-shrink:0;position:relative;top:-1px`)}></span>
              <span style={sx('flex:1;line-height:1.45')}>{n.text}</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;font-variant-numeric:tabular-nums")}>{n.when}</span>
            </div>
          ))}
        </div>
      )}
    </header>
  );
}
