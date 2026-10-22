import React from 'react'
import { Plus, Trash2 } from 'lucide-react'

import { useChatSessions } from './ChatSessionsContext'

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString('vi-VN')
}

export function ChatSidebar() {
  const { sessions, activeId, selectSession, newSession, removeSession } = useChatSessions()

  return (
    <div className="px-3 pb-3">
      {/* Primary action */}
      <div className="mb-2">
        <button
          type="button"
          onClick={newSession}
          className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-neutral-300 hover:text-white hover:bg-white/5 transition-colors"
        >
          <Plus className="w-4 h-4" strokeWidth={1.5} />
          <span>Tra cứu mới</span>
        </button>
      </div>

      {/* Session history */}
      <div className="mx-3 my-2 border-t border-white/10" />

      <div className="px-3">
        <div className="px-2 mb-1.5 text-[11px] text-neutral-500">Lịch sử tra cứu</div>
        <div className="space-y-0.5">
          {sessions.map((session) => {
            const isActive = session.id === activeId
            return (
              <div
                key={session.id}
                className={`group flex items-start gap-2 px-2.5 py-1.5 rounded-lg transition-colors ${
                  isActive
                    ? 'bg-white/10 text-white'
                    : 'text-neutral-400 hover:text-neutral-100 hover:bg-white/5'
                }`}
              >
                <button
                  type="button"
                  onClick={() => selectSession(session.id)}
                  className="flex-1 min-w-0 text-left"
                >
                  <span className="block truncate">{session.title}</span>
                  <span className="block text-[10px] text-neutral-500 font-mono mt-0.5">
                    {formatDate(session.updated_at)}
                  </span>
                </button>
                <button
                  type="button"
                  title="Xóa cuộc tra cứu"
                  onClick={() => removeSession(session.id)}
                  className="p-1 rounded text-neutral-500 opacity-0 group-hover:opacity-100 hover:text-rose-400 focus:opacity-100 transition-colors flex-shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" strokeWidth={1.5} />
                </button>
              </div>
            )
          })}
        </div>
      </div>

      {/* Scope note */}
      <div className="mx-3 my-3 border-t border-white/10" />

      <div className="px-3">
        <p className="px-2 text-[11px] text-neutral-500 leading-relaxed">
          VAT Copilot chỉ tra cứu thuế GTGT và các quy định pháp luật liên quan.
        </p>
      </div>
    </div>
  )
}

export default ChatSidebar
