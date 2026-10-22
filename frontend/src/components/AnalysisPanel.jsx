import React from 'react'
import { Card, CardHeader, Pill, Legend } from '../ui/primitives'

const RISK_CONFIG = {
  LOW: { label: 'An toàn áp dụng', tone: 'ok', bar: 'bg-emerald-500' },
  MEDIUM: { label: 'Cần đối chiếu', tone: 'warn', bar: 'bg-amber-500' },
  HIGH: { label: 'Rủi ro cao', tone: 'danger', bar: 'bg-orange-500' },
  CRITICAL: { label: 'Cảnh báo nghiêm trọng', tone: 'danger', bar: 'bg-rose-500' },
}

const IRAC_KEYS = ['issue', 'rule', 'application', 'conclusion']

export function AnalysisPanel({ result = {}, onCitationClick }) {
  const {
    risk_score: rawScore = 0,
    risk_level: riskLevel = 'LOW',
    warnings = [],
    irac_sections: sections = {},
    citations = [],
    llm_used: llmUsed = false,
  } = result || {}

  const risk = RISK_CONFIG[riskLevel] || RISK_CONFIG.LOW
  const riskTone = risk.tone
  const riskLabel = risk.label
  const barTone = risk.bar
  const riskScore = Math.max(0, Math.min(100, Number(rawScore) || 0))

  const safeWarnings = Array.isArray(warnings) ? warnings : []
  const safeSections = sections || {}
  const safeCitations = Array.isArray(citations) ? citations : []

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Chỉ số rủi ro pháp lý"
          caption="Kiểm toán thời hiệu và trích dẫn qua đồ thị Neo4j"
          action={<Legend tone={riskTone}>{riskLabel}</Legend>}
        />
        <div className="flex items-baseline gap-1">
          <span className="font-serif text-[30px] leading-none text-ink">{riskScore}</span>
          <span className="text-[11px] text-muted">/ 100</span>
        </div>
        <div className="mt-3 h-1 rounded-full bg-neutral-100">
          <div className={`h-1 rounded-full ${barTone}`} style={{ width: `${riskScore}%` }} />
        </div>
        {safeWarnings.length > 0 && (
          <ul className="mt-3 pt-3 border-t border-hairline text-[12px] text-body list-disc pl-4 space-y-1">
            {safeWarnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Lập luận pháp lý"
          caption="IRAC — Issue, Rule, Application, Conclusion"
          action={<Pill>{llmUsed ? 'Ollama qwen2.5:3b' : 'Tất định'}</Pill>}
        />
        {IRAC_KEYS.map((key, i) => (
          <div key={key} className="py-3 border-b border-hairline last:border-0">
            <div className="flex gap-3">
              <span className="w-3 text-[11px] font-mono text-muted">{'IRAC'[i]}</span>
              <div className="min-w-0">
                <p className="text-[13.5px] leading-[1.7] text-body whitespace-pre-line">
                  {safeSections[key] || '—'}
                </p>
                {key === 'rule' && safeCitations.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {safeCitations.map((c) => (
                      <Pill key={c} onClick={() => onCitationClick?.(c)}>
                        [{c}]
                      </Pill>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </Card>
    </div>
  )
}

export default AnalysisPanel
