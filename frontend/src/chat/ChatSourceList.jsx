import React from 'react'

import { Pill } from '../ui/primitives'

export function formatArticleCode(code) {
  if (!code) return ''
  return code.replace(/^Dieu\b/i, 'Điều')
}

export function formatValidity(source) {
  const from = source?.valid_from || '—'
  const to = source?.valid_to && source.valid_to !== '9999-12-31' ? source.valid_to : 'Hiện hành'
  return `Hiệu lực: ${from} → ${to}`
}

export function ChatSourceList({ sources, onSourceClick }) {
  const list = Array.isArray(sources) ? sources : []
  if (list.length === 0) return null

  return (
    <div className="max-w-[78ch]">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          Căn cứ pháp lý
        </span>
        <Pill className="font-mono">{list.length}</Pill>
      </div>

      <div className="mt-1 border-t border-hairline">
        {list.map((source, index) => (
          <button
            key={source?.id || `src-${index}`}
            type="button"
            onClick={() => onSourceClick?.(source)}
            className="w-full text-left flex items-start gap-3 border-b border-hairline py-2 pl-1 pr-1 hover:bg-paper transition-colors cursor-pointer"
          >
            <span className="font-mono text-[11px] text-accent w-[70px] shrink-0 pt-px">
              {formatArticleCode(source?.code) || source?.id}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[12.5px] text-ink leading-snug">{source?.title}</span>
              <span className="block text-[11px] text-muted mt-0.5">{formatValidity(source)}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default ChatSourceList
