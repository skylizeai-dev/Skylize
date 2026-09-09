import React from 'react';
import { sx } from './lib/style.jsx';
import { useMyDayState } from './hooks/useMyDayState.js';
import Sidebar from './components/Sidebar.jsx';
import TopBar from './components/TopBar.jsx';
import MorningBrief from './screens/MorningBrief.jsx';
import CoWork from './screens/CoWork.jsx';
import WhatCanDo from './screens/WhatCanDo.jsx';
import AgentMap from './screens/AgentMap.jsx';
import PastTasks from './screens/PastTasks.jsx';
import PausedDetail from './screens/PausedDetail.jsx';
import MobileGlance from './screens/MobileGlance.jsx';

function useQueryScenario() {
  if (typeof window === 'undefined') return 'typical';
  const p = new URLSearchParams(window.location.search).get('scenario');
  return p || 'typical';
}

const DEFAULT_PROPS = { accent: '#0047FF' };

export default function App() {
  const scenario = useQueryScenario();
  const vm = useMyDayState({ ...DEFAULT_PROPS, scenario });

  return (
    <div
      style={{
        position: 'relative', height: '100vh', display: 'flex', flexDirection: 'column',
        color: '#08090A', fontFamily: "'Geist',system-ui,-apple-system,sans-serif", fontSize: 13,
        overflow: 'hidden', background: '#F7F8F8',
        '--accent': vm.accentVar,
      }}
    >
      <TopBar vm={vm} />
      <div style={sx('position:relative;z-index:1;display:flex;flex:1;min-height:0')}>
        <Sidebar vm={vm} />
        <main style={sx('flex:1;min-width:0;display:flex;flex-direction:column;overflow:hidden')}>
          {vm.isBrief && <MorningBrief vm={vm} />}
          {vm.isChat && <CoWork vm={vm} />}
          {vm.isAuth && <WhatCanDo vm={vm} />}
          {vm.isMap && <AgentMap vm={vm} />}
          {vm.isPast && <PastTasks vm={vm} />}
          {vm.isDetail && <PausedDetail vm={vm} />}
          {vm.isMobile && <MobileGlance vm={vm} />}
        </main>
      </div>
    </div>
  );
}
