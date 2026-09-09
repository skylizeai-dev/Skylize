import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Dashboard({ vm }) {
  return (
    <div data-screen-label="Dashboard" style={sx('flex:1;min-height:0;overflow-y:auto;padding:20px 24px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      {/* hero */}
      <div style={sx('position:relative;display:flex;align-items:stretch;gap:20px;background:linear-gradient(135deg,rgba(18,22,34,0.85),rgba(11,13,20,0.9));border:1px solid #1E2434;border-radius:12px;overflow:hidden;padding:24px 28px;min-height:218px')}>
        <div style={sx('position:absolute;inset:0;background:radial-gradient(600px 260px at 78% 30%, color-mix(in oklab, var(--accent,#3D6BFF) 12%, transparent), transparent 70%);pointer-events:none')}></div>
        <div style={sx('position:relative;flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center;gap:12px;max-width:520px')}>
          <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.18em;color:color-mix(in oklab,var(--accent,#3D6BFF) 80%,#FFFFFF)")}>AUTONOMOUS ORGANIZATION · ONLINE</div>
          <h1 style={sx('margin:0;font-size:24px;font-weight:600;letter-spacing:-0.02em;line-height:1.25')}>Good evening, Operator.<br /><span style={sx('color:#8B93A7;font-weight:500')}>151 agents are on station across 15 departments.</span></h1>
          <div style={sx('display:flex;align-items:center;gap:8px;margin-top:6px;height:42px;padding:0 6px 0 14px;border:1px solid #262D40;border-radius:8px;background:rgba(7,8,12,0.7);transition:border-color .15s')}>
            <span style={sx('font-family:\'Geist Mono\',ui-monospace,monospace;font-size:12px;color:var(--accent,#3D6BFF)')}>❯</span>
            <input
              value={vm.dirInput}
              onChange={vm.onDirInput}
              onKeyDown={vm.dirKey}
              placeholder="Issue a directive to the organization…"
              style={sx("flex:1;border:none;background:transparent;font-family:'Geist Mono',ui-monospace,monospace;font-size:12px;color:#E9EBF2;min-width:0")}
            />
            <Interactive
              as="button"
              onClick={vm.sendDir}
              style={sx("height:30px;padding:0 14px;border-radius:6px;border:none;background:var(--accent,#3D6BFF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.08em;cursor:pointer;box-shadow:0 0 18px color-mix(in oklab,var(--accent,#3D6BFF) 45%,transparent);transition:filter .15s")}
              hoverStyle={sx('filter:brightness(1.15)')}
            >EXECUTE</Interactive>
          </div>
        </div>
        {/* 3D authority stack */}
        <div style={sx(`position:relative;width:340px;flex-shrink:0;perspective:900px;display: ${vm.heroStackDisp}`)}>
          <div style={sx('position:absolute;inset:0;transform-style:preserve-3d;transform:translateY(30px) rotateX(56deg) rotateZ(-42deg) translateX(6px)')}>
            {vm.tierLayers.map((tr, i) => (
              <div key={i} onClick={tr.go} style={sx(`position:absolute;left:50%;top:50%;width: ${tr.w};height: ${tr.h};transform: ${tr.tf};transform-style:preserve-3d;cursor:pointer`)}>
                <Interactive
                  style={sx(`width:100%;height:100%;border-radius:10px;border:1px solid ${tr.bd};background: ${tr.bg};backdrop-filter:blur(2px);display:flex;align-items:center;justify-content:space-between;padding:0 14px;transition:transform .25s cubic-bezier(0.2,0.8,0.2,1),box-shadow .25s;box-shadow:0 10px 30px rgba(0,0,0,0.35)`)}
                  hoverStyle={sx('transform:translateZ(22px);box-shadow:0 22px 50px rgba(0,0,0,0.5)')}
                >
                  <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color: ${tr.c}`)}>{tr.label}</span>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:12px;font-weight:600;color:#E9EBF2;font-variant-numeric:tabular-nums")}>{tr.count}</span>
                </Interactive>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* KPI cards */}
      <div style={sx('display:grid;grid-template-columns:repeat(auto-fit,minmax(186px,1fr));gap:12px;margin-top:14px;perspective:1200px')}>
        {vm.kpis.map((k, i) => (
          <Interactive
            key={i}
            onMouseMove={vm.tiltMove}
            onMouseLeave={vm.tiltLeave}
            style={sx('background:linear-gradient(180deg,#0E1119,#0B0D14);border:1px solid #1B2130;border-radius:10px;padding:14px 15px 12px;min-width:0;transition:border-color .2s,box-shadow .2s;will-change:transform;transform-style:preserve-3d')}
            hoverStyle={sx('border-color:#2B3450;box-shadow:0 14px 34px rgba(0,0,0,0.45), 0 0 24px color-mix(in oklab,var(--accent,#3D6BFF) 10%,transparent)')}
          >
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#77809A;margin-bottom:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{k.label}</div>
            <div style={sx('display:flex;align-items:baseline;gap:6px')}>
              <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:25px;font-weight:600;line-height:1;font-variant-numeric:tabular-nums;letter-spacing:-0.02em;color: ${k.valColor}`)}>{k.value}</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#77809A;font-variant-numeric:tabular-nums")}>{k.sub}</span>
            </div>
            <div style={sx('display:flex;align-items:center;justify-content:space-between;margin-top:10px;height:22px;gap:8px')}>
              <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;font-variant-numeric:tabular-nums;white-space:nowrap;color: ${k.deltaColor}`)}>{k.delta}</span>
              <svg width="64" height="22" viewBox="0 0 64 22" aria-hidden="true"><polyline points={k.spark} fill="none" stroke={k.sparkColor} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round"></polyline></svg>
            </div>
          </Interactive>
        ))}
      </div>

      {/* middle grid */}
      <div style={sx('display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,1fr);gap:12px;margin-top:12px;align-items:start')}>
        <div style={sx('display:flex;flex-direction:column;gap:12px;min-width:0')}>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
            <div style={sx('display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid #161A26')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>SPEND BY DEPARTMENT · 24H</span>
              <Interactive
                as="button"
                onClick={vm.goAnalytics}
                style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;background:none;border:none;cursor:pointer;letter-spacing:0.06em;transition:color .15s;padding:0")}
                hoverStyle={sx('color:var(--accent,#3D6BFF)')}
              >ANALYTICS →</Interactive>
            </div>
            <div style={sx('display:flex;flex-direction:column;gap:9px;padding:13px 14px')}>
              {vm.deptBars.map((b, i) => (
                <div key={i} style={sx('display:grid;grid-template-columns:120px 1fr 58px;gap:10px;align-items:center')}>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color:#9AA1B2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{b.name}</span>
                  <span style={sx('height:5px;background:#141826;border-radius:3px;overflow:hidden')}><span style={sx(`display:block;height:100%;border-radius:3px;background:linear-gradient(90deg, ${b.color}, color-mix(in oklab, ${b.color} 60%, #FFFFFF));width: ${b.w};box-shadow:0 0 10px ${b.color}`)}></span></span>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2;text-align:right;font-variant-numeric:tabular-nums")}>{b.amt}</span>
                </div>
              ))}
            </div>
          </div>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
            <div style={sx('display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid #161A26')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>AWAITING YOUR APPROVAL</span>
              <Interactive as="button" onClick={vm.goApprovals} style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#D19A3F;border:1px solid rgba(209,154,63,0.4);border-radius:4px;padding:2px 7px;background:none;cursor:pointer;font-variant-numeric:tabular-nums")}>{vm.apPrevN} PENDING</Interactive>
            </div>
            {vm.apPrev.map((ap, i) => (
              <div key={i} style={sx('display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid #141826')}>
                <span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:2px 6px;border-radius:3px;color: ${ap.riskColor};background: ${ap.riskBg};flex-shrink:0`)}>{ap.risk}</span>
                <div style={sx('flex:1;min-width:0')}>
                  <div style={sx('font-size:12.5px;font-weight:500;color:#E9EBF2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{ap.title}</div>
                  <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;margin-top:2px")}>{ap.meta}</div>
                </div>
                <Interactive as="button" onClick={ap.decline} style={sx("height:26px;padding:0 11px;border-radius:5px;border:1px solid #262D40;background:transparent;color:#8B93A7;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:#77809A;color:#E9EBF2')}>DECLINE</Interactive>
                <Interactive as="button" onClick={ap.approve} style={sx("height:26px;padding:0 11px;border-radius:5px;border:none;background:var(--accent,#3D6BFF);color:#FFFFFF;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;cursor:pointer;box-shadow:0 0 14px color-mix(in oklab,var(--accent,#3D6BFF) 35%,transparent);transition:filter .15s")} hoverStyle={sx('filter:brightness(1.15)')}>APPROVE</Interactive>
              </div>
            ))}
          </div>
        </div>
        <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
          <div style={sx('display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid #161A26')}>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>SYSTEM PULSE</span>
            <span style={sx("display:flex;align-items:center;gap:5px;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;color:#34C579")}><span style={sx('width:5px;height:5px;border-radius:50%;background:#34C579;box-shadow:0 0 8px rgba(52,197,121,0.8);animation:pulseDot 1.6s ease-in-out infinite')}></span>LIVE</span>
          </div>
          <div style={sx('max-height:472px;overflow-y:auto')}>
            {vm.pulseLogs.map((l, i) => (
              <div key={i} style={sx("padding:6px 12px;border-bottom:1px solid #12151F;font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;line-height:1.5")}>
                <span style={sx('color:#616A82;font-variant-numeric:tabular-nums')}>{l.time}</span>
                <span style={sx(`color: ${l.agentColor}`)}> {l.agent}</span>
                <span style={sx('color:#8B93A7')}> · {l.action}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
