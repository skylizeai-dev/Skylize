import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

// FOUR COLUMNS ARE GONE.
//
// GET /api/v1/tenants/me/users returns {user_id, role} and nothing else
// (tenants.py:105-113). The design had MEMBER / EMAIL / ROLE / LAST ACTIVE /
// MFA and was filled with six invented employees at an invented company. Email,
// last-active and MFA have no source anywhere behind this route, so they are
// dropped rather than blanked -- an empty MFA cell on a security-adjacent
// screen still reads as "we checked, and there is none".
//
// THE ROLE PERMISSION MATRIX IS REAL NOW, via GET /api/v1/permissions/matrix
// -- mechanically derived from an `ast` scan of the actual
// Depends(require_role(...)) call sites in edge/routes/*.py
// (edge/permission_matrix.py), never hand-transcribed. Rows are the raw
// route-group names, the owner-approved design: no invented business-action
// vocabulary.
export default function Team({ vm }) {
  const COLS = 'display:grid;grid-template-columns:minmax(220px,2.4fr) 130px;gap:0 12px';
  return (
    <div data-screen-label="Team" style={sx('flex:1;min-height:0;overflow-y:auto;padding:16px 24px 18px;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('display:flex;align-items:baseline;justify-content:space-between;margin-bottom:13px')}>
        <h1 style={sx('margin:0;font-size:15px;font-weight:600;letter-spacing:-0.01em')}>Team &amp; Access</h1>
        {/* No invite endpoint exists. The button is labelled for what it is. */}
        <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#4A5162;border:1px solid #1B2130;border-radius:5px;padding:5px 9px")}>INVITE · NOT WIRED</span>
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden;margin-bottom:12px')}>
        <div style={sx(COLS + ";padding:9px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.12em;color:#77809A;background:rgba(255,255,255,0.015)")}>
          <span>USER ID</span><span>ROLE</span>
        </div>
        {vm.teamLoading && (
          <div style={sx("padding:30px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#77809A;letter-spacing:0.06em")}>Reading the member list…</div>
        )}
        {vm.teamError && (
          <div style={sx('padding:26px 20px;text-align:center')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#E15A52;margin-bottom:8px")}>MEMBER LIST UNAVAILABLE</div>
            <div style={sx('font-size:12.5px;color:#9AA1B2')}>{vm.teamError}</div>
          </div>
        )}
        {vm.teamEmpty && (
          <div style={sx("padding:30px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#616A82;letter-spacing:0.06em")}>No members returned for this org.</div>
        )}
        {vm.memberRows.map((mr, i) => (
          <Interactive
            key={i}
            style={sx(COLS + ';align-items:center;padding:9px 16px;border-bottom:1px solid #12151F;transition:background-color .12s')}
            hoverStyle={sx('background-color:rgba(255,255,255,0.02)')}
          >
            <span style={sx('display:flex;align-items:center;gap:10px;min-width:0')}>
              <span style={sx("display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:6px;border:1px solid #232939;background:rgba(255,255,255,0.03);font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;color:#C7CBD6;flex-shrink:0")}>{mr.initials}</span>
              {/* The user_id IS the identity the platform holds. Shown as such. */}
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:11.5px;color:#E9EBF2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis")}>{mr.name}</span>
            </span>
            <span><span style={sx(`font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;padding:2px 7px;border-radius:4px;color: ${mr.roleColor};background: ${mr.roleBg}`)}>{mr.role}</span></span>
          </Interactive>
        ))}
      </div>
      <div style={sx('background:#0C0F16;border:1px solid #1B2130;border-radius:10px;overflow:hidden')}>
        <div style={sx("padding:10px 16px;border-bottom:1px solid #161A26;font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.13em;color:#77809A")}>
          ROLE PERMISSIONS &middot; MECHANICALLY DERIVED FROM ROUTE SOURCE
        </div>
        {vm.permLoading ? (
          <div style={sx("padding:30px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#77809A;letter-spacing:0.06em")}>Reading the permission matrix&hellip;</div>
        ) : vm.permError ? (
          <div style={sx('padding:26px 20px;text-align:center')}>
            <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:#E15A52;margin-bottom:8px")}>MATRIX UNAVAILABLE</div>
            <div style={sx('font-size:12.5px;color:#9AA1B2')}>{vm.permError}</div>
          </div>
        ) : vm.permEmpty ? (
          <div style={sx("padding:30px;text-align:center;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;color:#616A82;letter-spacing:0.06em")}>No route groups returned.</div>
        ) : (
          <div style={sx('overflow-x:auto')}>
            <div style={sx("display:grid;grid-template-columns:minmax(140px,1.6fr) repeat(5,90px);gap:0 8px;padding:9px 16px;border-bottom:1px solid #1B2130;font-family:'Geist Mono',ui-monospace,monospace;font-size:9px;letter-spacing:0.1em;color:#77809A;background:rgba(255,255,255,0.015)")}>
              <span>ROUTE GROUP</span>
              {vm.permRows[0] && vm.permRows[0].cells.map((c, i) => (<span key={i} style={sx('text-align:center')}>{c.role.toUpperCase()}</span>))}
            </div>
            {vm.permRows.map((row, i) => (
              <div key={i} style={sx("display:grid;grid-template-columns:minmax(140px,1.6fr) repeat(5,90px);gap:0 8px;align-items:center;padding:8px 16px;border-bottom:1px solid #12151F")}>
                <span style={sx('font-size:12px;font-weight:500;color:#E9EBF2')}>{row.name}</span>
                {row.cells.map((c, j) => (
                  <span key={j} style={sx('text-align:center;font-family:\'Geist Mono\',ui-monospace,monospace;font-size:10px;color:#9AA1B2')}>
                    {c.read && c.write ? 'RW' : c.read ? 'R' : c.write ? 'W' : '—'}
                  </span>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
