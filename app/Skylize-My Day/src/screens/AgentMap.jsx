import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function AgentMap({ vm }) {
  return (
    <div data-screen-label="Agent map" style={sx('flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('max-width:940px;margin:0 auto;padding:34px 36px 90px')}>

        <div style={sx('display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap')}>
          <div style={sx('flex:1;min-width:280px')}>
            <div style={sx('display:flex;align-items:center;gap:8px')}>
              {vm.annotate && <span style={sx('display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--accent,#0047FF);color:#FFFFFF;font-family:\'Geist Mono\',ui-monospace,monospace;font-size:9px;flex-shrink:0')}>9</span>}
              <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.18em;color:#8A8F96")}>THE WHOLE PICTURE · ONE SCREEN</div>
            </div>
            <h1 style={sx('margin:11px 0 0;font-size:22px;font-weight:600;letter-spacing:-0.02em;line-height:1.3')}>Agent map</h1>
            <div style={sx('font-size:13px;color:#55595E;line-height:1.65;margin-top:8px;max-width:600px')}>Where your authority ends, what your agent did inside it, and what it handed back. Every other screen is a close-up of something on this one.</div>
          </div>
          <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.11em;color:#B7BDC4;border:1px solid #E4E6EA;border-radius:4px;padding:5px 8px;white-space:nowrap;font-variant-numeric:tabular-nums")}>{vm.mapStamp}</span>
        </div>

        {vm.mapWaitShow && (
          <Interactive as="button" onClick={vm.goBrief} style={sx('display:flex;align-items:center;gap:10px;width:100%;margin-top:22px;padding:12px 15px;border:1px solid rgba(180,83,9,0.35);border-radius:6px;background:rgba(180,83,9,0.05);cursor:pointer;text-align:left;font-family:inherit;transition:border-color .15s')} hoverStyle={sx('border-color:#B45309')}>
            <span style={sx('width:6px;height:6px;border-radius:50%;background:#B45309;flex-shrink:0')}></span>
            <span style={sx('flex:1;min-width:0;font-size:12.5px;color:#26292D')}>{vm.mapWaitText}</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#B45309;white-space:nowrap")}>GO TO THE DECISIONS →</span>
          </Interactive>
        )}

        <div style={sx('display:flex;align-items:center;gap:10px;margin-top:28px;margin-bottom:11px')}>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:#8A8F96")}>1 · THE PERIMETER</div>
          <span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
          <Interactive as="button" onClick={vm.goAuth} style={sx("border:none;background:none;padding:0;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#8A8F96;cursor:pointer;white-space:nowrap;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>ALL PERMISSIONS →</Interactive>
        </div>
        <div style={sx('display:flex;flex-wrap:wrap;border:1px solid #E4E6EA;border-radius:6px;background:#FFFFFF;overflow:hidden')}>
          <div style={sx('flex:1;min-width:260px;padding:16px 19px 18px')}>
            <div style={sx("display:flex;align-items:center;gap:7px;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.12em;color:#0F7B3A")}>
              <span style={sx('width:5px;height:5px;border-radius:50%;background:#0F7B3A')}></span>ACTS ALONE — NO ASKING
            </div>
            <div style={sx('display:flex;flex-direction:column;gap:8px;margin-top:12px')}>
              {vm.mapInside.map((mi, i) => (
                <div key={i} style={sx('font-size:12.5px;color:#26292D;line-height:1.5')}>{mi.t}</div>
              ))}
            </div>
          </div>
          <div style={sx('flex:1;min-width:260px;padding:16px 19px 18px;border-left:1px solid #E4E6EA;background:rgba(180,83,9,0.022)')}>
            <div style={sx("display:flex;align-items:center;gap:7px;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.12em;color:#B45309")}>
              <span style={sx('width:5px;height:5px;border-radius:50%;background:#B45309')}></span>ALWAYS ASKS YOU FIRST
            </div>
            <div style={sx('display:flex;flex-direction:column;gap:8px;margin-top:12px')}>
              {vm.mapEdges.map((me, i) => (
                <div key={i} style={sx('display:flex;align-items:baseline;gap:10px')}>
                  <span style={sx(`flex:1;min-width:0;font-size:12.5px;line-height:1.5;color: ${me.c}`)}>{me.t}</span>
                  <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.09em;white-space:nowrap;font-variant-numeric:tabular-nums;color: ${me.nC}`)}>{me.n}</span>
                </div>
              ))}
            </div>
          </div>
          <div style={sx('width:100%;display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:13px 19px;border-top:1px solid #E4E6EA')}>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.12em;color:#8A8F96;white-space:nowrap")}>SPENDING HEADROOM</span>
            <span style={sx('flex:1;min-width:110px;height:5px;border-radius:3px;background:#EDEFF2;overflow:hidden')}><span style={sx(`display:block;height:100%;border-radius:3px;background:var(--accent,#0047FF);width: ${vm.authPct}`)}></span></span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#08090A;font-variant-numeric:tabular-nums;white-space:nowrap")}>{vm.mapLeft} LEFT OF $250</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.09em;color:#B7BDC4;font-variant-numeric:tabular-nums;white-space:nowrap")}>SPENT {vm.mapSpent} {vm.mapSpentLabel}</span>
          </div>
        </div>

        <div style={sx('display:flex;align-items:center;gap:10px;margin-top:30px;margin-bottom:11px')}>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:#8A8F96")}>2 · {vm.mapFlowTitle}</div>
          <span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
          <Interactive as="button" onClick={vm.goBrief} style={sx("border:none;background:none;padding:0;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#8A8F96;cursor:pointer;white-space:nowrap;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>THE BRIEF →</Interactive>
        </div>
        <div style={sx('display:flex;align-items:stretch;gap:7px;flex-wrap:wrap')}>
          {vm.mapFlow.map((fn, i) => (
            <div key={i} style={sx('display:flex;align-items:center;gap:7px;flex:1;min-width:170px')}>
              <span style={sx(`font-size:13px;color:#C6CAD1;flex-shrink:0;display: ${fn.arrowDisp}`)}>→</span>
              <Interactive as="button" onClick={fn.click} style={sx(`flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;padding:13px 14px 14px;border-radius:6px;cursor:pointer;text-align:left;font-family:inherit;transition:border-color .15s;border:1px solid ${fn.bd};background: ${fn.bg}`)} hoverStyle={sx('border-color:#08090A')}>
                <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:21px;line-height:1;font-variant-numeric:tabular-nums;color: ${fn.c}`)}>{fn.value}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;color:#55595E;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{fn.label}</span>
                <span style={sx('font-size:11px;color:#8A8F96;line-height:1.5')}>{fn.sub}</span>
              </Interactive>
            </div>
          ))}
        </div>

        <div style={sx('display:flex;align-items:center;gap:10px;margin-top:30px;margin-bottom:11px')}>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:#8A8F96")}>3 · THE LAST NINE NIGHTS</div>
          <span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
          <Interactive as="button" onClick={vm.goPast} style={sx("border:none;background:none;padding:0;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#8A8F96;cursor:pointer;white-space:nowrap;transition:color .15s")} hoverStyle={sx('color:var(--accent,#0047FF)')}>OPEN THE RECORD →</Interactive>
        </div>
        <div style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:16px 19px 14px')}>
          <div style={sx('display:flex;align-items:stretch;gap:6px;height:96px')}>
            {vm.mapDays.map((md, i) => (
              <div key={i} style={sx('flex:1;min-width:0;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:5px')}>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;color:#A2A7AD;font-variant-numeric:tabular-nums")}>{md.n}</span>
                <span style={sx(`width:100%;border-radius:2px 2px 0 0;background: ${md.bg};height: ${md.h}`)}></span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8px;letter-spacing:0.05em;color:#B7BDC4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%")}>{md.label}</span>
              </div>
            ))}
          </div>
          <div style={sx("margin-top:13px;padding-top:11px;border-top:1px solid #F0F2F4;display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.09em;color:#A2A7AD;font-variant-numeric:tabular-nums")}>
            <span>{vm.mapDaysTotal}</span><span>·</span><span>{vm.mapDaysYours}</span><span>·</span><span>BUSIEST {vm.mapDaysPeak}</span>
          </div>
        </div>

        <div style={sx('display:flex;align-items:center;gap:10px;margin-top:30px;margin-bottom:11px')}>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:#8A8F96")}>4 · EVERY SURFACE, RIGHT NOW</div>
          <span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
        </div>
        <div style={sx('display:grid;grid-template-columns:repeat(auto-fit,minmax(212px,1fr));gap:9px')}>
          {vm.mapSurfaces.map((ms, i) => (
            <Interactive key={i} as="button" onClick={ms.click} style={sx('display:flex;flex-direction:column;gap:7px;padding:14px 15px;border:1px solid #E4E6EA;border-radius:6px;background:#FFFFFF;cursor:pointer;text-align:left;font-family:inherit;transition:border-color .15s')} hoverStyle={sx('border-color:#08090A')}>
              <div style={sx('display:flex;align-items:center;gap:8px')}>
                <span style={sx(`width:5px;height:5px;border-radius:50%;flex-shrink:0;background: ${ms.dot}`)}></span>
                <span style={sx('flex:1;min-width:0;font-size:12.5px;font-weight:600;letter-spacing:-0.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{ms.label}</span>
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;color:#B7BDC4;flex-shrink:0")}>→</span>
              </div>
              <div style={sx('font-size:11.5px;color:#8A8F96;line-height:1.5')}>{ms.status}</div>
            </Interactive>
          ))}
        </div>

        <div style={sx("margin-top:22px;padding-top:13px;border-top:1px solid #E4E6EA;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.11em;color:#B7BDC4;line-height:1.7")}>ONE RECORD BEHIND EVERY NUMBER HERE · KEPT 90 DAYS · WRITE-ONCE</div>
      </div>
    </div>
  );
}
