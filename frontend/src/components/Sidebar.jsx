import React from 'react'
import {
  Bell,
  Search,
  Plus,
  BarChart2,
  FolderLock,
  Bot,
} from 'lucide-react'
import { Legend, NavRow, Pill } from '../ui/primitives'

const PIPELINES = [
  { id: 'P3_FullSystem', name: 'Full System', code: 'P3' },
  { id: 'P2_TemporalGraphRAG', name: 'Temporal GraphRAG', code: 'P2' },
  { id: 'P1_FlatRAG', name: 'Flat RAG', code: 'P1' },
]

export function Sidebar({
  scenarios = [],
  selectedScenario = null,
  onSelectScenario = () => {},
  pipeline = 'P3_FullSystem',
  onSelectPipeline = () => {},
  useLlm = false,
  onToggleLlm = () => {},
  ollamaOnline = false,
  health = { status: 'unknown', neo4j_connected: false, version: '1.0.0' },
  onNewQuery = () => {},
}) {
  const isNeo4jUp = Boolean(health?.neo4j_connected)

  return (
    <aside className="w-[248px] flex-shrink-0 bg-saddle text-neutral-300 flex flex-col h-screen overflow-y-auto select-none border-r border-white/5 font-sans text-[13px] leading-snug">
      {/* 1. Brand header */}
      <div className="px-4 pt-4 pb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-serif text-xl tracking-tight text-white">Harvey</span>
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

      {/* 2. Primary action */}
      <div className="px-3">
        <button
          type="button"
            onClick={onNewQuery}
          className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-neutral-300 hover:text-white hover:bg-white/5 transition-colors"
        >
          <Plus className="w-4 h-4" strokeWidth={1.5} />
          <span>Tra cứu mới</span>
        </button>
      </div>

      {/* 3. Primary navigation rows */}
      <div className="px-3 pt-1 space-y-0.5">
        <NavRow
          icon={<FolderLock className="w-4 h-4" strokeWidth={1.5} />}
          onClick={() =>
            document
              .getElementById('pane-corpus')
              ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }
        >
          Kho văn bản
        </NavRow>
        <NavRow
          icon={<BarChart2 className="w-4 h-4" strokeWidth={1.5} />}
          active
          onClick={() =>
            document
              .getElementById('pane-analysis')
              ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }
        >
          Trung tâm thẩm định
        </NavRow>
      </div>

      <div className="mx-3 my-2 border-t border-white/10" />

      {/* 4. Recents: scenario runs */}
      <div className="px-3">
        <div className="px-2 mb-1.5 text-[11px] text-neutral-500">Kịch bản thử nghiệm</div>
        <div className="space-y-0.5">
          {scenarios.map((sc, idx) => {
            const isSelected = selectedScenario === idx
            return (
              <button
                key={`scenario-${idx}`}
                type="button"
                onClick={() => onSelectScenario(idx)}
                className={`w-full flex items-start gap-2.5 px-2.5 py-1.5 rounded-lg text-left transition-colors ${
                  isSelected
                    ? 'bg-white/10 text-white'
                    : 'text-neutral-400 hover:text-neutral-100 hover:bg-white/5'
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-[2px] mt-1.5 flex-shrink-0 ${
                    isSelected ? 'bg-white' : 'bg-neutral-600'
                  }`}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">
                    {(sc.name || '').replace(/^Kịch bản \d+\s*\([^)]*\):\s*/i, '')}
                  </span>
                  <span className="block text-[10px] text-neutral-500 font-mono mt-0.5">
                    {sc.as_of_date}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="mx-3 my-2 border-t border-white/10" />

      {/* 5. Spaces: inference architectures */}
      <div className="px-3">
        <div className="px-2 mb-1.5 text-[11px] text-neutral-500">Kiến trúc suy luận</div>
        <div className="space-y-0.5">
          {PIPELINES.map((p) => {
            const isPActive = pipeline === p.id
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => onSelectPipeline(p.id)}
                className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left transition-colors ${
                  isPActive
                    ? 'bg-white/10 text-white'
                    : 'text-neutral-400 hover:text-neutral-100 hover:bg-white/5'
                }`}
              >
                <span
                  className={`w-5 h-5 rounded text-[10px] font-mono font-bold flex items-center justify-center flex-shrink-0 border ${
                    isPActive
                      ? 'bg-white text-zinc-950 border-white'
                      : 'bg-zinc-800 text-neutral-400 border-zinc-700/60'
                  }`}
                >
                  {p.code}
                </span>
                <span className="truncate">{p.name}</span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="mx-3 my-2 border-t border-white/10" />

      {/* 6. Local model toggle */}
      <div className="px-3 pb-2">
        <div className="flex items-center justify-between px-2.5 py-1.5 rounded-lg">
          <div className="flex items-center gap-2.5">
            <Bot className="w-4 h-4 text-neutral-500" strokeWidth={1.5} />
            <span>Ollama local</span>
          </div>
          <label className="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => onToggleLlm(e.target.checked)}
              className="sr-only peer"
            />
            <span className="w-8 h-[18px] rounded-full bg-white/10 peer-focus-visible:ring-2 peer-focus-visible:ring-white/30 peer-checked:bg-white relative after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:h-[14px] after:w-[14px] after:rounded-full after:bg-white after:transition-transform after:duration-150 peer-checked:after:translate-x-[14px] peer-checked:after:bg-zinc-950" />
          </label>
        </div>
        <div className="px-2.5 mt-1">
          <Legend tone={ollamaOnline ? 'ok' : 'neutral'} textClassName="text-neutral-500">
            {ollamaOnline ? 'qwen2.5:3b Online' : 'Tất định an toàn'}
          </Legend>
        </div>
      </div>

      {/* 7. Footer */}
      <div className="mt-auto px-3 py-3 flex items-center justify-between gap-3">
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
