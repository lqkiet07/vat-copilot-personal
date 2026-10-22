import React from 'react'
import { Sparkles } from 'lucide-react'

const FALLBACK_QUESTIONS = [
  'Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản vào tháng 03/2026 áp dụng thuế suất thuế GTGT bao nhiêu?',
  'Doanh nghiệp xuất khẩu dịch vụ vào tháng 05/2024 áp dụng điều kiện theo Luật 48/2024 có được không?',
  'Quy định về hóa đơn chứng từ đối với dịch vụ xuất khẩu theo Điều 9 Nghị định 181/2025 áp dụng như thế nào vào tháng 08/2026?',
]

export function ChatEmptyState({ questions, onPick }) {
  const items = questions.length > 0 ? questions : FALLBACK_QUESTIONS

  return (
    <div className="flex flex-col items-center justify-center text-center py-10">
      <div className="w-10 h-10 rounded-full bg-neutral-100 flex items-center justify-center mb-4">
        <Sparkles className="w-5 h-5 text-neutral-400" strokeWidth={1.5} />
      </div>

      <h2 className="font-serif text-[26px] leading-tight text-ink">
        Hỏi về luật Thuế GTGT
      </h2>
      <p className="mt-2 text-[13px] text-muted max-w-md leading-relaxed">
        Trợ lý chỉ trả lời các câu hỏi về thuế GTGT và quy định pháp luật Việt Nam liên quan.
      </p>

      <div className="mt-6 w-full max-w-2xl space-y-2 text-left">
        {items.map((question, index) => (
          <button
            key={`sample-${index}`}
            type="button"
            onClick={() => onPick(question)}
            className="w-full flex items-start gap-3 px-4 py-3 rounded-lg border border-hairline bg-surface text-left text-[13px] text-body hover:border-neutral-300 hover:text-ink transition-colors cursor-pointer"
          >
            <span className="w-5 h-5 rounded-full bg-[#EFEAE2] text-ink font-mono text-[10px] font-bold flex items-center justify-center flex-shrink-0 mt-0.5">
              {index + 1}
            </span>
            <span className="leading-relaxed">{question}</span>
          </button>
        ))}
      </div>

      <p className="mt-6 text-[11px] text-muted max-w-md leading-relaxed">
        Kết quả là hỗ trợ tra cứu, không thay thế tư vấn pháp lý chính thức.
      </p>
    </div>
  )
}

export default ChatEmptyState
