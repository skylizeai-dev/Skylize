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
                <div style={sx('font-size:11px;color:#77809A;margin-top:2px')}>Halts the tenant scope at the backend. The approval queue stays readable.</div>
                <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.08em;color:#4A5162;margin-top:4px")}>{vm.pauseScope}</div>
              </div>
              <Interactive
                as="button"
                onClick={vm.dangerPause}
                disabled={vm.pauseBusy}
                style={sx(`height:30px;padding:0 13px;border-radius:6px;border:1px solid rgba(225,90,82,0.5);background: ${vm.pauseBg};color: ${vm.pauseC};font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.08em;cursor:pointer;flex-shrink:0;transition:background-color .15s`)}
                hoverStyle={sx('background-color:rgba(225,90,82,0.18)')}
              >{vm.pauseLabel}</Interactive>
            </div>
            {/* REQUIRED BY THE BACKEND, AND NOT DEFAULTED. The reason is written
                into the audit record; the console must not put words there on
                the operator's behalf. */}
            {vm.pauseReasonShow && (
              <div style={sx('margin-top:11px')}>
                <label style={sx("display:block;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#E15A52;margin-bottom:5px")}>REASON — RECORDED IN THE AUDIT TRAIL</label>
                <input
                  value={vm.pauseReason}
                  onChange={vm.onPauseReason}
                  autoFocus
                  placeholder="Why are you halting every agent?"
                  style={sx("width:100%;box-sizing:border-box;height:32px;padding:0 10px;border-radius:6px;border:1px solid rgba(225,90,82,0.4);background:#07080C;color:#E9EBF2;font-family:'Geist',system-ui,sans-serif;font-size:12.5px")}
                />
              </div>
            )}
            {vm.pauseError && (
              <div style={sx("margin-top:9px;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#E15A52;line-height:1.55")}>{vm.pauseError}</div>
            )}
          </div>
        </div>
        <div style={sx('display:flex;flex-direction:column;gap:12px')}>
          <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:15px 16px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;margin-bottom:4px")}>ORG AUTONOMY</div>
            <div style={sx('font-size:11px;color:#8B93A7;margin-bottom:5px')}>{vm.autonomyDesc}</div>
            {/* Where the displayed value came from: the org's stored posture,
                the fail-closed default, or a read that failed. Without this the
                three are indistinguishable on screen. */}
            <div style={sx('font-size:10.5px;color:#77809A;margin-bottom:11px')} aria-live="polite">{vm.autonomyStatus}</div>
            {vm.autonomyError ? (
              <div
                role="alert"
                style={sx('display:flex;gap:8px;align-items:flex-start;margin-bottom:11px;padding:8px 10px;border-radius:6px;border:1px solid rgba(225,90,82,0.5);background:rgba(225,90,82,0.1)')}
              >
                <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.08em;color:#E15A52;flex-shrink:0;padding-top:1px")}>{vm.autonomyErrorLabel}</span>
                <span style={sx('font-size:11px;color:#E9A9A4;line-height:1.4')}>{vm.autonomyError}</span>
              </div>
            ) : null}
            <div style={sx('display:flex;gap:5px')}>
              {vm.autonomyChips.map((au, i) => (
                <Interactive
                  key={i}
                  as="button"
                  onClick={au.pick}
                  title={au.title}
                  disabled={au.disabled}
                  aria-pressed={au.mode === vm.autonomyMode}
                  aria-busy={vm.autonomyBusy || undefined}
                  style={sx(`flex:1;min-height:32px;padding:5px 4px;border-radius:6px;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;line-height:1.25;letter-spacing:0.04em;white-space:normal;cursor:${au.cursor};opacity:${au.opacity};transition:border-color .15s,background-color .15s,opacity .15s;border:1px solid ${au.bd};background: ${au.bg};color: ${au.c}`)}
                  hoverStyle={au.disabled ? undefined : sx('border-color:#77809A')}
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
