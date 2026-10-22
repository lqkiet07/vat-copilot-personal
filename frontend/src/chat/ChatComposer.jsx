import React, { useEffect, useRef } from 'react'
import { Calendar, Send, Square } from 'lucide-react'

const MAX_TEXTAREA_HEIGHT = 200

export function ChatComposer({
  input,
  onInputChange,
  asOfDate,
  onAsOfDateChange,
  onSend,
  onStop,
  streaming,
}) {
  const textareaRef = useRef(null)

  useEffect(() => {
    const node = textareaRef.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`
  }, [input])

  const handleKeyDown = (event) => {
    if (event.key !== 'Enter' || event.shiftKey) return
    event.preventDefault()
    if (!streaming) onSend()
  }

  return (
    <div className="border-t border-hairline bg-surface px-6 py-4 flex-shrink-0">
      <div className="max-w-[78ch] mx-auto">
        <div className="flex items-center gap-2 mb-2.5">
          <Calendar className="w-3.5 h-3.5 text-muted" strokeWidth={1.5} />
          <span className="text-[11px] text-muted font-medium">Mốc thời gian áp dụng</span>
          <input
            type="date"
            value={asOfDate}
            onChange={(e) => onAsOfDateChange(e.target.value)}
            className="px-2 py-1 rounded-md border border-hairline bg-surface text-[11px] font-mono font-medium text-ink focus:border-ink focus:outline-none"
          />
        </div>

        <div className="flex items-end gap-3">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Đặt câu hỏi về thuế GTGT..."
            className="flex-1 resize-none rounded-lg border border-hairline bg-surface px-3.5 py-3 text-[13.5px] leading-relaxed text-ink placeholder:text-muted focus:border-ink focus:outline-none focus:ring-0 max-h-[200px] overflow-y-auto"
          />
          {streaming ? (
            <button
              type="button"
              onClick={onStop}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-full text-xs font-medium text-ink bg-[#EFEAE2] hover:bg-[#E5DFD4] transition-colors cursor-pointer flex-shrink-0"
            >
              <Square className="w-3 h-3 fill-current" strokeWidth={1.5} />
              <span>Dừng</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={onSend}
              disabled={!input.trim()}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-full text-xs font-medium text-white bg-black hover:bg-zinc-800 disabled:bg-zinc-300 transition-all cursor-pointer flex-shrink-0"
            >
              <Send className="w-3.5 h-3.5" strokeWidth={1.5} />
              <span>Gửi</span>
            </button>
          )}
        </div>

        <p className="mt-2 text-[10.5px] text-muted">
          Enter để gửi · Shift + Enter để xuống dòng mới
        </p>
      </div>
    </div>
  )
}

export default ChatComposer
