import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Workflows({ vm }) {
  return (
    <div data-screen-label="Workflows" style={sx('flex:1;min-height:0;display:flex;gap:14px;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('width:238px;flex-shrink:0;background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow-y:auto')}>
        <div style={sx("padding:10px 12px 7px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;border-bottom:1px solid #161A26")}>WORKFLOW LIBRARY</div>
        {vm.wfList.map((w, i) => (
          <Interactive
            key={i}
            as="button"
            onClick={w.pick}
            style={sx(`display:flex;flex-direction:column;gap:3px;width:100%;padding:10px 12px;border:none;border-left:2px solid ${w.edge};background: ${w.bg};cursor:pointer;text-align:left;font-family:inherit;transition:background-color .12s`)}
            hoverStyle={sx('background-color:rgba(255,255,255,0.03)')}
          >
            <span style={sx(`font-size:12.5px;font-weight:500;color: ${w.c}`)}>{w.name}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#77809A")}>{w.meta}</span>
          </Interactive>
        ))}
      </div>
      <div style={sx('flex:1;min-width:0;display:flex;flex-direction:column;gap:12px')}>
        <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:16px 18px')}>
          <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:4px')}>
            <div style={sx('font-size:14px;font-weight:600;letter-spacing:-0.01em')}>{vm.wfSel.name}</div>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.1em;color:#77809A")}>{vm.wfSel.meta}</span>
          </div>
          <div style={sx('font-size:12px;color:#8B93A7;margin-bottom:18px')}>{vm.wfSel.desc}</div>
          <div style={sx('display:flex;align-items:stretch;gap:0;overflow-x:auto;padding:6px 2px 10px;perspective:900px')}>
            {vm.wfStages.map((st, i) => (
              <div key={i} style={sx('display:flex;align-items:center;flex-shrink:0')}>
                <Interactive
                  onMouseMove={vm.tiltMove}
                  onMouseLeave={vm.tiltLeave}
                  style={sx(`width:158px;border:1px solid ${st.bd};border-radius:9px;background:linear-gradient(180deg,#101420,#0C0F16);padding:11px 12px;transition:box-shadow .2s;will-change:transform`)}
                  hoverStyle={sx('box-shadow:0 12px 28px rgba(0,0,0,0.45)')}
                >
                  <div style={sx('display:flex;align-items:center;justify-content:space-between;margin-bottom:7px')}>
                    <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.12em;color:#77809A")}>STAGE {st.n}</span>
                    <span style={sx(`width:6px;height:6px;border-radius:50%;background: ${st.dot};box-shadow:0 0 8px ${st.dot};animation: ${st.anim}`)}></span>
                  </div>
                  <div style={sx('font-size:12.5px;font-weight:500;color:#E9EBF2;margin-bottom:5px')}>{st.name}</div>
                  <div style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color: ${st.stColor};letter-spacing:0.06em`)}>{st.status}</div>
                  <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;color:#616A82;margin-top:4px")}>{st.owner}</div>
                </Interactive>
                <svg width="30" height="12" viewBox="0 0 30 12" style={sx(`flex-shrink:0;display: ${st.arrowDisp}`)}><path d="M2 6h22M20 2l5 4-5 4" fill="none" stroke="#2A3040" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"></path></svg>
              </div>
            ))}
          </div>
        </div>
        <div style={sx('flex:1;min-height:0;background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden;display:flex;flex-direction:column')}>
          <div style={sx("padding:10px 14px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;flex-shrink:0")}>RECENT RUNS</div>
          <div style={sx('flex:1;overflow-y:auto')}>
            {vm.wfRuns.map((run, i) => (
              <Interactive
                key={i}
                style={sx('display:grid;grid-template-columns:minmax(160px,2fr) minmax(110px,1.3fr) 90px 70px 64px;gap:0 12px;align-items:center;padding:8px 14px;border-bottom:1px solid #12151F;transition:background-color .12s')}
                hoverStyle={sx('background-color:rgba(255,255,255,0.02)')}
              >
                <span style={sx('font-size:12.5px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{run.name}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{run.trigger}</span>
                <span style={sx('display:flex;align-items:center;gap:5px')}><span style={sx(`width:5px;height:5px;border-radius:50%;background: ${run.stColor};box-shadow:0 0 6px ${run.stColor}`)}></span><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color: ${run.stColor}`)}>{run.status}</span></span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;font-variant-numeric:tabular-nums")}>{run.when}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#9AA1B2;text-align:right;font-variant-numeric:tabular-nums")}>{run.dur}</span>
              </Interactive>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
