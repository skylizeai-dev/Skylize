import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function PastTasks({ vm }) {
  return (
    <div data-screen-label="Past tasks" style={sx('flex:1;min-height:0;overflow-y:auto;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('max-width:720px;margin:0 auto;padding:34px 36px 90px')}>
        <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.18em;color:#8A8F96")}>THE RECORD · KEPT FOR YOU, NOT ABOUT YOU</div>
        <h1 style={sx('margin:11px 0 0;font-size:22px;font-weight:600;letter-spacing:-0.02em;line-height:1.3')}>Past tasks</h1>
        <div style={sx('font-size:13px;color:#55595E;line-height:1.65;margin-top:8px;max-width:580px')}>Every piece of work your agent finished, and every call you made on the rest — newest day first. Nothing here can be edited, by you or by it.</div>

        <div style={sx('display:flex;align-items:center;gap:6px;margin-top:22px;flex-wrap:wrap')}>
          {vm.pastFilters.map((pf, i) => (
            <Interactive key={i} as="button" onClick={pf.pick} style={sx(`height:26px;padding:0 11px;border-radius:6px;border:1px solid ${pf.bd};background: ${pf.bg};color: ${pf.c};font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s`)} hoverStyle={sx('border-color:#C7CAD1')}>{pf.label}</Interactive>
          ))}
          <span style={sx('flex:1;min-width:12px')}></span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.1em;color:#A2A7AD;font-variant-numeric:tabular-nums;white-space:nowrap")}>{vm.pastCount}</span>
        </div>

        <div style={sx('display:flex;flex-direction:column;gap:10px;margin-top:14px')}>
          {vm.pastDays.map((pd, pi) => (
            <div key={pd.label ?? pi} style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;overflow:hidden')}>
              <Interactive as="button" onClick={pd.toggle} style={sx('display:flex;align-items:center;gap:10px;width:100%;padding:12px 15px;border:none;background:transparent;cursor:pointer;text-align:left;font-family:inherit;transition:background-color .15s')} hoverStyle={sx('background-color:rgba(8,9,10,0.015)')}>
                <span style={sx('font-size:13px;font-weight:600;letter-spacing:-0.01em;white-space:nowrap')}>{pd.label}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color:#A2A7AD;font-variant-numeric:tabular-nums;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")}>{pd.meta}</span>
                <span style={sx('flex:1;min-width:8px')}></span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#8A8F96;font-variant-numeric:tabular-nums;white-space:nowrap")}>{pd.cost}</span>
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="#A2A7AD" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, transition: 'transform .2s ease', transform: pd.chev }}><path d="M4 6.5L8 10.5l4-4"></path></svg>
              </Interactive>
              <div style={sx(`flex-direction:column;border-top:1px solid #F0F2F4;display: ${pd.rowsDisp}`)}>
                {pd.rows.map((pr, ri) => (
                  <div key={ri} style={sx('display:flex;align-items:center;gap:10px;padding:9px 15px;border-bottom:1px solid #F5F6F8')}>
                    <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#A2A7AD;font-variant-numeric:tabular-nums;flex-shrink:0;width:38px")}>{pr.t}</span>
                    <span style={sx(`width:5px;height:5px;border-radius:50%;flex-shrink:0;background: ${pr.dot}`)}></span>
                    <span style={sx('flex:1;min-width:0;font-size:12.5px;color:#26292D;line-height:1.5')}>{pr.h}</span>
                    <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.08em;padding:2px 6px;border-radius:3px;white-space:nowrap;flex-shrink:0;color: ${pr.chipC};background: ${pr.chipBg};border:1px solid ${pr.chipBd};display: ${pr.chipDisp}`)}>{pr.chip}</span>
                    <Interactive as="button" onClick={pr.open} style={sx("border:none;background:none;padding:0;flex-shrink:0;color:#B7BDC4;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;cursor:pointer;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>TRACE</Interactive>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {vm.pastEmptyShow && (
          <div style={sx('border:1px dashed #DADDE2;border-radius:6px;padding:20px 18px;text-align:center;font-size:12.5px;color:#8A8F96')}>{vm.pastEmptyText}</div>
        )}

        <div style={sx("display:flex;align-items:center;gap:10px;margin-top:18px;padding-top:14px;border-top:1px solid #E4E6EA;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color:#A2A7AD;font-variant-numeric:tabular-nums;flex-wrap:wrap")}>
          <span>{vm.pastTotal}</span><span>·</span><span>{vm.pastCost} SPENT</span><span>·</span><span>KEPT 90 DAYS · WRITE-ONCE</span>
        </div>
      </div>
    </div>
  );
}
