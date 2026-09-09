import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function MorningBrief({ vm }) {
  return (
    <div data-screen-label="Morning Brief" style={sx('flex:1;min-height:0;display:flex;overflow:hidden;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('flex:1;min-width:0;overflow-y:auto')}>
        <div style={sx('max-width:720px;margin:0 auto;padding:34px 36px 90px')}>

          {vm.isLoading && (
            <div style={sx("display:flex;align-items:center;gap:8px;margin-bottom:26px;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#55595E")}>
              <span>Writing your brief — pulling the night's record</span><span style={sx('display:inline-block;width:7px;height:13px;background:var(--accent,#0047FF);animation:blinkCursor 1s step-end infinite')}></span>
            </div>
          )}

          {vm.introShow && (
            <div style={sx('border:1px solid #DADDE2;border-radius:6px;background:color-mix(in srgb, var(--accent,#0047FF) 4%, #FFFFFF);padding:22px 24px;margin-bottom:30px')}>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.18em;color:var(--accent,#0047FF)")}>DAY ONE</div>
              <div style={sx('font-size:19px;font-weight:600;letter-spacing:-0.015em;margin-top:9px')}>This page is your Morning Brief.</div>
              <div style={sx('font-size:12.5px;color:#55595E;line-height:1.65;margin-top:7px;max-width:520px')}>It will look exactly like this every morning: what needs you first, then what got done, then your own trail, then cost. Same shape every day — empty sections stay put, so your eyes learn where to look. Your agent starts tonight.</div>
              <div style={sx('display:flex;gap:8px;margin-top:16px')}>
                <button onClick={vm.goChat} style={sx("height:30px;padding:0 14px;border-radius:6px;border:none;background:var(--accent,#0047FF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.08em;white-space:nowrap;cursor:pointer")}>SAY HELLO IN CO-WORK</button>
                <Interactive as="button" onClick={vm.goAuth} style={sx("height:30px;padding:0 14px;border-radius:6px;border:1px solid #DADDE2;background:transparent;color:#55595E;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.08em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:var(--accent,#0047FF);color:#08090A')}>SEE WHAT IT CAN DO</Interactive>
              </div>
            </div>
          )}

          {vm.absenceShow && (
            <div style={sx('border:1px solid #DADDE2;border-radius:6px;background:#FFFFFF;padding:14px 18px;margin-bottom:26px;display:flex;align-items:center;gap:12px')}>
              <span style={sx('width:6px;height:6px;border-radius:50%;background:#0F7B3A;flex-shrink:0')}></span>
              <div style={sx('flex:1;min-width:0')}>
                <span style={sx('font-size:13px;font-weight:600')}>Welcome back — you were away 9 days.</span>
                <span style={sx('font-size:12.5px;color:#55595E')}> Your agent grouped everything by day; open any day for the full record.</span>
              </div>
            </div>
          )}

          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.18em;color:#8A8F96")}>{vm.dateLine}</div>
          <h1 style={sx('margin:10px 0 0;font-size:24px;font-weight:600;letter-spacing:-0.02em;line-height:1.25')}>{vm.greeting}</h1>
          {vm.summaryShow && (
            <div style={sx('margin-top:10px;max-width:560px')}>
              <div style={sx('font-size:13.5px;color:#55595E;line-height:1.65')}>{vm.summaryText}</div>
              <div style={sx("display:flex;align-items:center;gap:6px;margin-top:7px;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#A2A7AD")}>
                <span style={sx('font-size:10px;position:relative;top:-0.5px')}>✳</span><span>WRITTEN BY YOUR AGENT</span>
                <span className="skt">
                  <span tabIndex={0} style={sx("display:inline-flex;align-items:center;justify-content:center;width:13px;height:13px;border:1px solid #C6CAD1;border-radius:3px;color:#8A8F96;font-size:8.5px;cursor:help;line-height:1")}>i</span>
                  <span className="sktip" style={sx("left:0;transform:none;background:#FFFFFF;border:1px solid #08090A;border-radius:4px;padding:8px 10px;font-family:'Geist',system-ui,sans-serif;font-size:11.5px;font-weight:400;line-height:1.5;color:#26292D;letter-spacing:0;text-transform:none;white-space:normal")}>Your agent wrote this summary in its own words. Everything underneath is the exact record — items, times and amounts are not generated.</span>
                </span>
              </div>
            </div>
          )}

          {/* SECTION 1 · NEEDS YOUR DECISION */}
          <div style={sx('display:flex;align-items:center;gap:10px;margin:38px 0 12px')}>
            {vm.annotate && <span style={sx('display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--accent,#0047FF);color:#FFFFFF;font-family:\'Geist Mono\',ui-monospace,monospace;font-size:9px;flex-shrink:0')}>2</span>}
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:#8A8F96;white-space:nowrap")}>NEEDS YOUR DECISION</span>
            <span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
            <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.1em;color: ${vm.decCountC};font-variant-numeric:tabular-nums`)}>{vm.decCountLabel}</span>
          </div>
          {vm.isLoading && (
            <div style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:16px;display:flex;flex-direction:column;gap:10px')}>
              <div style={sx('height:12px;width:62%;border-radius:4px;background:linear-gradient(90deg,#EFF1F4 25%,#E6E8EC 37%,#EFF1F4 63%);background-size:420px 100%;animation:shimmer 1.4s linear infinite')}></div>
              <div style={sx('height:12px;width:40%;border-radius:4px;background:linear-gradient(90deg,#EFF1F4 25%,#E6E8EC 37%,#EFF1F4 63%);background-size:420px 100%;animation:shimmer 1.4s linear infinite')}></div>
            </div>
          )}
          {vm.decEmptyShow && (
            <div style={sx('border:1px dashed #DADDE2;border-radius:6px;padding:16px 18px;display:flex;align-items:center;gap:10px')}>
              <span style={sx('width:6px;height:6px;border-radius:50%;background:#0F7B3A;flex-shrink:0')}></span>
              <div><span style={sx('font-size:12.5px;font-weight:500;color:#26292D')}>{vm.decEmptyTitle}</span><span style={sx('font-size:12.5px;color:#8A8F96')}> {vm.decEmptyBody}</span></div>
            </div>
          )}
          <div style={sx('display:flex;flex-direction:column;gap:10px')}>
            {vm.decItems.map((d) => (
              <Interactive key={d.key} style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:15px 16px 13px;transition:border-color .15s')} hoverStyle={sx('border-color:#C7CAD1')}>
                <div style={sx('display:flex;align-items:center;gap:8px')}>
                  {vm.annotate && <span style={sx('display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--accent,#0047FF);color:#FFFFFF;font-family:\'Geist Mono\',ui-monospace,monospace;font-size:9px;flex-shrink:0')}>8</span>}
                  <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:2px 7px;border-radius:3px;white-space:nowrap;color: ${d.chipC};background: ${d.chipBg};border:1px solid ${d.chipBd}`)}>{d.chipText}</span>
                  <span style={sx('flex:1')}></span>
                  <Interactive as="button" onClick={d.open} style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;color:#A2A7AD;background:none;border:none;cursor:pointer;padding:0;transition:color .15s")} hoverStyle={sx('color:#08090A')}>FULL TRACE →</Interactive>
                </div>
                <Interactive as="button" onClick={d.open} style={sx("display:block;margin-top:10px;font-size:14px;font-weight:600;letter-spacing:-0.01em;color:#08090A;line-height:1.45;background:none;border:none;padding:0;cursor:pointer;text-align:left;font-family:inherit")} hoverStyle={sx('color:var(--accent,#0047FF)')}>{d.title}</Interactive>
                <div style={sx('font-size:12.5px;color:#55595E;line-height:1.6;margin-top:5px;max-width:540px')}>{d.why}</div>
                {d.actionsShow && (
                  <div>
                    <div style={sx('display:flex;align-items:center;gap:9px;margin-top:12px;padding-top:11px;border-top:1px solid #F0F2F4;flex-wrap:wrap')}>
                      <span style={sx('display:flex;align-items:center;gap:6px;flex-shrink:0')}>
                        <span style={sx('display:flex;gap:2px;flex-shrink:0')}>
                          <span style={sx(`width:10px;height:3px;border-radius:1px;background: ${d.g1}`)}></span>
                          <span style={sx(`width:10px;height:3px;border-radius:1px;background: ${d.g2}`)}></span>
                          <span style={sx(`width:10px;height:3px;border-radius:1px;background: ${d.g3}`)}></span>
                        </span>
                        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;color:#55595E;white-space:nowrap")}>{d.readyLabel}</span>
                      </span>
                      <span style={sx('width:1px;height:11px;background:#E4E6EA;flex-shrink:0')}></span>
                      <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;color:#A2A7AD;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")}>CHECKED {d.checked}</span>
                    </div>
                    <Interactive as="button" onClick={d.toggleWhy} style={sx("display:flex;align-items:center;gap:6px;margin-top:10px;padding:0;border:none;background:none;cursor:pointer;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#8A8F96;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>
                      {d.whyLabel}
                      <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" style={{ transition: 'transform .2s', transform: d.whyChev }}><path d="M4 6l4 4 4-4"></path></svg>
                    </Interactive>
                    <div style={sx(`flex-direction:column;gap:7px;margin-top:9px;padding:12px 13px;border-radius:5px;background:rgba(8,9,10,0.025);max-width:540px;display: ${d.whyDisp}`)}>
                      <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.11em;color:#B45309")}>{d.limitLabel}</div>
                      <div style={sx('font-size:12px;color:#26292D;line-height:1.6')}>{d.limitBody}</div>
                      <div style={sx('font-size:12px;color:#8A8F96;line-height:1.6')}>{d.ifIgnored}</div>
                    </div>
                    <div style={sx('display:flex;align-items:center;gap:8px;margin-top:13px;flex-wrap:wrap')}>
                      <button onClick={d.approve} style={sx("height:28px;padding:0 13px;border-radius:5px;border:none;background:var(--accent,#0047FF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer")}>{d.approveL}</button>
                      <Interactive as="button" onClick={d.reject} style={sx("height:28px;padding:0 13px;border-radius:5px;border:1px solid #DADDE2;background:transparent;color:#6E7378;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:#C7CAD1;color:#08090A')}>{d.rejectL}</Interactive>
                      <Interactive as="button" onClick={d.ask} style={sx("height:28px;padding:0 10px;border-radius:5px;border:none;background:none;color:#8A8F96;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>ASK BEFORE DECIDING</Interactive>
                    </div>
                  </div>
                )}
                {d.resolvedShow && (
                  <div style={sx(`display:flex;align-items:center;gap:7px;margin-top:13px;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.05em;white-space:nowrap;color: ${d.resolvedC}`)}>
                    <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${d.resolvedC}`)}></span>{d.resolvedText}
                  </div>
                )}
              </Interactive>
            ))}
          </div>
          {vm.decMoreShow && (
            <div style={sx('margin-top:10px')}>
              <Interactive as="button" onClick={vm.decMoreClick} style={sx("display:flex;align-items:center;gap:8px;width:100%;padding:11px 14px;border:1px dashed #DADDE2;border-radius:6px;background:transparent;cursor:pointer;text-align:left;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.09em;color:#6E7378;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:#C7CAD1;color:#08090A')}>{vm.decMoreLabel}</Interactive>
              <div style={sx('margin-top:7px;font-size:11.5px;color:#A2A7AD;line-height:1.55;max-width:520px')}>Three at a time, so a full desk still reads in ten seconds. The section itself never moves — only how many cards it holds.</div>
            </div>
          )}
          {vm.blockedBandShow && (
            <div style={sx('margin-top:14px;border:1px solid rgba(180,83,9,0.35);border-radius:6px;background:rgba(180,83,9,0.04);padding:14px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap')}>
              <div style={sx('flex:1;min-width:260px')}>
                <div style={sx('font-size:13px;font-weight:600')}>Five pauses in one night is unusual.</div>
                <div style={sx('font-size:12px;color:#55595E;line-height:1.55;margin-top:3px')}>Nothing failed — each piece is staged and runs the moment you decide. But your headroom may be set tighter than your actual job.</div>
              </div>
              <div style={sx('display:flex;gap:8px')}>
                <button onClick={vm.goAuth} style={sx("height:28px;padding:0 12px;border-radius:5px;border:none;background:var(--accent,#0047FF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer")}>SEE YOUR AUTHORITY</button>
                <Interactive as="button" onClick={vm.askOwner} style={sx("height:28px;padding:0 12px;border-radius:5px;border:1px solid #DADDE2;background:#FFFFFF;color:#6E7378;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:var(--accent,#0047FF);color:#08090A')}>ASK FOR A CHANGE</Interactive>
              </div>
            </div>
          )}

          {/* SECTION 2 · DONE WHILE YOU WERE AWAY */}
          <div style={sx('display:flex;align-items:center;gap:10px;margin:34px 0 12px')}>
            {vm.annotate && <span style={sx('display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--accent,#0047FF);color:#FFFFFF;font-family:\'Geist Mono\',ui-monospace,monospace;font-size:9px;flex-shrink:0')}>3</span>}
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:#8A8F96;white-space:nowrap")}>DONE WHILE YOU WERE AWAY</span>
            <span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.1em;color:#8A8F96;font-variant-numeric:tabular-nums")}>{vm.doneCountLabel}</span>
          </div>
          {vm.isLoading && (
            <div style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:16px;display:flex;flex-direction:column;gap:12px')}>
              <div style={sx('height:12px;width:78%;border-radius:4px;background:linear-gradient(90deg,#EFF1F4 25%,#E6E8EC 37%,#EFF1F4 63%);background-size:420px 100%;animation:shimmer 1.4s linear infinite')}></div>
              <div style={sx('height:12px;width:64%;border-radius:4px;background:linear-gradient(90deg,#EFF1F4 25%,#E6E8EC 37%,#EFF1F4 63%);background-size:420px 100%;animation:shimmer 1.4s linear infinite')}></div>
              <div style={sx('height:12px;width:71%;border-radius:4px;background:linear-gradient(90deg,#EFF1F4 25%,#E6E8EC 37%,#EFF1F4 63%);background-size:420px 100%;animation:shimmer 1.4s linear infinite')}></div>
              <div style={sx('height:12px;width:52%;border-radius:4px;background:linear-gradient(90deg,#EFF1F4 25%,#E6E8EC 37%,#EFF1F4 63%);background-size:420px 100%;animation:shimmer 1.4s linear infinite')}></div>
            </div>
          )}
          {vm.doneEmptyShow && (
            <div style={sx('border:1px dashed #DADDE2;border-radius:6px;padding:18px;max-width:640px')}>
              <div style={sx('font-size:13px;font-weight:600;color:#26292D')}>{vm.doneEmptyTitle}</div>
              <div style={sx('font-size:12.5px;color:#8A8F96;line-height:1.6;margin-top:4px')}>{vm.doneEmptyBody}</div>
            </div>
          )}
          {vm.doneFlatShow && (
            <div style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;overflow:hidden')}>
              {vm.doneRows.map((r, i) => (
                <div key={i} style={sx('display:flex;align-items:center;gap:11px;padding:11px 16px;border-bottom:1px solid #F0F2F4')}>
                  <span style={sx(`width:6px;height:6px;border-radius:50%;background:var(--accent,#0047FF);flex-shrink:0;transition:opacity .5s ease;opacity: ${r.dotOp}`)}></span>
                  <span style={sx('flex:1;font-size:13px;font-weight:500;color:#08090A;line-height:1.5;min-width:0')}>{r.h}</span>
                  <Interactive as="button" onClick={r.open} style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.08em;color:#C6CAD1;background:none;border:none;cursor:pointer;padding:0;flex-shrink:0;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>TRACE →</Interactive>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#8A8F96;font-variant-numeric:tabular-nums;flex-shrink:0;width:40px;text-align:right")}>{r.t}</span>
                </div>
              ))}
            </div>
          )}
          {vm.doneGroupedShow && (
            <div style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;overflow:hidden')}>
              {vm.doneGroups.map((gp, i) => (
                <div key={i} style={sx('border-bottom:1px solid #F0F2F4')}>
                  <Interactive as="button" onClick={gp.toggle} style={sx('display:flex;align-items:center;gap:11px;width:100%;padding:11px 16px;border:none;background:none;cursor:pointer;text-align:left;font-family:inherit;transition:background-color .12s')} hoverStyle={sx('background-color:rgba(8,9,10,0.02)')}>
                    <span style={sx(`width:6px;height:6px;border-radius:50%;background:var(--accent,#0047FF);flex-shrink:0;transition:opacity .5s ease;opacity: ${gp.dotOp}`)}></span>
                    <span style={sx('font-size:13px;font-weight:600;color:#08090A;width:92px;flex-shrink:0')}>{gp.label}</span>
                    <span style={sx('flex:1;font-size:12.5px;color:#55595E')}>{gp.meta}</span>
                    <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#8A8F96;font-variant-numeric:tabular-nums")}>{gp.cost}</span>
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="#A2A7AD" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ transition: 'transform .2s', transform: gp.chev }}><path d="M4 6l4 4 4-4"></path></svg>
                  </Interactive>
                  <div style={sx(`display: ${gp.rowsDisp};flex-direction:column;padding:0 16px 6px 33px`)}>
                    {gp.rows.map((gr, gi) => (
                      <div key={gi} style={sx('display:flex;align-items:center;gap:10px;padding:7px 0;border-top:1px solid #F5F6F8')}>
                        <span style={sx('flex:1;font-size:12.5px;color:#26292D;line-height:1.5')}>{gr.h}</span>
                        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#A2A7AD;font-variant-numeric:tabular-nums")}>{gr.t}</span>
                      </div>
                    ))}
                    {gp.moreShow && (
                      <div style={sx("padding:7px 0 6px;border-top:1px solid #F5F6F8;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color:#A2A7AD")}>{gp.moreLabel}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* SECTION 3 · YOUR OWN ACTIONS */}
          <div style={sx('display:flex;align-items:center;gap:10px;margin:34px 0 10px')}>
            {vm.annotate && <span style={sx('display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--accent,#0047FF);color:#FFFFFF;font-family:\'Geist Mono\',ui-monospace,monospace;font-size:9px;flex-shrink:0')}>4</span>}
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:#A2A7AD;white-space:nowrap")}>YOUR OWN ACTIONS</span>
            <span style={sx('flex:1;height:1px;background:#EDEFF2')}></span>
            <Interactive as="button" onClick={vm.ownToggle} style={sx("display:flex;align-items:center;gap:5px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.1em;color:#A2A7AD;background:none;border:none;cursor:pointer;padding:0;transition:color .15s")} hoverStyle={sx('color:#08090A')}>
              {vm.ownCountLabel}
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ transition: 'transform .2s', transform: vm.ownChev }}><path d="M4 6l4 4 4-4"></path></svg>
            </Interactive>
          </div>
          {vm.isLoading && (
            <div style={sx('height:11px;width:44%;border-radius:4px;background:linear-gradient(90deg,#EFF1F4 25%,#E6E8EC 37%,#EFF1F4 63%);background-size:420px 100%;animation:shimmer 1.4s linear infinite')}></div>
          )}
          {vm.ownEmptyShow && (
            <div style={sx('font-size:12px;color:#A2A7AD;padding:2px 0 0')}>{vm.ownEmptyText}</div>
          )}
          {vm.ownRowsShow && (
            <div style={sx('display:flex;flex-direction:column')}>
              {vm.ownRows.map((o, i) => (
                <div key={i} style={sx('display:flex;align-items:center;gap:10px;padding:7px 2px;border-bottom:1px solid #F0F2F4')}>
                  <span style={sx('flex:1;font-size:12px;color:#6E7378;line-height:1.5')}>{o.h}</span>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#A2A7AD;font-variant-numeric:tabular-nums")}>{o.t}</span>
                </div>
              ))}
            </div>
          )}

          {/* SECTION 4 · COST */}
          <div style={sx('display:flex;align-items:center;gap:10px;margin:34px 0 10px')}>
            {vm.annotate && <span style={sx('display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--accent,#0047FF);color:#FFFFFF;font-family:\'Geist Mono\',ui-monospace,monospace;font-size:9px;flex-shrink:0')}>5</span>}
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:#A2A7AD;white-space:nowrap")}>COST</span>
            <span style={sx('flex:1;height:1px;background:#EDEFF2')}></span>
          </div>
          {vm.isLoading && (
            <div style={sx('height:11px;width:32%;border-radius:4px;background:linear-gradient(90deg,#EFF1F4 25%,#E6E8EC 37%,#EFF1F4 63%);background-size:420px 100%;animation:shimmer 1.4s linear infinite')}></div>
          )}
          {vm.costShow && (
            <div style={sx('display:flex;align-items:center;gap:22px;flex-wrap:wrap')}>
              <div style={sx('display:flex;align-items:baseline;gap:7px')}>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:13px;font-weight:500;color:#26292D;font-variant-numeric:tabular-nums")}>{vm.costSpent}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#A2A7AD")}>{vm.costSpentLabel}</span>
              </div>
              <div style={sx('display:flex;align-items:baseline;gap:7px')}>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#6E7378;font-variant-numeric:tabular-nums")}>{vm.costWeek}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#A2A7AD")}>THIS WEEK</span>
              </div>
              <div style={sx('display:flex;align-items:center;gap:8px;margin-left:auto')}>
                <div style={sx('width:150px;height:4px;border-radius:2px;background:#ECEDF0;overflow:hidden')}><div style={sx(`height:100%;border-radius:2px;background:#0F7B3A;transition:width .4s ease;width: ${vm.costPct}`)}></div></div>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#0F7B3A;font-variant-numeric:tabular-nums")}>{vm.costLeft} headroom</span>
              </div>
            </div>
          )}

          {/* ACKNOWLEDGE */}
          {vm.annotate && <div style={sx('display:flex;margin:36px 0 -26px')}><span style={sx('display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--accent,#0047FF);color:#FFFFFF;font-family:\'Geist Mono\',ui-monospace,monospace;font-size:9px')}>6</span></div>}
          {vm.ackBtnShow && (
            <div style={sx('margin-top:40px;border:1px solid #DADDE2;border-radius:6px;background:#FFFFFF;padding:16px 18px;display:flex;align-items:center;gap:16px;flex-wrap:wrap')}>
              <div style={sx('flex:1;min-width:240px')}>
                <div style={sx('font-size:13px;font-weight:600')}>{vm.ackCountText}</div>
                <div style={sx('font-size:12px;color:#8A8F96;line-height:1.55;margin-top:3px')}>Dots mark what you haven't seen. Clearing them is your call — it never happens on its own.</div>
              </div>
              <Interactive as="button" onClick={vm.ackClick} style={sx("display:flex;align-items:center;gap:8px;height:32px;padding:0 15px;border-radius:6px;border:1px solid #DADDE2;background:transparent;color:#26292D;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.08em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:var(--accent,#0047FF);color:#08090A')}>
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 8.5l3.5 3.5 7.5-8"></path></svg>
                {vm.ackBtnLabel}
              </Interactive>
            </div>
          )}
          {vm.ackDoneShow && (
            <div style={sx('margin-top:40px;border:1px solid #E4E6EA;border-radius:6px;padding:14px 18px;display:flex;align-items:center;gap:9px;animation:fadeSlide .3s ease-out')}>
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="#0F7B3A" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 8.5l3.5 3.5 7.5-8"></path></svg>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;letter-spacing:0.05em;color:#0F7B3A")}>{vm.ackSeenText}</span>
              <span style={sx('font-size:12px;color:#A2A7AD;margin-left:4px')}>New work brings fresh dots tomorrow.</span>
            </div>
          )}
          {vm.ackNoneShow && (
            <div style={sx("margin-top:40px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.1em;color:#B7BDC4")}>{vm.ackNoneText}</div>
          )}
          {vm.genStampShow && (
            <div style={sx("margin-top:26px;padding-top:13px;border-top:1px solid #EDEFF2;display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.11em;color:#B7BDC4;font-variant-numeric:tabular-nums")}>
              <span>{vm.genStamp}</span><span>·</span><span>ANYTHING AFTER THAT ISN'T IN IT YET</span>
            </div>
          )}
        </div>
      </div>

      {/* ANNOTATION RAIL */}
      {vm.annotate && (
        <div style={sx('width:312px;flex-shrink:0;border-left:1px solid #E4E6EA;background:#FFFFFF;overflow-y:auto;padding:22px 20px 60px;animation:pageIn .3s ease-out')}>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.18em;color:var(--accent,#0047FF)")}>DESIGN NOTES</div>
          <div style={sx('font-size:15px;font-weight:600;letter-spacing:-0.01em;margin-top:8px')}>Why the brief is this exact shape, every day</div>
          <div style={sx('display:flex;flex-direction:column;gap:16px;margin-top:18px')}>
            {vm.notes.map((n, i) => (
              <div key={i} style={sx('display:flex;gap:10px')}>
                <span style={sx("display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--accent,#0047FF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;flex-shrink:0;margin-top:1px")}>{n.n}</span>
                <div>
                  <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#26292D")}>{n.title}</div>
                  <div style={sx('font-size:11.5px;color:#55595E;line-height:1.6;margin-top:4px')}>{n.body}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
