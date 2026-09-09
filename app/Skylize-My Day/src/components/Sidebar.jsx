import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Sidebar({ vm }) {
  return (
    <aside style={sx(`display:flex;flex-direction:column;flex-shrink:0;border-right:1px solid #E4E6EA;background:#F7F8F8;overflow:hidden;transition:width .24s cubic-bezier(0.2,0.8,0.2,1);width: ${vm.sidebarW}`)}>
      <nav style={sx('display:flex;flex-direction:column;padding:10px 8px;flex:1;overflow-y:auto;overflow-x:hidden')}>
        {vm.navGroups.map((g, gi) => (
          <div key={gi} style={sx('display:flex;flex-direction:column;gap:1px;margin-bottom:12px')}>
            <div style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.16em;color:#A2A7AD;padding:0 9px 5px;white-space:nowrap;display: ${vm.labelDisp}`)}>{g.label}</div>
            {g.items.map((it, ii) => (
              <Interactive
                key={ii}
                as="button"
                onClick={it.click}
                style={sx(`position:relative;display:flex;align-items:center;gap:10px;height:31px;padding:0 9px;border-radius:6px;border:1px solid transparent;width:100%;background: ${it.bg};color: ${it.c};cursor:pointer;text-align:left;font-family:inherit;transition:background-color .15s,color .15s`)}
                hoverStyle={sx('border-color:#DADDE2')}
              >
                <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}><path d={it.icon}></path></svg>
                <span style={sx(`flex:1;font-size:12.5px;font-weight:500;letter-spacing:-0.005em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display: ${vm.labelDisp}`)}>{it.label}</span>
                <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;color:#B45309;border:1px solid rgba(180,83,9,0.4);border-radius:3px;padding:1px 5px;font-variant-numeric:tabular-nums;display: ${it.badgeDisp}`)}>{it.badge}</span>
              </Interactive>
            ))}
          </div>
        ))}
      </nav>
      <div style={sx(`padding:8px 11px;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.14em;color:#B7BDC4;white-space:nowrap;display: ${vm.labelDisp}`)}>ONLY YOU SEE THIS PAGE</div>
      <Interactive
        as="button"
        onClick={vm.toggleSidebar}
        aria-label="Toggle sidebar"
        style={sx('display:flex;align-items:center;justify-content:center;height:36px;border:none;border-top:1px solid #E4E6EA;background:transparent;color:#8A8F96;cursor:pointer;transition:color .15s')}
        hoverStyle={sx('color:#08090A')}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ transition: 'transform .24s cubic-bezier(0.2,0.8,0.2,1)', transform: vm.collapseTf }}><path d="M10 3.5L5.5 8l4.5 4.5"></path></svg>
      </Interactive>
    </aside>
  );
}
