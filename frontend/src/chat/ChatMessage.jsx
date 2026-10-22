import React, { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Copy, RefreshCw } from 'lucide-react'

import { MARKDOWN_COMPONENTS } from './markdownComponents'

function Caret() {
  return (
    <span className="inline-block w-[2px] h-[14px] ml-0.5 bg-ink align-bottom animate-pulse" />
  )
}

export function ChatMessage({ message, isLastAssistant, onCopy, onRegenerate }) {
  const isUser = message.role === 'user'

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] bg-[#EFEAE2] rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed text-ink whitespace-pre-wrap break-words">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="max-w-[78ch]">
        {message.streaming ? (
          <div className="text-[13.5px] leading-relaxed text-ink">
            {message.content}
            <Caret />
          </div>
        ) : (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
            {message.content}
          </ReactMarkdown>
        )}
        {message.stopped && (
          <p className="mt-2 text-[11px] text-muted italic">Đã dừng tạo câu trả lời.</p>
        )}
        {message.error && (
          <p className="mt-2 text-[11px] text-rose-600">Không thể hoàn tất câu trả lời.</p>
        )}
      </div>

      {isLastAssistant && !message.streaming && (
        <div className="flex items-center gap-3 pt-0.5">
          <button
            type="button"
            onClick={() => onCopy(message.content)}
            className="inline-flex items-center gap-1.5 text-[11px] text-muted hover:text-ink transition-colors cursor-pointer"
          >
            <Copy className="w-3 h-3" strokeWidth={1.5} />
            <span>Sao chép</span>
          </button>
          <button
            type="button"
            onClick={onRegenerate}
            className="inline-flex items-center gap-1.5 text-[11px] text-muted hover:text-ink transition-colors cursor-pointer"
          >
            <RefreshCw className="w-3 h-3" strokeWidth={1.5} />
            <span>Sinh lại</span>
          </button>
        </div>
      )}
    </div>
  )
}

export default ChatMessage
