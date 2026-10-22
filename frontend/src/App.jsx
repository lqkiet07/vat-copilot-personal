import React, { useState } from 'react'

import Sidebar from './layout/Sidebar'
import DashboardSidebar from './views/DashboardSidebar'
import DashboardView from './views/DashboardView'
import { useDashboardState } from './views/useDashboardState'
import ChatSidebar from './chat/ChatSidebar'
import ChatView from './chat/ChatView'
import { ChatSessionsProvider } from './chat/ChatSessionsContext'

export default function App() {
  const [view, setView] = useState('chat')
  const dashboard = useDashboardState()

  return (
    <ChatSessionsProvider>
      <div className="flex h-screen bg-[#FBFBFB] font-sans text-zinc-900 antialiased overflow-hidden selection:bg-zinc-200">
        <Sidebar view={view} onSwitchView={setView} health={dashboard.health}>
          {view === 'chat' ? (
            <ChatSidebar />
          ) : (
            <DashboardSidebar
              scenarios={dashboard.scenarios}
              selectedScenario={dashboard.selectedScenario}
              onSelectScenario={dashboard.onSelectScenario}
              pipeline={dashboard.pipeline}
              onSelectPipeline={dashboard.onSelectPipeline}
              useLlm={dashboard.useLlm}
              onToggleLlm={dashboard.onToggleLlm}
              ollamaOnline={dashboard.ollamaOnline}
              health={dashboard.health}
              onNewQuery={dashboard.onNewQuery}
            />
          )}
        </Sidebar>

        {view === 'chat' ? (
          <ChatView />
        ) : (
          <DashboardView state={dashboard} />
        )}
      </div>
    </ChatSessionsProvider>
  )
}
