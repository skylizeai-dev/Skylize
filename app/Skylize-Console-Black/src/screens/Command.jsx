import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Command({ vm }) {
  return (
    <div data-screen-label="Command" style={sx('flex:1;min-height:0;display:flex;gap:14px;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('flex:1;min-width:0;display:flex;flex-direction:column;max-width:880px;margin:0 auto')}>
        <div ref={vm.chatRef} style={sx('flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:10px;padding:4px 2px 12px')}>
          {vm.chatEmpty && (
            <div style={sx('flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center')}>
              <div style={sx('position:relative;width:64px;height:64px;margin-bottom:18px')}>
                <div style={sx('position:absolute;inset:0;border-radius:50%;background:radial-gradient(circle, color-mix(in oklab,var(--accent,#3D6BFF) 60%,transparent), transparent 70%);animation:corePulse 3s ease-in-out infinite')}></div>
                <div style={sx('position:absolute;inset:18px;border-radius:50%;background:var(--accent,#3D6BFF);box-shadow:0 0 30px color-mix(in oklab,var(--accent,#3D6BFF) 70%,transparent)')}></div>
              </div>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.18em;color:#77809A")}>COMMAND CHANNEL</div>
              <div style={sx('font-size:20px;font-weight:600;letter-spacing:-0.015em;margin-top:10px')}>Command your organization.</div>
              <div style={sx('font-size:12.5px;color:#8B93A7;margin-top:6px;max-width:420px;line-height:1.6')}>One order in — routed, delegated, and executed by 151 governed agents. Every action signed and auditable.</div>
              <div style={sx('display:flex;gap:8px;margin-top:20px;flex-wrap:wrap;justify-content:center')}>
                {vm.suggestions.map((sg, i) => (
                  <Interactive key={i} as="button" onClick={sg.send} style={sx("height:29px;padding:0 13px;border:1px solid #232939;border-radius:6px;background:rgba(255,255,255,0.02);color:#C7CBD6;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;cursor:pointer;transition:border-color .15s,color .15s,box-shadow .15s")} hoverStyle={sx('border-color:var(--accent,#3D6BFF);color:#E9EBF2;box-shadow:0 0 16px color-mix(in oklab,var(--accent,#3D6BFF) 20%,transparent)')}>{sg.text}</Interactive>
                ))}
              </div>
            </div>
          )}
          {vm.messagesVm.map((m, i) => (
            <div key={i} style={sx(`display:flex;flex-direction:column;align-items: ${m.align}`)}>
              <div style={sx(`max-width:78%;border-radius:9px;padding:10px 13px;font-size:13px;line-height:1.55;background: ${m.bg};color: ${m.color};border:1px solid ${m.border}`)}>
                {m.lines.map((ln, li) => (
                  <React.Fragment key={li}>
                    {ln.isBold && <div style={sx('font-weight:600;margin-bottom:3px')}>{ln.text}</div>}
                    {ln.isItem && <div style={sx('display:flex;gap:7px;margin-top:3px')}><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#77809A;flex-shrink:0;position:relative;top:1px")}>{ln.marker}</span><span>{ln.text}</span></div>}
                    {ln.isPlain && <div>{ln.text}</div>}
                  </React.Fragment>
                ))}
                {m.chipsShow && (
                  <div style={sx('display:flex;flex-wrap:wrap;gap:5px;margin-top:8px')}>
                    {m.chips.map((ch, ci) => (
                      <span key={ci} style={sx("display:flex;align-items:center;gap:5px;height:19px;padding:0 7px;border-radius:5px;background:rgba(255,255,255,0.07);font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.05em;color:#C7CBD6")}>
                        <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${ch.color};display: ${ch.dotDisp};flex-shrink:0`)}></span>
                        <span>{ch.label}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {m.govShow && (
                <div style={sx("display:flex;align-items:center;gap:7px;margin-top:5px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;color:#77809A")}>
                  <span style={sx('width:5px;height:5px;border-radius:50%;background:#34C579;box-shadow:0 0 8px rgba(52,197,121,0.8)')}></span>
                  <span style={sx('color:#34C579')}>GOVERNED</span><span>·</span><span>{m.govToken}</span><span>·</span><span style={sx('font-variant-numeric:tabular-nums')}>{m.govAgents} AGENTS</span><span>·</span><span style={sx('font-variant-numeric:tabular-nums')}>{m.govTokens} TOK</span>
                </div>
              )}
            </div>
          ))}
          {vm.chatBusy && (
            <div style={sx("display:flex;align-items:center;gap:8px;padding:2px 2px;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#9AA1B2")}>
              <span>{vm.chatPhase}</span><span style={sx('display:inline-block;width:7px;height:13px;background:var(--accent,#3D6BFF);animation:blinkCursor 1s step-end infinite')}></span>
            </div>
          )}
        </div>
        <div style={sx('position:relative;border:1px solid #262D40;border-radius:9px;background:rgba(12,15,22,0.85);backdrop-filter:blur(10px);transition:border-color .15s,box-shadow .15s')}>
          {vm.composerChipsShow && (
            <div style={sx('display:flex;flex-wrap:wrap;gap:6px;padding:10px 13px 0')}>
              {vm.composerChips.map((cc, i) => (
                <span key={i} style={sx("display:flex;align-items:center;gap:6px;height:24px;padding:0 6px 0 9px;border-radius:6px;border:1px solid #262D40;background:rgba(255,255,255,0.03);font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.03em;color: " + cc.c)}>
                  <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${cc.dot};flex-shrink:0;display: ${cc.dotDisp}`)}></span>
                  <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={sx(`display: ${cc.fileDisp};flex-shrink:0`)}><path d="M11 2.5H6a2 2 0 00-2 2v9a2 2 0 002 2h5a2 2 0 002-2V6.5z"></path><path d="M10.5 2.5V6h3.5"></path></svg>
                  <span style={sx('white-space:nowrap;max-width:180px;overflow:hidden;text-overflow:ellipsis')}>{cc.label}</span>
                  <Interactive as="button" onClick={cc.remove} aria-label="Remove" style={sx('display:flex;align-items:center;justify-content:center;width:13px;height:13px;border:none;background:none;color:inherit;opacity:0.55;cursor:pointer;padding:0;flex-shrink:0')} hoverStyle={sx('opacity:1')}><svg width="8" height="8" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 4l8 8M12 4l-8 8"></path></svg></Interactive>
                </span>
              ))}
            </div>
          )}
          <div style={sx('display:flex;align-items:flex-end;gap:8px;padding:11px 10px 8px 13px')}>
            <textarea
              value={vm.chatInput}
              onChange={vm.onChatInput}
              onKeyDown={vm.chatKey}
              rows={2}
              placeholder="Issue a directive — routed via the Orchestrator…"
              style={sx("flex:1;border:none;background:transparent;resize:none;font-family:'Geist',system-ui,sans-serif;font-size:13px;line-height:1.5;color:#E9EBF2;padding:0")}
            ></textarea>
            <Interactive
              as="button"
              onClick={vm.sendChat}
              aria-label="Send"
              style={sx(`display:flex;align-items:center;justify-content:center;width:29px;height:29px;border-radius:6px;border:none;background:var(--accent,#3D6BFF);color:#FFFFFF;cursor:pointer;flex-shrink:0;box-shadow:0 0 16px color-mix(in oklab,var(--accent,#3D6BFF) 40%,transparent);transition:filter .15s;opacity: ${vm.sendOpacity}`)}
              hoverStyle={sx('filter:brightness(1.15)')}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M8 13V3M4 7l4-4 4 4"></path></svg>
            </Interactive>
          </div>
          <div style={sx('display:flex;align-items:center;justify-content:space-between;padding:5px 8px 5px 6px;border-top:1px solid #161A26')}>
            <div style={sx('display:flex;align-items:center;gap:2px')}>
              <input ref={vm.fileInputRef} type="file" multiple onChange={vm.onFileChange} style={{ display: 'none' }} />
              <Interactive as="button" onClick={vm.onAttachClick} aria-label="Attach files" title="Attach files" style={sx('display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:6px;border:1px solid transparent;background:none;color:#8B93A7;cursor:pointer;transition:border-color .15s,color .15s')} hoverStyle={sx('border-color:#232939;color:#E9EBF2')}>
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"><path d="M11.5 5.5l-5 5a2 2 0 102.8 2.8l5-5a3.6 3.6 0 00-5-5.1l-5.2 5.2a5 5 0 007 7l4.7-4.6"></path></svg>
              </Interactive>
              <Interactive as="button" onClick={vm.toggleTagMenu} aria-label="Tag a department or agent" title="Tag a department or agent" style={sx(`display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:6px;border:1px solid ${vm.tagBtnBd};background: ${vm.tagBtnBg};color: ${vm.tagBtnC};cursor:pointer;transition:border-color .15s,color .15s`)} hoverStyle={sx('border-color:#232939;color:#E9EBF2')}>
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"><path d="M6.2 2.5L5 13.5M11 2.5l-1.2 11M2.5 6h11M2 10.5h11"></path></svg>
              </Interactive>
              <Interactive as="button" onClick={vm.toggleConnMenu} aria-label="Attach connector context" title="Attach connector context" style={sx(`display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:6px;border:1px solid ${vm.connBtnBd};background: ${vm.connBtnBg};color: ${vm.connBtnC};cursor:pointer;transition:border-color .15s,color .15s`)} hoverStyle={sx('border-color:#232939;color:#E9EBF2')}>
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5.2 2.5v3M10.8 2.5v3M4 5.5h8v2.8a4 4 0 01-8 0zM8 12.3V14"></path></svg>
              </Interactive>
            </div>
            <div style={sx("display:flex;align-items:center;gap:10px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.09em;color:#616A82")}>
              <span>ENTER TO SEND</span>
              <span style={sx('font-variant-numeric:tabular-nums')}>SESSION {vm.chatTokFmt} TOK · {vm.chatCostFmt}</span>
            </div>
          </div>
          {vm.tagMenuOpen && (
            <div style={sx('position:absolute;left:6px;bottom:calc(100% + 8px);width:264px;background:rgba(14,17,25,0.96);backdrop-filter:blur(18px);border:1px solid #232939;border-radius:8px;box-shadow:0 18px 50px rgba(0,0,0,0.55);animation:fadeSlide .16s ease-out;overflow:hidden;z-index:55')}>
              <div style={sx('display:flex;align-items:center;gap:7px;padding:9px 11px;border-bottom:1px solid #1B2130')}>
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="#616A82" strokeWidth="1.5" strokeLinecap="round"><circle cx="7" cy="7" r="4"></circle><path d="M10 10l3.5 3.5"></path></svg>
                <input value={vm.tagQ} onChange={vm.onTagQ} placeholder="Tag a department or exec…" style={sx("flex:1;border:none;background:transparent;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#E9EBF2;min-width:0")} />
              </div>
              <div style={sx('max-height:230px;overflow-y:auto')}>
                {vm.tagOptions.map((tg, i) => (
                  <Interactive key={i} as="button" onClick={tg.pick} style={sx('display:flex;align-items:center;gap:8px;width:100%;padding:7px 11px;border:none;background:none;cursor:pointer;text-align:left;font-family:inherit;transition:background-color .12s')} hoverStyle={sx('background-color:rgba(255,255,255,0.05)')}>
                    <span style={sx(`width:6px;height:6px;border-radius:2px;background: ${tg.color};flex-shrink:0`)}></span>
                    <span style={sx('flex:1;font-size:12px;color:#E9EBF2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{tg.label}</span>
                    <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.06em;color:#77809A")}>{tg.kind}</span>
                  </Interactive>
                ))}
              </div>
            </div>
          )}
          {vm.connMenuOpen && (
            <div style={sx('position:absolute;left:38px;bottom:calc(100% + 8px);width:232px;background:rgba(14,17,25,0.96);backdrop-filter:blur(18px);border:1px solid #232939;border-radius:8px;box-shadow:0 18px 50px rgba(0,0,0,0.55);animation:fadeSlide .16s ease-out;overflow:hidden;z-index:55')}>
              <div style={sx("padding:9px 11px 7px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#77809A;border-bottom:1px solid #1B2130")}>CONNECTED SOURCES</div>
              <div style={sx('max-height:230px;overflow-y:auto')}>
                {vm.connOptions.map((co, i) => (
                  <Interactive key={i} as="button" onClick={co.pick} style={sx('display:flex;align-items:center;gap:9px;width:100%;padding:7px 11px;border:none;background:none;cursor:pointer;text-align:left;font-family:inherit;transition:background-color .12s')} hoverStyle={sx('background-color:rgba(255,255,255,0.05)')}>
                    <span style={sx(`display:flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:5px;border:1px solid #232939;background:rgba(255,255,255,0.03);font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;font-weight:600;color: ${co.color};flex-shrink:0`)}>{co.mono}</span>
                    <span style={sx('flex:1;font-size:12px;color:#E9EBF2')}>{co.name}</span>
                    <span style={sx('width:5px;height:5px;border-radius:50%;background:#34C579;box-shadow:0 0 6px rgba(52,197,121,0.7);flex-shrink:0')}></span>
                  </Interactive>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
      <div style={sx('width:292px;flex-shrink:0;display:flex;flex-direction:column;background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
        <div style={sx('display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid #161A26')}>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>DELEGATION CHAIN</span>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;font-variant-numeric:tabular-nums")}>{vm.railCount}</span>
        </div>
        <div style={sx('flex:1;overflow-y:auto')}>
          {vm.railEmpty && (
            <div style={sx("padding:22px 14px;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;line-height:1.7;color:#616A82;letter-spacing:0.04em")}>No delegation in flight.<br />Issue a directive to watch the authority chain execute here.</div>
          )}
          {vm.railAgents.map((ra, i) => (
            <div key={i} style={sx(`display:flex;align-items:center;gap:8px;padding:7px 12px;border-bottom:1px solid #12151F;padding-left: ${ra.pad}`)}>
              <span style={sx(`width:6px;height:6px;border-radius:50%;flex-shrink:0;background: ${ra.dot};box-shadow:0 0 8px ${ra.dot};animation: ${ra.anim}`)}></span>
              <div style={sx('flex:1;min-width:0')}>
                <div style={sx('font-size:12px;font-weight:500;color:#E9EBF2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{ra.name}</div>
                <div style={sx('display:flex;align-items:center;gap:5px;margin-top:1px')}><span style={sx(`width:5px;height:5px;border-radius:2px;background: ${ra.deptColor}`)}></span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.08em;color:#77809A")}>{ra.statusWord}</span></div>
              </div>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2;font-variant-numeric:tabular-nums")}>{ra.tok}</span>
            </div>
          ))}
        </div>
        <div style={sx("display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-top:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;color:#77809A")}>
          <span>TOTAL</span>
          <span style={sx('font-variant-numeric:tabular-nums;color:#E9EBF2')}>{vm.chatTokFmt} TOK · {vm.chatCostFmt}</span>
        </div>
      </div>
    </div>
  );
}
