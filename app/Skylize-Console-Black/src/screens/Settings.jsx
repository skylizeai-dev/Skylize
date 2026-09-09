import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function Settings({ vm }) {
  return (
    <div data-screen-label="Settings" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <h1 style={sx('margin:0 0 13px;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Settings</h1>
      <div style={sx('display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start;max-width:1060px')}>
        <div style={sx('display:flex;flex-direction:column;gap:12px')}>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:15px 16px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;margin-bottom:12px")}>ORGANIZATION</div>
            <div style={sx('display:flex;flex-direction:column;gap:10px')}>
              <div>
                <div style={sx('font-size:11px;color:#8B93A7;margin-bottom:5px')}>Organization name</div>
                <Interactive
                  as="input"
                  value={vm.orgName}
                  onChange={vm.onOrgName}
                  style={sx("width:100%;height:32px;padding:0 11px;border:1px solid #232939;border-radius:6px;background:rgba(255,255,255,0.02);font-family:'Geist',system-ui,sans-serif;font-size:12.5px;color:#E9EBF2;transition:border-color .15s")}
                  focusStyle={sx('border-color:var(--accent,#3D6BFF)')}
                />
              </div>
              <div>
                <div style={sx('font-size:11px;color:#8B93A7;margin-bottom:5px')}>Primary region · data residency</div>
                <select value={vm.region} onChange={vm.onRegion} style={sx("width:100%;height:32px;padding:0 8px;border:1px solid #232939;border-radius:6px;background:rgba(255,255,255,0.02);font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#C7CBD6;cursor:pointer")}>
                  <option value="eu-central">EU-CENTRAL · FRANKFURT</option>
                  <option value="us-east">US-EAST · VIRGINIA</option>
                  <option value="ap-south">AP-SOUTH · SINGAPORE</option>
                </select>
              </div>
              <div>
                <div style={sx('font-size:11px;color:#8B93A7;margin-bottom:5px')}>Log retention</div>
                <select value={vm.retention} onChange={vm.onRetention} style={sx("width:100%;height:32px;padding:0 8px;border:1px solid #232939;border-radius:6px;background:rgba(255,255,255,0.02);font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#C7CBD6;cursor:pointer")}>
                  <option value="90">90 DAYS</option>
                  <option value="365">365 DAYS</option>
                  <option value="forever">INDEFINITE · WORM</option>
                </select>
              </div>
            </div>
          </div>
          <div style={sx('background:#0C0F16;border:1px solid rgba(225,90,82,0.3);border-radius:10px;padding:15px 16px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#E15A52;margin-bottom:10px")}>DANGER ZONE</div>
            <div style={sx('display:flex;align-items:center;justify-content:space-between;gap:12px')}>
              <div>
                <div style={sx('font-size:12.5px;font-weight:500;color:#E9EBF2')}>{vm.pauseTitle}</div>
                <div style={sx('font-size:11px;color:#77809A;margin-top:2px')}>Freezes all delegation. Human approval queue stays live.</div>
              </div>
              <Interactive
                as="button"
                onClick={vm.dangerPause}
                style={sx(`height:30px;padding:0 13px;border-radius:6px;border:1px solid rgba(225,90,82,0.5);background: ${vm.pauseBg};color: ${vm.pauseC};font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;cursor:pointer;flex-shrink:0;transition:background-color .15s`)}
                hoverStyle={sx('background-color:rgba(225,90,82,0.18)')}
              >{vm.pauseLabel}</Interactive>
            </div>
          </div>
        </div>
        <div style={sx('display:flex;flex-direction:column;gap:12px')}>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:15px 16px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;margin-bottom:4px")}>DEFAULT AUTONOMY</div>
            <div style={sx('font-size:11px;color:#8B93A7;margin-bottom:11px')}>{vm.autonomyDesc}</div>
            <div style={sx('display:flex;gap:5px')}>
              {vm.autonomyChips.map((au, i) => (
                <Interactive
                  key={i}
                  as="button"
                  onClick={au.pick}
                  style={sx(`flex:1;height:32px;border-radius:6px;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.06em;cursor:pointer;transition:border-color .15s,background-color .15s;border:1px solid ${au.bd};background: ${au.bg};color: ${au.c}`)}
                  hoverStyle={sx('border-color:#77809A')}
                >{au.label}</Interactive>
              ))}
            </div>
          </div>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:6px 0')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;padding:10px 16px 6px")}>GUARDRAILS</div>
            {vm.guardrails.map((gr, i) => (
              <div key={i} style={sx('display:flex;align-items:center;gap:12px;padding:10px 16px;border-top:1px solid #12151F')}>
                <div style={sx('flex:1;min-width:0')}>
                  <div style={sx('font-size:12.5px;font-weight:500;color:#E9EBF2')}>{gr.name}</div>
                  <div style={sx('font-size:11px;color:#77809A;margin-top:2px')}>{gr.desc}</div>
                </div>
                <button onClick={gr.toggle} aria-label="Toggle" style={sx(`position:relative;width:34px;height:19px;border-radius:10px;border:1px solid ${gr.trackBd};background: ${gr.trackBg};cursor:pointer;flex-shrink:0;transition:background-color .18s;padding:0`)}>
                  <span style={sx(`position:absolute;top:2px;width:13px;height:13px;border-radius:50%;background:#E9EBF2;transition:left .18s cubic-bezier(0.2,0.8,0.2,1);left: ${gr.knobX}`)}></span>
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
