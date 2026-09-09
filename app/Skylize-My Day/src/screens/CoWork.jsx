import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function CoWork({ vm }) {
  return (
    <div data-screen-label="Co-work" style={sx('flex:1;min-height:0;display:flex;gap:14px;padding:16px 24px 58px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('flex:1;min-width:0;display:flex;flex-direction:column;max-width:820px;margin:0 auto')}>
        <div style={sx('display:flex;align-items:center;gap:8px;padding:0 2px 10px')}>
          <Interactive as="button" onClick={vm.goBrief} style={sx("display:flex;align-items:center;gap:7px;height:26px;padding:0 10px;border:1px solid #DADDE2;border-radius:20px;background:#FFFFFF;color:#55595E;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:var(--accent,#0047FF);color:#08090A')}>
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"><path d="M8 1.6v1.9M8 12.5v1.9M1.6 8h1.9M12.5 8h1.9M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M12.5 3.5l-1.3 1.3M4.8 11.2l-1.3 1.3M8 5.4a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2"></path></svg>
            CONTINUES FROM THIS MORNING'S BRIEF
          </Interactive>
          <span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#A2A7AD;white-space:nowrap;flex-shrink:1;min-width:0;overflow:hidden;text-overflow:ellipsis")}>SAME AGENT · SAME RECORD</span>
        </div>
        <div ref={vm.chatRef} style={sx('flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;display:flex;flex-direction:column;gap:10px;padding:4px 2px 12px')}>
          {vm.threadVm.map((m, i) => (
            <div key={i} style={sx('display:flex;flex-direction:column;animation:fadeSlide .3s cubic-bezier(0.2,0.8,0.2,1)')}>
              {m.isUser && (
                <div style={sx('align-self:flex-end;max-width:72%;border-radius:6px;padding:10px 13px;font-size:13px;line-height:1.55;background:color-mix(in srgb, var(--accent,#0047FF) 7%, #FFFFFF);border:1px solid color-mix(in srgb, var(--accent,#0047FF) 22%, #E4E6EA);color:#08090A')}>{m.text}</div>
              )}
              {m.isAgent && (
                <div style={sx('align-self:flex-start;max-width:78%;border-radius:6px;padding:10px 13px;font-size:13px;line-height:1.55;background:#FFFFFF;border:1px solid #E4E6EA;color:#26292D')}>
                  <div>{m.text}</div>
                  {m.chipShow && (
                    <Interactive as="button" onClick={m.chipGo} style={sx("display:inline-flex;align-items:center;gap:6px;margin-top:9px;height:24px;padding:0 10px;border:1px solid #DADDE2;border-radius:5px;background:rgba(8,9,10,0.02);color:#26292D;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.05em;white-space:nowrap;cursor:pointer;transition:border-color .15s")} hoverStyle={sx('border-color:var(--accent,#0047FF)')}>
                      <span style={sx('width:5px;height:5px;border-radius:50%;background:var(--accent,#0047FF)')}></span>{m.chipLabel}
                    </Interactive>
                  )}
                </div>
              )}
              {m.isTool && (
                <div style={sx('align-self:flex-start;width:78%;border:1px solid #E4E6EA;border-radius:6px;background:rgba(8,9,10,0.015);padding:9px 12px')}>
                  <div style={sx('display:flex;align-items:center;gap:7px;flex-wrap:wrap;min-width:0')}>
                    <span style={sx('display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border:1px solid #DADDE2;border-radius:4px;background:#FFFFFF;flex-shrink:0')}><svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="#6E7378" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M9.5 3.5l3 3-6.5 6.5H3v-3z"></path></svg></span>
                    <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#6E7378;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")}>{m.toolLabel}</span>
                    <span style={sx('flex:1')}></span>
                    <span style={sx("display:flex;align-items:center;gap:5px;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.08em;color:#0F7B3A;white-space:nowrap")}><span style={sx('width:4px;height:4px;border-radius:50%;background:#0F7B3A')}></span>WITHIN YOUR AUTHORITY</span>
                    <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#A2A7AD;font-variant-numeric:tabular-nums")}>{m.time}</span>
                  </div>
                  <Interactive as="button" onClick={m.toolToggle} style={sx("display:flex;align-items:center;gap:6px;margin-top:5px;margin-left:25px;padding:0;border:none;background:none;cursor:pointer;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;color:#A2A7AD;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>
                    <span style={sx('color:#C6CAD1')}>⌞</span>{m.toolMoreLabel}
                  </Interactive>
                  <div style={sx(`font-size:12.5px;color:#26292D;line-height:1.55;margin-top:6px;padding-left:25px;display: ${m.toolBodyDisp}`)}>{m.toolBody}</div>
                </div>
              )}
              {m.isLimit && (
                <div style={sx('align-self:flex-start;width:78%;border:1px solid rgba(180,83,9,0.35);border-radius:6px;background:rgba(180,83,9,0.04);padding:11px 13px')}>
                  <div style={sx('display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0')}>
                    <span style={sx('font-family:\'Geist Mono\',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:2px 7px;border-radius:3px;white-space:nowrap;color:#B45309;background:rgba(180,83,9,0.07);border:1px solid rgba(180,83,9,0.35)')}>WAITING ON YOU</span>
                    <span style={sx('flex:1')}></span>
                    <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#A2A7AD;font-variant-numeric:tabular-nums")}>{m.time}</span>
                  </div>
                  <div style={sx('font-size:13px;font-weight:600;margin-top:8px;line-height:1.45')}>{m.limitTitle}</div>
                  <div style={sx('font-size:12.5px;color:#55595E;line-height:1.6;margin-top:4px')}>{m.limitBody}</div>
                  {m.limitActionsShow && (
                    <div style={sx('display:flex;gap:8px;margin-top:11px;flex-wrap:wrap')}>
                      <button onClick={m.limitApprove} style={sx("height:27px;padding:0 12px;border-radius:5px;border:none;background:var(--accent,#0047FF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer")}>APPROVE & SEND</button>
                      <Interactive as="button" onClick={m.limitDetail} style={sx("height:27px;padding:0 12px;border-radius:5px;border:1px solid #DADDE2;background:#FFFFFF;color:#6E7378;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:#C7CAD1;color:#08090A')}>SEE THE FULL PICTURE</Interactive>
                    </div>
                  )}
                  {m.limitResolvedShow && (
                    <div style={sx(`display:flex;align-items:center;gap:7px;margin-top:11px;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.05em;white-space:nowrap;color: ${m.limitResolvedC}`)}>
                      <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${m.limitResolvedC}`)}></span>{m.limitResolvedText}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          {vm.chatBusy && (
            <div style={sx("display:flex;align-items:center;gap:8px;padding:2px 2px;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#55595E")}>
              <span>Thinking</span><span style={sx('display:inline-block;width:7px;height:13px;background:var(--accent,#0047FF);animation:blinkCursor 1s step-end infinite')}></span>
            </div>
          )}
        </div>
        <Interactive style={sx('position:relative;border:1px solid #DADDE2;border-radius:6px;background:#FFFFFF;transition:border-color .15s')} focusStyle={sx('border-color:var(--accent,#0047FF)')}>
          <div style={sx('display:flex;align-items:flex-end;gap:8px;padding:11px 10px 8px 13px')}>
            <textarea value={vm.chatInput} onChange={vm.onChatInput} onKeyDown={vm.chatKey} rows="2" placeholder="Ask, hand something over, or think out loud…" style={sx("flex:1;border:none;background:transparent;resize:none;font-family:'Geist',system-ui,sans-serif;font-size:13px;line-height:1.5;color:#08090A;padding:0")}></textarea>
            <button onClick={vm.sendChat} aria-label="Send" style={sx(`display:flex;align-items:center;justify-content:center;width:29px;height:29px;border-radius:6px;border:none;background:var(--accent,#0047FF);color:#FFFFFF;cursor:pointer;flex-shrink:0;opacity: ${vm.sendOpacity}`)}>
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M8 13V3M4 7l4-4 4 4"></path></svg>
            </button>
          </div>
          <div style={sx('display:flex;align-items:center;justify-content:space-between;padding:5px 10px 5px 13px;border-top:1px solid #E4E6EA')}>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.09em;color:#A2A7AD")}>ENTER TO SEND</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.09em;color:#A2A7AD")}>ACTS ONLY WITH YOUR AUTHORITY · EVERY STEP SHOWS HERE</span>
          </div>
        </Interactive>
      </div>
      {/* AUTHORITY GLANCE RAIL */}
      <div style={sx('width:288px;flex-shrink:0;display:flex;flex-direction:column;background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;overflow:hidden;align-self:stretch')}>
        <div style={sx('display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid #E4E6EA')}>
          <span style={sx("display:flex;align-items:center;gap:6px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#8A8F96;white-space:nowrap")}>IN THIS SESSION, IT CAN
            <span className="skt">
              <span tabIndex={0} style={sx("display:inline-flex;align-items:center;justify-content:center;width:13px;height:13px;border:1px solid #C6CAD1;border-radius:3px;color:#8A8F96;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;cursor:help;line-height:1")}>i</span>
              <span className="sktip" style={sx("left:auto;right:-8px;transform:none;background:#FFFFFF;border:1px solid #08090A;border-radius:4px;padding:8px 10px;font-family:'Geist',system-ui,sans-serif;font-size:11.5px;font-weight:400;line-height:1.5;color:#26292D;letter-spacing:0;text-transform:none;text-align:left;white-space:normal")}>Always visible, always current. Your agent works with your authority — this is exactly what that means right now.</span>
            </span>
          </span>
        </div>
        <div style={sx('flex:1;overflow-y:auto;padding:6px 0')}>
          {vm.glanceRows.map((ga, i) => (
            <div key={i} style={sx('display:flex;align-items:flex-start;gap:9px;padding:8px 12px')}>
              <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${ga.dot};flex-shrink:0;margin-top:5px`)}></span>
              <span style={sx('font-size:12px;color:#26292D;line-height:1.5')}>{ga.t}</span>
            </div>
          ))}
          <div style={sx('padding:10px 12px 6px')}>
            <div style={sx('display:flex;align-items:baseline;justify-content:space-between')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#A2A7AD")}>SPENDING HEADROOM</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#0F7B3A;font-variant-numeric:tabular-nums")}>{vm.glanceLeft}</span>
            </div>
            <div style={sx('margin-top:6px;height:4px;border-radius:2px;background:#ECEDF0;overflow:hidden')}><div style={sx(`height:100%;border-radius:2px;background:#0F7B3A;width: ${vm.glancePct}`)}></div></div>
          </div>
        </div>
        <Interactive as="button" onClick={vm.goAuth} style={sx("display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border:none;border-top:1px solid #E4E6EA;background:transparent;cursor:pointer;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.1em;color:#6E7378;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>
          <span>EVERYTHING IT CAN DO</span><span>→</span>
        </Interactive>
      </div>
    </div>
  );
}
