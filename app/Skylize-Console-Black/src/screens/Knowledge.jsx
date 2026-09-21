import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

// REAL per-source_path ingestion census only. `source_path` is an ORIGIN
// STRING whoever ingested a document supplied, NOT a configured, syncing
// data source -- there is no connector-type column, no coverage percentage,
// no SYNCED/INDEXING status and no recall latency, because none has a
// backend source. See useConsoleState.js's `kbRows`/`kbStats` for the full
// accounting of what this screen used to claim and cannot.
export default function Knowledge({ vm }) {
  return (
    <div data-screen-label="Knowledge" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Knowledge &amp; Memory</h1>
      </div>
      <div style={sx('display:grid;grid-template-columns:repeat(auto-fit,minmax(186px,1fr));gap:12px;margin-bottom:12px')}>
        {vm.kbStats.map((s, i) => (
          <div key={i} style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;padding:13px 15px')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.14em;color:#77809A;margin-bottom:8px")}>{s.label}</div>
            <div style={sx('display:flex;align-items:baseline;gap:6px')}><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:22px;font-weight:600;color: ${s.color};font-variant-numeric:tabular-nums`)}>{s.value}</span><span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A")}>{s.sub}</span></div>
          </div>
        ))}
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
        <div style={sx("display:grid;grid-template-columns:minmax(200px,2fr) 90px 90px minmax(140px,1.6fr) 110px;gap:0 12px;padding:9px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#77809A;background:rgba(255,255,255,0.015)")}>
          <span>SOURCE PATH</span><span>DOCS</span><span>CHUNKS</span><span>DEPARTMENTS</span><span style={sx('text-align:right')}>LAST INGESTED</span>
        </div>
        {vm.kbLoading ? (
          <div style={sx('padding:16px;font-size:12px;color:#77809A')}>Reading the knowledge index&hellip;</div>
        ) : vm.kbError ? (
          <div role="alert" style={sx('padding:16px;font-size:12px;color:#E9A9A4')}>{vm.kbError}</div>
        ) : vm.kbEmpty ? (
          <div style={sx('padding:16px;font-size:12px;color:#77809A')}>Nothing ingested yet.</div>
        ) : (
          vm.kbRows.map((k, i) => (
            <Interactive
              key={i}
              style={sx('display:grid;grid-template-columns:minmax(200px,2fr) 90px 90px minmax(140px,1.6fr) 110px;gap:0 12px;align-items:center;padding:9px 16px;border-bottom:1px solid #12151F;transition:background-color .12s')}
              hoverStyle={sx('background-color:rgba(255,255,255,0.02)')}
            >
              <span style={sx('font-size:12.5px;font-weight:500;color:#E9EBF2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{k.name}</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2;font-variant-numeric:tabular-nums")}>{k.docs}</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;color:#9AA1B2;font-variant-numeric:tabular-nums")}>{k.chunks}</span>
              <span style={sx("font-size:11px;color:#8B93A7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{k.departments}</span>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;color:#77809A;text-align:right")}>{k.lastIngested}</span>
            </Interactive>
          ))
        )}
      </div>
    </div>
  );
}
