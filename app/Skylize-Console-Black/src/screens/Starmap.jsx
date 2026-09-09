import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Starmap({ vm }) {
  return (
    <div data-screen-label="Starmap" style={sx('flex:1;min-height:0;display:flex;flex-direction:column;padding:14px 22px 16px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Starmap</h1>
        <div style={sx('display:flex;align-items:center;gap:6px;height:32px;padding:0 6px;border:1px solid #232939;border-radius:9px;background:rgba(255,255,255,0.02)')}>
          <Interactive as="button" onClick={vm.starPrev} aria-label="Previous department" style={sx('display:flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:6px;border:none;background:transparent;color:#8B93A7;cursor:pointer;transition:color .15s,background-color .15s')} hoverStyle={sx('color:#E9EBF2;background-color:rgba(255,255,255,0.06)')}><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M10 3.5L5.5 8l4.5 4.5"></path></svg></Interactive>
          <div style={sx('display:flex;align-items:center;gap:8px;min-width:188px;justify-content:center')}>
            <span style={sx(`width:8px;height:8px;border-radius:2px;background: ${vm.starColor};box-shadow:0 0 8px ${vm.starColor}`)}></span>
            <span style={sx('font-size:12.5px;font-weight:600;color:#E9EBF2;white-space:nowrap')}>{vm.starDeptName}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;font-variant-numeric:tabular-nums")}>{vm.starIdxLabel}/15</span>
          </div>
          <Interactive as="button" onClick={vm.starNext} aria-label="Next department" style={sx('display:flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:6px;border:none;background:transparent;color:#8B93A7;cursor:pointer;transition:color .15s,background-color .15s')} hoverStyle={sx('color:#E9EBF2;background-color:rgba(255,255,255,0.06)')}><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3.5L10.5 8L6 12.5"></path></svg></Interactive>
        </div>
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;color:#77809A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:260px")}>{vm.starTagline}</span>
        <div style={sx("margin-left:auto;display:flex;align-items:center;gap:14px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#8B93A7")}>
          <span style={sx('display:flex;align-items:center;gap:5px')}><span style={sx(`width:9px;height:9px;border-radius:50%;background: ${vm.starColor};box-shadow:0 0 7px ${vm.starColor}`)}></span>CORE</span>
          <span style={sx('display:flex;align-items:center;gap:5px')}><span style={sx(`width:7px;height:7px;border-radius:50%;background: ${vm.starColor}`)}></span>DIRECTORS</span>
          <span style={sx('display:flex;align-items:center;gap:5px')}><span style={sx(`width:5px;height:5px;border-radius:50%;background: ${vm.starColor};opacity:0.85`)}></span>MANAGERS</span>
          <span style={sx('display:flex;align-items:center;gap:5px')}><span style={sx(`width:4px;height:4px;border-radius:50%;background: ${vm.starColor};opacity:0.6`)}></span>WORKERS</span>
        </div>
      </div>

      <div style={sx('flex:1;min-height:0;display:flex;gap:12px')}>
        <div style={sx('width:248px;flex-shrink:0;display:flex;flex-direction:column;gap:10px;min-height:0')}>
          <div style={sx('background:linear-gradient(180deg,#0E1119,#0B0D14);border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
            <div style={sx('display:flex;align-items:center;gap:8px;padding:12px 14px 10px')}>
              <span style={sx(`width:10px;height:10px;border-radius:3px;background: ${vm.starColor};box-shadow:0 0 10px ${vm.starColor}`)}></span>
              <span style={sx('font-size:13.5px;font-weight:600;color:#E9EBF2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{vm.starDeptName}</span>
            </div>
            <div style={sx('display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1B2130;border-top:1px solid #1B2130')}>
              <div style={sx('background:#0C0F16;padding:9px 14px')}><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82")}>AGENTS</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:14px;color:#E9EBF2;margin-top:3px;font-variant-numeric:tabular-nums")}>{vm.starAgents}</div></div>
              <div style={sx('background:#0C0F16;padding:9px 14px')}><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82")}>EXECUTING</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:14px;color:#34C579;margin-top:3px;font-variant-numeric:tabular-nums")}>{vm.starExecN}</div></div>
              <div style={sx('background:#0C0F16;padding:9px 14px')}><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82")}>TOKENS 24H</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:14px;color:#E9EBF2;margin-top:3px;font-variant-numeric:tabular-nums")}>{vm.starTokFmt}</div></div>
              <div style={sx('background:#0C0F16;padding:9px 14px')}><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82")}>HEAD</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#C7CBD6;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{vm.starHead}</div></div>
            </div>
          </div>
          <div style={sx('flex:1;min-height:0;background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden;display:flex;flex-direction:column')}>
            <div style={sx('display:flex;align-items:center;gap:7px;height:34px;padding:0 12px;border-bottom:1px solid #161A26;flex-shrink:0')}>
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="#616A82" strokeWidth="1.5" strokeLinecap="round"><circle cx="7" cy="7" r="4"></circle><path d="M10 10l3.5 3.5"></path></svg>
              <input value={vm.starQ} onChange={vm.onStarQ} placeholder="Filter agents in this dept…" style={sx("flex:1;border:none;background:transparent;font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#E9EBF2;min-width:0")} />
            </div>
            <div style={sx('flex:1;overflow-y:auto')}>
              {vm.starEmpty && (
                <div style={sx("padding:20px 14px;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;line-height:1.7;color:#616A82")}>No agents stationed in this department yet.</div>
              )}
              {vm.starList.map((a, i) => (
                <Interactive key={i} onClick={a.click} style={sx(`display:flex;align-items:center;gap:9px;padding:7px 12px;border-bottom:1px solid #12151F;cursor:pointer;background: ${a.bg};transition:background-color .12s`)} hoverStyle={sx('background-color:rgba(255,255,255,0.03)')}>
                  <span style={sx(`width:6px;height:6px;border-radius:50%;flex-shrink:0;background: ${a.statusColor};box-shadow:0 0 6px ${a.statusColor}`)}></span>
                  <span style={sx(`flex:1;font-size:12px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color: ${a.c}`)}>{a.name}</span>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.06em;color:#77809A")}>{a.auth}</span>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#8B93A7;font-variant-numeric:tabular-nums")}>{a.tok}</span>
                </Interactive>
              ))}
            </div>
          </div>
        </div>

        <div onMouseMove={vm.starTilt} onMouseLeave={vm.starTiltReset} style={sx(`position:relative;flex:1;min-width:0;border:1px solid #1B2130;border-radius:12px;overflow:hidden;perspective:1200px;background:radial-gradient(680px 460px at 50% 46%, ${vm.starGlow}, transparent 66%),#080A11`)}>
          <div style={sx('position:absolute;inset:-60px;pointer-events:none;opacity:0.55;animation:driftA 26s linear infinite alternate;background-image:radial-gradient(1.4px 1.4px at 12% 22%, rgba(255,255,255,.7), transparent),radial-gradient(1px 1px at 28% 66%, rgba(200,220,255,.6), transparent),radial-gradient(1.4px 1.4px at 46% 14%, rgba(255,255,255,.55), transparent),radial-gradient(1px 1px at 63% 44%, rgba(255,255,255,.55), transparent),radial-gradient(1.5px 1.5px at 78% 74%, rgba(210,225,255,.5), transparent),radial-gradient(1px 1px at 88% 30%, rgba(255,255,255,.6), transparent),radial-gradient(1px 1px at 8% 82%, rgba(255,255,255,.5), transparent),radial-gradient(1.4px 1.4px at 36% 88%, rgba(255,255,255,.45), transparent),radial-gradient(1px 1px at 54% 60%, rgba(255,255,255,.5), transparent),radial-gradient(1px 1px at 70% 10%, rgba(255,255,255,.55), transparent)')}></div>
          <div style={sx('position:absolute;inset:-60px;pointer-events:none;opacity:0.3;animation:driftB 40s linear infinite alternate;background-image:radial-gradient(1px 1px at 18% 40%, rgba(255,255,255,.5), transparent),radial-gradient(1px 1px at 40% 80%, rgba(255,255,255,.4), transparent),radial-gradient(1px 1px at 60% 24%, rgba(255,255,255,.45), transparent),radial-gradient(1px 1px at 82% 58%, rgba(255,255,255,.4), transparent),radial-gradient(1px 1px at 92% 84%, rgba(255,255,255,.4), transparent),radial-gradient(1px 1px at 30% 12%, rgba(255,255,255,.4), transparent)')}></div>
          <div style={sx(`position:absolute;left:50%;top:46%;width:520px;height:520px;transform:translate(-50%,-50%);border-radius:50%;background:radial-gradient(circle, ${vm.starGlow}, transparent 62%);filter:blur(4px);animation:corePulse 5.5s ease-in-out infinite;pointer-events:none`)}></div>

          <div ref={vm.starTiltRef} style={sx('position:absolute;inset:0;transform-style:preserve-3d;transition:transform .28s ease-out')}>
            <div ref={vm.starPlaneRef} style={sx('position:absolute;left:50%;top:46%;width:760px;height:500px;margin:-250px 0 0 -380px;transform-style:preserve-3d')}>
              <div style={sx('position:absolute;left:380px;top:250px;width:300px;height:300px;margin:-150px 0 0 -150px;border-radius:50%;border:1px solid rgba(122,134,166,0.14)')}></div>
              <div style={sx('position:absolute;left:380px;top:250px;width:410px;height:410px;margin:-205px 0 0 -205px;border-radius:50%;border:1px solid rgba(122,134,166,0.1)')}></div>
              <div style={sx('position:absolute;left:380px;top:250px;width:494px;height:494px;margin:-247px 0 0 -247px;border-radius:50%;border:1px dashed rgba(122,134,166,0.12);animation:spinSlow 90s linear infinite')}></div>

              <svg width="760" height="500" viewBox="0 0 760 500" style={{ position: 'absolute', left: 0, top: 0, pointerEvents: 'none', overflow: 'visible' }}>
                {vm.starLines.map((ln, i) => (
                  <line key={i} className="sk-line" x1={ln.x1} y1={ln.y1} x2={ln.x2} y2={ln.y2} stroke={ln.stroke} strokeWidth={ln.w} strokeOpacity={ln.op}></line>
                ))}
              </svg>

              {vm.starNodes.map((n, i) => (
                <div key={i} style={sx(`position:absolute;left: ${n.left};top: ${n.top};width: ${n.size};height: ${n.size};transform:translateZ(${n.z});transform-style:preserve-3d`)}>
                  <div className="sk-star" onClick={n.click} style={sx(`width:100%;height:100%;border-radius:50%;cursor:pointer;background:radial-gradient(circle at 34% 30%, #FFFFFF, ${n.color} 72%);box-shadow:0 0 ${n.glow} ${n.color}${n.ring};opacity: ${n.op};transform:scale(${n.scale});transition:opacity .3s,transform .2s;animation: ${n.anim}`)}></div>
                  <div style={sx(`position:absolute;left:50%;top:calc(100% + 3px);transform:translateX(-50%);white-space:nowrap;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.05em;color:#AEB4C2;text-shadow:0 1px 5px #000;pointer-events:none;display: ${n.labelShow}`)}>{n.label}</div>
                </div>
              ))}
            </div>
          </div>

          <Interactive as="button" onClick={vm.starPrev} aria-label="Previous" style={sx('position:absolute;left:12px;top:50%;transform:translateY(-50%);display:flex;align-items:center;justify-content:center;width:34px;height:62px;border-radius:9px;border:1px solid #1E2434;background:rgba(10,12,18,0.6);backdrop-filter:blur(8px);color:#9AA1B2;cursor:pointer;transition:color .15s,border-color .15s,box-shadow .15s;z-index:5')} hoverStyle={sx('color:#E9EBF2;border-color:var(--accent,#3D6BFF);box-shadow:0 0 18px color-mix(in oklab,var(--accent,#3D6BFF) 25%,transparent)')}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M10 3.5L5.5 8l4.5 4.5"></path></svg></Interactive>
          <Interactive as="button" onClick={vm.starNext} aria-label="Next" style={sx('position:absolute;right:12px;top:50%;transform:translateY(-50%);display:flex;align-items:center;justify-content:center;width:34px;height:62px;border-radius:9px;border:1px solid #1E2434;background:rgba(10,12,18,0.6);backdrop-filter:blur(8px);color:#9AA1B2;cursor:pointer;transition:color .15s,border-color .15s,box-shadow .15s;z-index:5')} hoverStyle={sx('color:#E9EBF2;border-color:var(--accent,#3D6BFF);box-shadow:0 0 18px color-mix(in oklab,var(--accent,#3D6BFF) 25%,transparent)')}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3.5L10.5 8L6 12.5"></path></svg></Interactive>

          <div style={sx("position:absolute;left:16px;top:14px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#77809A;pointer-events:none")}>AUTHORITY STARMAP · ◂ ▸ ARROW KEYS TO TRAVEL</div>
          <div style={sx("position:absolute;right:16px;top:14px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#34C579;pointer-events:none")}>{vm.starExecN} EXECUTING</div>

          <div style={sx('position:absolute;left:16px;right:16px;bottom:14px;display:flex;align-items:center;justify-content:center;gap:6px')}>
            {vm.starDots.map((dt, i) => (
              <button key={i} onClick={dt.click} aria-label="Department" style={sx(`height:7px;border:none;border-radius:4px;cursor:pointer;padding:0;transition:width .25s,background-color .25s;width: ${dt.w};background: ${dt.bg};box-shadow: ${dt.glow}`)}></button>
            ))}
          </div>
        </div>

        {vm.hasSel && (
          <div style={sx('width:312px;flex-shrink:0;background:linear-gradient(180deg,#0E1119,#0B0D14);border:1px solid #232939;border-radius:10px;overflow-y:auto;animation:fadeSlide .18s ease-out;box-shadow:-12px 0 40px rgba(0,0,0,0.35)')}>
            <div style={sx('display:flex;align-items:flex-start;justify-content:space-between;padding:13px 14px;border-bottom:1px solid #1B2130')}>
              <div>
                <div style={sx('font-size:14px;font-weight:600;letter-spacing:-0.01em')}>{vm.sel.name}</div>
                <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.1em;color:#77809A;margin-top:3px")}>{vm.sel.id}</div>
              </div>
              <Interactive as="button" onClick={vm.closeSel} aria-label="Close" style={sx('display:flex;align-items:center;justify-content:center;width:22px;height:22px;border:1px solid transparent;border-radius:5px;background:none;color:#77809A;cursor:pointer;transition:border-color .15s,color .15s;padding:0')} hoverStyle={sx('border-color:#232939;color:#E9EBF2')}><svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M4 4l8 8M12 4l-8 8"></path></svg></Interactive>
            </div>
            <div style={sx('padding:11px 14px;border-bottom:1px solid #1B2130;font-size:12px;line-height:1.55;color:#9AA1B2')}>{vm.sel.role}</div>
            <div style={sx('display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1B2130;border-bottom:1px solid #1B2130')}>
              <div style={sx('background:#0C0F16;padding:9px 14px')}><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82")}>AUTHORITY</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#E9EBF2;margin-top:3px;text-transform:uppercase")}>{vm.sel.auth}</div></div>
              <div style={sx('background:#0C0F16;padding:9px 14px')}><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82")}>STATUS</div><div style={sx('display:flex;align-items:center;gap:5px;margin-top:4px')}><span style={sx(`width:5px;height:5px;border-radius:50%;background: ${vm.sel.statusColor};box-shadow:0 0 6px ${vm.sel.statusColor}`)}></span><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;color: ${vm.sel.statusColor}`)}>{vm.sel.statusLabel}</span></div></div>
              <div style={sx('background:#0C0F16;padding:9px 14px')}><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82")}>DEPARTMENT</div><div style={sx('display:flex;align-items:center;gap:6px;margin-top:4px')}><span style={sx(`width:6px;height:6px;border-radius:2px;background: ${vm.sel.deptColor}`)}></span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#C7CBD6")}>{vm.sel.deptName}</span></div></div>
              <div style={sx('background:#0C0F16;padding:9px 14px')}><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82")}>TASKS DONE</div><div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#E9EBF2;margin-top:3px;font-variant-numeric:tabular-nums")}>{vm.sel.tasksFmt}</div></div>
            </div>
            <div style={sx('padding:11px 14px;border-bottom:1px solid #1B2130')}>
              <div style={sx("display:flex;justify-content:space-between;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82")}><span>TOKEN BUDGET</span><span style={sx('font-variant-numeric:tabular-nums;color:#9AA1B2')}>{vm.sel.tokensFmt} / {vm.sel.budgetFmt}</span></div>
              <div style={sx('height:4px;background:#141826;border-radius:2px;overflow:hidden;margin-top:7px')}><div style={sx(`height:100%;background: ${vm.sel.barColor};width: ${vm.sel.barW};transition:width .3s;box-shadow:0 0 8px ${vm.sel.barColor}`)}></div></div>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#77809A;margin-top:5px;font-variant-numeric:tabular-nums")}>{vm.sel.pct}% consumed · resets 00:00 UTC</div>
            </div>
            <div style={sx('padding:11px 14px;border-bottom:1px solid #1B2130')}>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82;margin-bottom:7px")}>TOOL GRANTS</div>
              {vm.sel.tools.map((tt, i) => (
                <div key={i} style={sx('display:flex;align-items:baseline;gap:8px;padding:4px 0')}>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:var(--accent,#3D6BFF)")}>{tt.name}</span>
                  <span style={sx('font-size:11px;color:#77809A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{tt.purpose}</span>
                </div>
              ))}
            </div>
            <div style={sx('padding:11px 14px')}>
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.13em;color:#616A82;margin-bottom:7px")}>ESCALATION PATH</div>
              {vm.sel.path.map((p, i) => (
                <div key={i} style={sx("display:flex;align-items:center;gap:7px;padding:3px 0;font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2")}><span style={sx('color:#3C4150')}>{p.arrow}</span><span>{p.name}</span></div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
