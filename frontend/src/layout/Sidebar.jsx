import React from 'react'
import { Bell, Search, Bot, FileSearch } from 'lucide-react'
import { Legend, Pill } from '../ui/primitives'

const VIEWS = [
  { id: 'chat', label: 'VAT Copilot', icon: Bot },
  { id: 'dashboard', label: 'Trung tâm Thẩm định', icon: FileSearch },
]

export function Sidebar({
  view = 'chat',
  onSwitchView = () => {},
  health = { status: 'unknown', neo4j_connected: false, version: '1.0.0' },
  children = null,
}) {
  const isNeo4jUp = Boolean(health?.neo4j_connected)

  return (
    <aside className="w-[264px] flex-shrink-0 bg-saddle text-neutral-300 flex flex-col h-screen border-r border-white/5 font-sans text-[13px] leading-snug select-none">
      {/* 1. Brand header */}
      <div className="px-4 pt-4 pb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-serif text-xl tracking-tight text-white">VAT Copilot</span>
          <Pill active>VAT</Pill>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            title="Thông báo hệ thống"
            className="p-1.5 rounded-md text-neutral-400 hover:text-white hover:bg-white/10 transition-colors"
          >
            <Bell className="w-3.5 h-3.5" strokeWidth={1.5} />
          </button>
          <button
            type="button"
            title="Tìm kiếm nhanh"
            className="p-1.5 rounded-md text-neutral-400 hover:text-white hover:bg-white/10 transition-colors"
          >
            <Search className="w-3.5 h-3.5" strokeWidth={1.5} />
          </button>
        </div>
      </div>

      {/* 2. Product switcher */}
      <div className="px-3 pb-1 space-y-0.5">
        {VIEWS.map((v) => {
          const Icon = v.icon
          const isActive = view === v.id
          return (
            <button
              key={v.id}
              type="button"
              onClick={() => onSwitchView(v.id)}
              className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left transition-colors ${
                isActive
                  ? 'bg-white/10 text-white font-medium'
                  : 'text-neutral-400 hover:text-white hover:bg-white/5'
              }`}
            >
              <Icon className="w-4 h-4" strokeWidth={1.5} />
              <span>{v.label}</span>
            </button>
          )
        })}
      </div>

      <div className="mx-3 my-2 border-t border-white/10" />

      {/* 3. View specific content */}
      <div className="flex-1 min-h-0 overflow-y-auto">{children}</div>

      {/* 4. Footer */}
      <div className="mt-auto px-3 py-3 flex items-center justify-between gap-3 border-t border-white/10">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-7 h-7 rounded-md bg-white/10 border border-white/10 flex items-center justify-center font-serif text-white text-xs font-semibold flex-shrink-0">
            C
          </div>
          <div className="min-w-0">
            <p className="text-neutral-200 truncate leading-snug">K49 KTPM CLC</p>
            <p className="text-[10px] text-neutral-500 truncate leading-tight">Đại học Cần Thơ</p>
          </div>
        </div>
        <Legend tone={isNeo4jUp ? 'ok' : 'danger'} textClassName="text-neutral-500 flex-shrink-0">
          Neo4j 5.26
        </Legend>
      </div>
    </aside>
  )
}

export default Sidebar
