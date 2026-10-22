import React, { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Copy, RefreshCw } from 'lucide-react'

import { createMarkdownComponents } from './markdownComponents'
import ChatRiskBanner from './ChatRiskBanner'
import ChatSourceList from './ChatSourceList'

function Caret() {
  return (
    <span className="inline-block w-[2px] h-[14px] ml-0.5 bg-ink align-bottom animate-pulse" />
  )
}

export function ChatMessage({
  message,
  isLastAssistant,
  onCopy,
  onRegenerate,
  onCitationClick,
  onSourceClick,
}) {
  const markdownComponents = useMemo(
    () => createMarkdownComponents(onCitationClick),
    [onCitationClick],
  )
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

  const hasRisk = message.risk_score !== undefined && message.risk_score !== null

  return (
    <div className="space-y-2">
      {message.in_scope === false && (
        <span className="inline-flex items-center w-fit bg-[#F4F2ED] text-body border border-hairline rounded-full px-2 py-0.5 text-[11px]">
          Ngoài phạm vi thuế GTGT
        </span>
      )}

      <div className="max-w-[78ch]">
        {message.streaming ? (
          <div className="text-[13.5px] leading-relaxed text-ink">
            {message.content}
            <Caret />
          </div>
        ) : (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
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

      {hasRisk && (
        <ChatRiskBanner
          score={message.risk_score}
          level={message.risk_level}
          warnings={message.warnings}
        />
      )}

      {message.sources?.length > 0 && (
        <ChatSourceList sources={message.sources} onSourceClick={onSourceClick} />
      )}

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
