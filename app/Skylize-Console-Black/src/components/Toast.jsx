import React from 'react';
import { sx } from '../lib/style.jsx';

export default function Toast({ vm }) {
  if (!vm.toastShow) return null;
  return (
    <div role="alert" style={sx('position:absolute;right:18px;bottom:16px;display:flex;align-items:center;gap:9px;padding:10px 14px;background:rgba(14,17,25,0.95);backdrop-filter:blur(14px);border:1px solid #2B3450;border-radius:8px;box-shadow:0 14px 40px rgba(0,0,0,0.5);animation:fadeSlide .18s ease-out;z-index:80')}>
      <span style={sx(`width:6px;height:6px;border-radius:50%;background: ${vm.toastDot};box-shadow:0 0 8px ${vm.toastDot};flex-shrink:0`)}></span>
      <span style={sx("font-family:'Geist Mono',ui-monospace,monospace;font-size:10.5px;letter-spacing:0.05em;color:#E9EBF2")}>{vm.toastText}</span>
    </div>
  );
}
