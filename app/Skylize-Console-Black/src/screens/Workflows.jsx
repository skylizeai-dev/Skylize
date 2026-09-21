import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

// REAL run history only, from workflow_runs (migration 0033). The workflow-
// definition library and the per-run stage-progress panel are GONE, not
// reshaped: GET /api/v1/workflows (the real definition list) has no BFF
// proxy today, and there is no per-stage progress anywhere on the live
// orchestrator path -- `failure_stage` is where a run STOPPED, never how far
// it got. See useConsoleState.js's `wfRuns` for the full accounting.
export default function Workflows({ vm }) {
  return (
    <div data-screen-label="Workflows" style={sx('flex:1;min-height:0;display:flex;flex-direction:column;gap:12px;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Workflows</h1>
      <div style={sx("font-size:11px;color:#77809A")}>{vm.wfDefinitionsUnavailable}</div>
      <div style={sx('flex:1;min-height:0;background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden;display:flex;flex-direction:column')}>
        <div style={sx("padding:10px 14px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A;flex-shrink:0")}>RUN HISTORY</div>
        {vm.wfLoading ? (
          <div style={sx('padding:16px;font-size:12px;color:#77809A')}>Reading workflow run history&hellip;</div>
        ) : vm.wfError ? (
          <div role="alert" style={sx('padding:16px;font-size:12px;color:#E9A9A4')}>{vm.wfError}</div>
        ) : vm.wfEmpty ? (
          <div style={sx('padding:16px;font-size:12px;color:#77809A')}>No workflow runs yet.</div>
        ) : (
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
        )}
      </div>
    </div>
  );
}
