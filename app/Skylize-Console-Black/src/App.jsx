import React from 'react';
import { sx } from './lib/style.jsx';
import { useConsoleState } from './hooks/useConsoleState.js';
import Sidebar from './components/Sidebar.jsx';
import TopBar from './components/TopBar.jsx';
import Toast from './components/Toast.jsx';
import Dashboard from './screens/Dashboard.jsx';
import Command from './screens/Command.jsx';
import Starmap from './screens/Starmap.jsx';
import Workflows from './screens/Workflows.jsx';
import Approvals from './screens/Approvals.jsx';
import Analytics from './screens/Analytics.jsx';
import Models from './screens/Models.jsx';
import Knowledge from './screens/Knowledge.jsx';
import Integrations from './screens/Integrations.jsx';
import Developers from './screens/Developers.jsx';
import AuditLog from './screens/AuditLog.jsx';
import Security from './screens/Security.jsx';
import Team from './screens/Team.jsx';
import Billing from './screens/Billing.jsx';
import Settings from './screens/Settings.jsx';

const DEFAULT_PROPS = { accent: '#3D6BFF', motion: 'immersive' };

export default function App(props) {
  const vm = useConsoleState({ ...DEFAULT_PROPS, ...props });

  return (
    <div
      style={{
        position: 'relative', height: '100vh', display: 'flex', flexDirection: 'column',
        color: '#E9EBF2', fontFamily: "'Geist',system-ui,-apple-system,sans-serif", fontSize: 13,
        overflow: 'hidden',
        background: 'radial-gradient(1100px 480px at 75% -12%, color-mix(in oklab, var(--accent,#3D6BFF) 9%, transparent), transparent 62%),radial-gradient(900px 500px at -10% 110%, color-mix(in oklab, var(--accent,#3D6BFF) 5%, transparent), transparent 60%),#07080C',
        '--accent': vm.accentVar,
      }}
    >
      <TopBar vm={vm} />
      <div style={sx('display:flex;flex:1;min-height:0')}>
        <Sidebar vm={vm} />
        <main style={sx('flex:1;min-width:0;display:flex;flex-direction:column;min-height:0')}>
          {vm.isDash && <Dashboard vm={vm} />}
          {vm.isChat && <Command vm={vm} />}
          {vm.isStar && <Starmap vm={vm} />}
          {vm.isWf && <Workflows vm={vm} />}
          {vm.isAppr && <Approvals vm={vm} />}
          {vm.isAna && <Analytics vm={vm} />}
          {vm.isMod && <Models vm={vm} />}
          {vm.isKb && <Knowledge vm={vm} />}
          {vm.isInteg && <Integrations vm={vm} />}
          {vm.isDev && <Developers vm={vm} />}
          {vm.isLog && <AuditLog vm={vm} />}
          {vm.isSec && <Security vm={vm} />}
          {vm.isTeam && <Team vm={vm} />}
          {vm.isBill && <Billing vm={vm} />}
          {vm.isSet && <Settings vm={vm} />}
        </main>
      </div>
      <Toast vm={vm} />
    </div>
  );
}
