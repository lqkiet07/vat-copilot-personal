import React from 'react'

const LEVEL_META = {
  LOW: { label: 'An toàn', text: 'text-emerald-700', bar: 'bg-emerald-500' },
  MEDIUM: { label: 'Cần đối chiếu', text: 'text-amber-700', bar: 'bg-amber-500' },
  HIGH: { label: 'Rủi ro cao', text: 'text-orange-700', bar: 'bg-orange-500' },
  CRITICAL: { label: 'Cảnh báo nghiêm trọng', text: 'text-rose-700', bar: 'bg-rose-500' },
}

const FALLBACK_LEVEL = LEVEL_META.MEDIUM

function clampScore(score) {
  const value = Number(score)
  if (!Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, Math.round(value)))
}

export function ChatRiskBanner({ score, level, warnings }) {
  const meta = LEVEL_META[String(level || '').toUpperCase()] || FALLBACK_LEVEL
  const value = clampScore(score)
  const items = Array.isArray(warnings) ? warnings.filter(Boolean) : []

  return (
    <div className="max-w-[78ch] border border-hairline rounded-lg px-3.5 py-3 bg-surface">
      <div className="flex items-center justify-between gap-4">
        <span className="text-[11px] uppercase tracking-[0.08em] text-muted">Rủi ro pháp lý</span>
        <span className="flex items-baseline gap-1">
          <span className={`font-serif text-[26px] leading-none ${meta.text}`}>{value}</span>
          <span className="text-[11px] text-muted">/100</span>
        </span>
      </div>

      <div className="mt-1.5 flex items-baseline gap-2">
        <span className={`text-[12.5px] font-semibold ${meta.text}`}>{meta.label}</span>
        <span className="font-mono text-[10.5px] uppercase text-muted">{level}</span>
      </div>

      <div className="mt-2 h-[2px] w-full rounded-full bg-[#EFEAE2] overflow-hidden">
        <div className={`h-full ${meta.bar}`} style={{ width: `${value}%` }} />
      </div>

      {items.length > 0 && (
        <ul className="mt-2.5 space-y-1">
          {items.map((warning, index) => (
            <li key={index} className="flex gap-1.5 text-[11.5px] leading-relaxed text-body">
              <span className="text-muted">•</span>
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default ChatRiskBanner
