import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function PausedDetail({ vm }) {
  return (
    <div data-screen-label="Paused item" style={sx('flex:1;min-height:0;overflow-y:auto;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('max-width:720px;margin:0 auto;padding:26px 36px 90px')}>
        <Interactive as="button" onClick={vm.goBrief} style={sx("display:flex;align-items:center;gap:7px;border:none;background:none;padding:0;color:#8A8F96;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.08em;white-space:nowrap;cursor:pointer;transition:color .15s")} hoverStyle={sx('color:#08090A')}>
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M10 3.5L5.5 8l4.5 4.5"></path></svg>MORNING BRIEF
        </Interactive>
        <div style={sx('display:flex;align-items:center;gap:8px;margin-top:22px')}>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:2px 7px;border-radius:3px;white-space:nowrap;color:#B45309;background:rgba(180,83,9,0.07);border:1px solid rgba(180,83,9,0.35)")}>{vm.dChip}</span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;color:#A2A7AD;font-variant-numeric:tabular-nums")}>PAUSED AT {vm.dTime}</span>
        </div>
        <h1 style={sx('margin:12px 0 0;font-size:21px;font-weight:600;letter-spacing:-0.02em;line-height:1.35;max-width:600px')}>{vm.dTitle}</h1>
        <div style={sx('font-size:13px;color:#55595E;line-height:1.6;margin-top:8px;max-width:560px')}>Your agent chose to stop here and hand you the decision. Nothing failed, and nothing was lost — the work is staged and runs the moment you decide.</div>

        <div style={sx('margin-top:26px;background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:16px 18px')}>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#8A8F96")}>WHAT IT WANTED TO DO</div>
          <div style={sx('font-size:13px;color:#26292D;line-height:1.65;margin-top:8px')}>{vm.dAttempted}</div>
          {vm.dDraftShow && (
            <div style={sx('margin-top:12px;border:1px solid #EDEFF2;border-radius:5px;background:rgba(8,9,10,0.015);padding:11px 13px;font-size:12.5px;color:#55595E;line-height:1.65;font-style:italic')}>{vm.dDraft}</div>
          )}
        </div>

        <div style={sx('margin-top:14px;background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:16px 18px')}>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#8A8F96")}>WHY IT PAUSED</div>
          <div style={sx('display:flex;align-items:center;gap:8px;margin-top:10px')}>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:2px 7px;border-radius:3px;white-space:nowrap;color:#B45309;background:rgba(180,83,9,0.07);border:1px solid rgba(180,83,9,0.35)")}>{vm.dLimitLabel}</span>
          </div>
          <div style={sx('font-size:13px;color:#26292D;line-height:1.65;margin-top:9px')}>{vm.dLimitBody}</div>
          <div style={sx('font-size:11.5px;color:#A2A7AD;line-height:1.6;margin-top:7px')}>The same line applies to everyone with your role — the agent did nothing wrong by reaching it.</div>
        </div>

        <div style={sx('display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px')}>
          <div style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:16px 18px')}>
            <div style={sx("display:flex;align-items:center;gap:6px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#0F7B3A")}><span style={sx('width:5px;height:5px;border-radius:50%;background:#0F7B3A')}></span>IF YOU APPROVE</div>
            <div style={sx('font-size:12.5px;color:#26292D;line-height:1.65;margin-top:9px')}>{vm.dYes}</div>
          </div>
          <div style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:16px 18px')}>
            <div style={sx("display:flex;align-items:center;gap:6px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#6E7378")}><span style={sx('width:5px;height:5px;border-radius:50%;background:#9AA0AC')}></span>IF YOU SAY NO</div>
            <div style={sx('font-size:12.5px;color:#26292D;line-height:1.65;margin-top:9px')}>{vm.dNo}</div>
          </div>
        </div>

        <div style={sx('margin-top:14px;background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:16px 18px')}>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#8A8F96")}>THE TRACE — EVERY STEP, IN ORDER</div>
          <div style={sx('display:flex;flex-direction:column;margin-top:10px')}>
            {vm.dTrace.map((tr, i) => (
              <div key={i} style={sx('display:flex;align-items:baseline;gap:12px;padding:6px 0;border-bottom:1px solid #F5F6F8')}>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#A2A7AD;font-variant-numeric:tabular-nums;flex-shrink:0;width:40px")}>{tr.t}</span>
                <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${tr.dot};flex-shrink:0;position:relative;top:-1px`)}></span>
                <span style={sx(`font-size:12.5px;color: ${tr.c};line-height:1.55`)}>{tr.s}</span>
              </div>
            ))}
          </div>
        </div>

        {vm.dActionsShow && (
          <div style={sx('display:flex;align-items:center;gap:9px;margin-top:22px')}>
            <button onClick={vm.dApprove} style={sx("height:34px;padding:0 18px;border-radius:6px;border:none;background:var(--accent,#0047FF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;letter-spacing:0.07em;white-space:nowrap;cursor:pointer")}>{vm.dApproveL}</button>
            <Interactive as="button" onClick={vm.dReject} style={sx("height:34px;padding:0 16px;border-radius:6px;border:1px solid #DADDE2;background:#FFFFFF;color:#6E7378;font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;letter-spacing:0.07em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:#C7CAD1;color:#08090A')}>{vm.dRejectL}</Interactive>
            <Interactive as="button" onClick={vm.dAsk} style={sx("height:34px;padding:0 12px;border-radius:6px;border:none;background:none;color:#8A8F96;font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;letter-spacing:0.07em;white-space:nowrap;cursor:pointer;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>ASK A QUESTION FIRST</Interactive>
          </div>
        )}
        {vm.dStatusShow && (
          <div style={sx('margin-top:22px;border:1px solid #E4E6EA;border-radius:6px;padding:14px 18px;display:flex;align-items:center;gap:9px;animation:fadeSlide .3s ease-out;background:#FFFFFF')}>
            <span style={sx(`width:6px;height:6px;border-radius:50%;background: ${vm.dStatusC}`)}></span>
            <span style={sx('font-size:12.5px;color:#26292D')}>{vm.dStatusText}</span>
          </div>
        )}
      </div>
    </div>
  );
}
