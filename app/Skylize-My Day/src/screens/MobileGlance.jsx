import React from 'react';
import { sx } from '../lib/style.jsx';

export default function MobileGlance({ vm }) {
  return (
    <div data-screen-label="Mobile brief" style={sx('flex:1;min-height:0;overflow-y:auto;display:flex;align-items:flex-start;justify-content:center;gap:40px;padding:30px 24px 90px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('width:384px;flex-shrink:0;background:#0B0C0E;border-radius:54px;padding:11px;box-shadow:0 24px 60px rgba(8,9,10,0.18)')}>
        <div style={sx('background:#F7F8F8;border-radius:44px;overflow:hidden;height:780px;display:flex;flex-direction:column')}>
          <div style={sx('display:flex;align-items:center;justify-content:space-between;padding:14px 26px 6px;flex-shrink:0')}>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:12px;font-weight:600;color:#08090A;font-variant-numeric:tabular-nums")}>8:02</span>
            <span style={sx('width:88px;height:24px;border-radius:14px;background:#0B0C0E')}></span>
            <span style={sx('display:flex;align-items:center;gap:4px')}>
              <svg width="14" height="10" viewBox="0 0 16 12" fill="#08090A"><rect x="0" y="7" width="2.5" height="4" rx="0.8"></rect><rect x="4" y="5" width="2.5" height="6" rx="0.8"></rect><rect x="8" y="3" width="2.5" height="8" rx="0.8"></rect><rect x="12" y="1" width="2.5" height="10" rx="0.8" opacity="0.35"></rect></svg>
              <svg width="20" height="10" viewBox="0 0 22 11"><rect x="0.5" y="0.5" width="17" height="10" rx="2.5" fill="none" stroke="#08090A" strokeOpacity="0.4"></rect><rect x="2" y="2" width="12" height="7" rx="1.2" fill="#08090A"></rect><rect x="19" y="3.5" width="2" height="4" rx="1" fill="#08090A" fillOpacity="0.4"></rect></svg>
            </span>
          </div>
          <div style={sx('flex:1;overflow-y:auto;padding:14px 20px 30px')}>
            <div style={sx('display:flex;align-items:center;gap:8px')}>
              <svg width="17" height="17" viewBox="0 0 20 20"><rect x="0.5" y="0.5" width="19" height="19" rx="4.5" fill="var(--accent,#0047FF)"></rect><path d="M5 6.5h10M7.2 10h7.8M9.4 13.5h5.6" stroke="#FFFFFF" strokeWidth="1.5" strokeLinecap="round"></path></svg>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.14em;color:#8A8F96;white-space:nowrap")}>MY DAY · GLANCE</span>
              <span style={sx('flex:1')}></span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8px;letter-spacing:0.1em;color:#B7BDC4")}>READ-ONLY</span>
            </div>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.16em;color:#8A8F96;margin-top:18px")}>{vm.dateLine}</div>
            <div style={sx('font-size:20px;font-weight:600;letter-spacing:-0.02em;margin-top:6px')}>{vm.greeting}</div>
            <div style={sx('font-size:12px;color:#55595E;line-height:1.6;margin-top:6px')}>{vm.mSummary}</div>
            <div style={sx("display:flex;align-items:center;gap:5px;margin-top:5px;font-family:'Geist Mono',ui-monospace,monospace;font-size:7.5px;letter-spacing:0.12em;color:#B7BDC4")}><span style={sx('font-size:9px')}>✳</span>WRITTEN BY YOUR AGENT</div>
            <div style={sx('display:flex;align-items:center;gap:8px;margin:20px 0 9px')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.14em;color:#8A8F96;white-space:nowrap")}>NEEDS YOUR DECISION</span><span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
            </div>
            {vm.decEmptyShow && (
              <div style={sx('border:1px dashed #DADDE2;border-radius:6px;padding:12px 13px;display:flex;align-items:center;gap:8px')}><span style={sx('width:5px;height:5px;border-radius:50%;background:#0F7B3A')}></span><span style={sx('font-size:11.5px;color:#6E7378')}>{vm.decEmptyTitle}</span></div>
            )}
            <div style={sx('display:flex;flex-direction:column;gap:8px')}>
              {vm.decItems.map((md) => (
                <div key={md.key} style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:12px 13px')}>
                  <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:7.5px;letter-spacing:0.1em;padding:2px 6px;border-radius:3px;white-space:nowrap;color: ${md.chipC};background: ${md.chipBg};border:1px solid ${md.chipBd}`)}>{md.chipText}</span>
                  <div style={sx('font-size:13px;font-weight:600;line-height:1.45;margin-top:8px')}>{md.title}</div>
                  <div style={sx('font-size:11px;color:#8A8F96;line-height:1.55;margin-top:4px')}>{md.why}</div>
                  {md.actionsShow && (
                    <div style={sx('display:flex;gap:7px;margin-top:11px')}>
                      <button onClick={md.approve} style={sx("flex:1;height:34px;border-radius:6px;border:none;background:var(--accent,#0047FF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer")}>{md.approveL}</button>
                      <button onClick={md.reject} style={sx("flex:1;height:34px;border-radius:6px;border:1px solid #DADDE2;background:transparent;color:#6E7378;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer")}>{md.rejectL}</button>
                    </div>
                  )}
                  {md.resolvedShow && (
                    <div style={sx(`display:flex;align-items:center;gap:6px;margin-top:10px;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.05em;color: ${md.resolvedC}`)}><span style={sx(`width:4px;height:4px;border-radius:50%;background: ${md.resolvedC}`)}></span>{md.resolvedText}</div>
                  )}
                </div>
              ))}
            </div>
            <div style={sx('display:flex;align-items:center;gap:8px;margin:20px 0 9px')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.14em;color:#8A8F96;white-space:nowrap")}>DONE WHILE YOU WERE AWAY</span><span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
            </div>
            <div style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;overflow:hidden')}>
              {vm.mDone.map((mr, i) => (
                <div key={i} style={sx('display:flex;align-items:center;gap:9px;padding:10px 13px;border-bottom:1px solid #F0F2F4')}>
                  <span style={sx(`width:5px;height:5px;border-radius:50%;background:var(--accent,#0047FF);flex-shrink:0;opacity: ${mr.dotOp}`)}></span>
                  <span style={sx('flex:1;font-size:12px;font-weight:500;line-height:1.45')}>{mr.h}</span>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;color:#A2A7AD;font-variant-numeric:tabular-nums")}>{mr.t}</span>
                </div>
              ))}
            </div>
            <div style={sx("display:flex;align-items:center;gap:8px;margin-top:18px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;color:#A2A7AD;font-variant-numeric:tabular-nums")}>
              <span>{vm.costSpent} {vm.costSpentLabel}</span><span>·</span><span style={sx('color:#0F7B3A')}>{vm.costLeft} headroom</span>
            </div>
            <div style={sx('margin-top:16px;font-size:10.5px;color:#B7BDC4;line-height:1.6;text-align:center')}>Approvals work here. Everything else — including marking the morning as seen — waits for your laptop.</div>
          </div>
        </div>
      </div>
      <div style={sx('width:230px;padding-top:26px')}>
        <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:var(--accent,#0047FF)")}>MOBILE GLANCE</div>
        <div style={sx('font-size:13px;color:#55595E;line-height:1.7;margin-top:10px')}>The brief, read-only, for the walk to work. Approvals stay actionable — everything else waits for the laptop. Same fixed section order, so the scanning habit transfers.</div>
      </div>
    </div>
  );
}
