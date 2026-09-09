import React from 'react';
import { sx, Interactive } from '../lib/style.jsx';

export default function WhatCanDo({ vm }) {
  return (
    <div data-screen-label="What my agent can do" style={sx('flex:1;min-height:0;overflow-y:auto;animation:pageIn .3s cubic-bezier(0.2,0.8,0.2,1)')}>
      <div style={sx('max-width:720px;margin:0 auto;padding:34px 36px 90px')}>
        <div style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.18em;color:#8A8F96")}>YOUR AUTHORITY · MIRRORED</div>
        <h1 style={sx('margin:10px 0 0;font-size:24px;font-weight:600;letter-spacing:-0.02em;line-height:1.25')}>What your agent can do</h1>
        <div style={sx('margin-top:22px;border:1px solid color-mix(in srgb, var(--accent,#0047FF) 30%, #E4E6EA);border-radius:6px;background:color-mix(in srgb, var(--accent,#0047FF) 4%, #FFFFFF);padding:20px 22px')}>
          <div style={sx('font-size:16.5px;font-weight:600;letter-spacing:-0.015em;line-height:1.4')}>Your agent can never do anything you couldn't do yourself.</div>
          <div style={sx('font-size:12.5px;color:#55595E;line-height:1.65;margin-top:7px;max-width:560px')}>It borrows your authority while it works — your address book, your budget, your access. If your access changes, its access changes in the same minute. And it's checked before every single action, not audited after.</div>
          <div style={sx("display:flex;align-items:center;gap:6px;margin-top:12px;font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.12em;color:#0F7B3A")}>
            <span style={sx('width:5px;height:5px;border-radius:50%;background:#0F7B3A')}></span>ENFORCED IN CODE · CHECKED BEFORE EVERY ACTION
          </div>
        </div>
        <div style={sx('margin-top:26px;background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;padding:16px 18px')}>
          <div style={sx('display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap')}>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.14em;color:#8A8F96")}>SPENDING HEADROOM</span>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.06em;color:#A2A7AD")}>RESETS MONDAY</span>
          </div>
          <div style={sx('display:flex;align-items:baseline;gap:8px;margin-top:10px')}>
            <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:26px;font-weight:600;letter-spacing:-0.02em;color:#08090A;font-variant-numeric:tabular-nums")}>{vm.authLeft}</span>
            <span style={sx('font-size:12.5px;color:#6E7378')}>still free this week, of {vm.authCap}</span>
          </div>
          <div style={sx('margin-top:12px;height:6px;border-radius:3px;background:#ECEDF0;overflow:hidden')}><div style={sx(`height:100%;border-radius:3px;background:#0F7B3A;width: ${vm.authPct}`)}></div></div>
          <div style={sx('margin-top:8px;font-size:11.5px;color:#A2A7AD')}>Anything bigger simply pauses and asks you — nothing bounces, nothing fails.</div>
        </div>
        {vm.authGroups.map((ag, gi) => (
          <div key={gi} style={sx('margin-top:26px')}>
            <div style={sx('display:flex;align-items:center;gap:10px;margin-bottom:10px')}>
              <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:9.5px;letter-spacing:0.16em;color:#8A8F96;white-space:nowrap")}>{ag.label}</span>
              <span style={sx('flex:1;height:1px;background:#E4E6EA')}></span>
            </div>
            <div style={sx('background:#FFFFFF;border:1px solid #E4E6EA;border-radius:6px;overflow:hidden')}>
              {ag.rows.map((ar, ri) => (
                <div key={ri} style={sx('display:flex;align-items:flex-start;gap:11px;padding:12px 16px;border-bottom:1px solid #F0F2F4')}>
                  <span style={sx(`width:5px;height:5px;border-radius:50%;background: ${ar.dot};flex-shrink:0;margin-top:6px`)}></span>
                  <div style={sx('flex:1;min-width:0')}>
                    <div style={sx('font-size:13px;font-weight:500;color:#08090A;line-height:1.5')}>{ar.s}</div>
                    {ar.noteShow && <div style={sx('font-size:12px;color:#8A8F96;line-height:1.55;margin-top:2px')}>{ar.note}</div>}
                  </div>
                  <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:8.5px;letter-spacing:0.1em;color:#A2A7AD;flex-shrink:0;text-align:right;margin-top:2px")}>{ar.prov}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
        <div style={sx('margin-top:30px;border-top:1px solid #E4E6EA;padding-top:16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap')}>
          <div style={sx('flex:1;min-width:280px;font-size:12px;color:#8A8F96;line-height:1.6')}>This page is a mirror, not a set of controls. Your authority came with your role — Marketing Lead — the day you got it. To change what you and your agent can do, ask <span style={sx('color:#26292D;font-weight:500')}>your access owner</span>, who owns access for Marketing.</div>
          <Interactive as="button" onClick={vm.askOwner} style={sx("height:30px;padding:0 13px;border-radius:6px;border:1px solid #DADDE2;background:#FFFFFF;color:#55595E;font-family:'Geist Mono',ui-monospace,monospace;font-size:10px;letter-spacing:0.07em;white-space:nowrap;cursor:pointer;transition:border-color .15s,color .15s")} hoverStyle={sx('border-color:var(--accent,#0047FF);color:#08090A')}>ASK FOR A CHANGE →</Interactive>
        </div>
      </div>
    </div>
  );
}
