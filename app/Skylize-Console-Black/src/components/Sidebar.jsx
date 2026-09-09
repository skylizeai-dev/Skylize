import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Sidebar({ vm }) {
  return (
    <aside style={sx(`display:flex;flex-direction:column;flex-shrink:0;border-right:1px solid #161A26;background:rgba(10,12,18,0.6);overflow:hidden;transition:width .24s cubic-bezier(0.2,0.8,0.2,1);width: ${vm.sidebarW}`)}>
      <nav style={sx('display:flex;flex-direction:column;padding:10px 8px;flex:1;overflow-y:auto;overflow-x:hidden')}>
        {vm.navGroups.map((g, gi) => (
          <div key={gi} style={sx('display:flex;flex-direction:column;gap:1px;margin-bottom:12px')}>
            <div style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.16em;color:#616A82;padding:0 9px 5px;white-space:nowrap;display: ${vm.labelDisp}`)}>{g.label}</div>
            {g.items.map((it, ii) => (
              <Interactive
                key={ii}
                as="button"
                onClick={it.click}
                style={sx(`position:relative;display:flex;align-items:center;gap:10px;height:31px;padding:0 9px;border-radius:6px;border:none;width:100%;background: ${it.bg};color: ${it.c};cursor:pointer;text-align:left;font-family:inherit;transition:background-color .15s,color .15s`)}
                hoverStyle={sx('background-color:rgba(255,255,255,0.045)')}
              >
                <span style={sx(`position:absolute;left:-8px;top:8px;width:2px;height:15px;border-radius:1px;background: ${it.edgeBg};box-shadow:0 0 8px ${it.edgeBg}`)}></span>
                <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}><path d={it.icon}></path></svg>
                <span style={sx(`flex:1;font-size:12.5px;font-weight:500;letter-spacing:-0.005em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display: ${vm.labelDisp}`)}>{it.label}</span>
                <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;color:#D19A3F;border:1px solid rgba(209,154,63,0.4);border-radius:3px;padding:1px 5px;font-variant-numeric:tabular-nums;display: ${it.badgeDisp}`)}>{it.badge}</span>
              </Interactive>
            ))}
          </div>
        ))}
      </nav>
      <div style={sx(`padding:8px 11px;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.14em;color:#3C4150;white-space:nowrap;display: ${vm.labelDisp}`)}>PRECISION ALTITUDE · v2.0</div>
      <Interactive
        as="button"
        onClick={vm.toggleSidebar}
        aria-label="Toggle sidebar"
        style={sx('display:flex;align-items:center;justify-content:center;height:36px;border:none;border-top:1px solid #161A26;background:transparent;color:#77809A;cursor:pointer;transition:color .15s')}
        hoverStyle={sx('color:#E9EBF2')}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ transition: 'transform .24s cubic-bezier(0.2,0.8,0.2,1)', transform: `rotate(${vm.chevRot})` }}><path d="M6 3.5L10.5 8L6 12.5"></path></svg>
      </Interactive>
    </aside>
  );
}
