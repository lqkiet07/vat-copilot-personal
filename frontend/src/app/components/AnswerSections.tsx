import { useState } from "react";
import { AlertTriangle, CalendarDays, ChevronDown, ChevronUp, RotateCcw } from "lucide-react";
import emblem from "@/imports/tai-xuong.png";
import type { AuditItem, Citation, Result } from "../data";
import { fmtDate, formatTechnicalDiagnosticText, splitTechnicalDiagnosticLines } from "../utils/citationMatch";
import { MarkdownContent } from "./MarkdownContent";
import type { Pipeline } from "./SettingsDialog";

/** Render answer card top metadata strip with validity status badge and evaluation date. */
export function AnswerHeader({
  pipeline,
  date,
  answerStatus,
  isGreeting,
}: {
  pipeline: Pipeline;
  date: string;
  answerStatus?: Result["answer_status"];
  isGreeting?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#F1F5F9] pb-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-[#BFDBFE] bg-[#EFF6FF]">
          <img src={emblem} alt="Quốc huy" className="h-4 w-auto" />
        </div>
        <span className="text-[14px] font-bold text-[#1E3A8A]">Thẩm định Pháp lý Thuế GTGT</span>
        <span className="font-mono-num rounded-full border border-[#BFDBFE] bg-[#EFF6FF] px-2 py-0.5 text-[10.5px] font-semibold text-[#1E3A8A]">
          {pipeline}
        </span>
        {answerStatus && !isGreeting && (
          <span
            className={`rounded-full border px-2 py-0.5 text-[10.5px] font-semibold ${
              answerStatus === "ANSWERED"
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : answerStatus === "INSUFFICIENT_BASIS"
                  ? "border-amber-200 bg-amber-50 text-amber-800"
                  : "border-orange-200 bg-orange-50 text-orange-800"
            }`}
          >
            {answerStatus === "ANSWERED"
              ? "Đã trả lời"
              : answerStatus === "INSUFFICIENT_BASIS"
                ? "Chưa đủ căn cứ"
                : "Ngoài phạm vi"}
          </span>
        )}
      </div>

      <div className="flex items-center gap-1.5 rounded-full border border-[#E2E8F0] bg-[#F8FAFC] px-3 py-1 text-[11px] font-semibold text-[#475569]">
        <CalendarDays className="h-3 w-3 text-[#1E3A8A]" />
        <span>
          Thời điểm thẩm định: <strong className="font-mono-num">{fmtDate(date)}</strong>
        </span>
      </div>
    </div>
  );
}

/** Render main conclusion markdown, collapsible diagnostic lines, rule basis, and retry actions. */
export function AnswerConclusionBox({
  result,
  isErrorCard,
  onCitationClick,
  onRetry,
  onEditQuery,
}: {
  result: Result;
  isErrorCard: boolean;
  onCitationClick?: (citation: Citation) => void;
  onRetry: () => void;
  onEditQuery: () => void;
}) {
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const { primaryText, diagnosticLines } = splitTechnicalDiagnosticLines(result.conclusion);

  return (
    <div className="mt-3.5 rounded-2xl border border-[#E2E8F0] bg-[#F8FAFC] p-4">
      <div className="flex items-center gap-2 text-[12px] font-bold uppercase tracking-wider">
        {result.is_out_of_domain ? (
          <>
            <AlertTriangle className="h-4 w-4 text-[#D97706]" />
            <span className="text-[#D97706]">Lưu ý phạm vi nghiệp vụ</span>
          </>
        ) : result.is_greeting ? (
          <span className="text-[#1E3A8A]">Chào mừng &amp; Hướng dẫn tra cứu</span>
        ) : (
          <span className="text-[#1E3A8A]">Kết luận pháp lý</span>
        )}
      </div>

      <MarkdownContent
        text={primaryText}
        onCitationClick={onCitationClick}
        className="mt-2.5 text-[14.5px] font-medium text-[#0F172A]"
      />

      {diagnosticLines.length > 0 && (
        <div className="mt-2.5 border-t border-[#E2E8F0] pt-2">
          <button
            type="button"
            aria-expanded={showDiagnostics}
            onClick={() => setShowDiagnostics((v) => !v)}
            className="flex items-center gap-1.5 text-[12px] font-semibold text-[#475569] hover:text-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
          >
            {showDiagnostics ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            <span>
              {showDiagnostics
                ? "Thu gọn chi tiết chứng cứ kỹ thuật"
                : `Xem chi tiết chứng cứ kỹ thuật (${diagnosticLines.length} dòng)`}
            </span>
          </button>
          {showDiagnostics && (
            <ul className="font-mono-num mt-2 max-h-56 space-y-1.5 overflow-y-auto rounded-xl border border-[#E2E8F0] bg-white p-3 text-[11px] text-[#475569]">
              {diagnosticLines.map((line, idx) => (
                <li key={idx} className="break-words">{formatTechnicalDiagnosticText(line)}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!result.is_out_of_domain && !result.is_greeting && result.rule && (
        <div className="mt-2.5 border-t border-[#E2E8F0]/80 pt-2.5 text-[13px] leading-relaxed text-[#475569]">
          <div className="mb-1 text-[11.5px] font-bold uppercase tracking-wider text-[#64748B]">
            Căn cứ pháp lý viện dẫn:
          </div>
          <MarkdownContent
            text={result.rule.replace(/^Căn cứ(?:\s+pháp\s+lý\s+viện\s+dẫn)?:\s*/i, "")}
            onCitationClick={onCitationClick}
          />
        </div>
      )}

      {isErrorCard && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[#E2E8F0] pt-2.5">
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 rounded-full bg-[#1E3A8A] px-3.5 py-1.5 text-[12px] font-semibold text-white hover:bg-[#1E40AF] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            <span>Thử lại câu hỏi này</span>
          </button>
          <button
            type="button"
            onClick={onEditQuery}
            className="inline-flex items-center gap-1.5 rounded-full border border-[#CBD5E1] bg-white px-3.5 py-1.5 text-[12px] font-medium text-[#334155] hover:bg-[#F8FAFC] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
          >
            <span>Đưa lại vào ô nhập</span>
          </button>
        </div>
      )}
    </div>
  );
}

/** Render collapsible technical audit checklist when warnings are lengthy. */
export function AnswerAuditSection({ audit }: { audit: AuditItem[] }) {
  const [expanded, setExpanded] = useState(false);
  const shouldCollapse = audit.length > 2 || audit.some((item) => item.text.length > 150);

  if (!shouldCollapse) {
    return (
      <div className="mt-3.5 space-y-1.5">
        <div className="text-[11.5px] font-bold uppercase tracking-wider text-[#64748B]">
          Thông tin và lưu ý từ phản hồi:
        </div>
        <ul className="space-y-1.5 text-[13px]">
          {audit.map((a, i) => (
            <li key={i} className="flex items-start gap-2 text-[#334155]">
              <span
                className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                  a.tone === "ok" ? "bg-[#059669]" : "bg-[#EA580C]"
                }`}
              />
              <span>{formatTechnicalDiagnosticText(a.text)}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="mt-3.5 rounded-xl border border-[#E2E8F0] bg-[#F8FAFC] px-3 py-2">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-2 text-left text-[12px] font-semibold text-[#475569] hover:text-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
      >
        <span>Thông tin và lưu ý kiểm định kỹ thuật ({audit.length} mục)</span>
        {expanded ? <ChevronUp className="h-4 w-4 shrink-0" /> : <ChevronDown className="h-4 w-4 shrink-0" />}
      </button>
      {expanded && (
        <ul className="mt-2 max-h-60 space-y-1.5 overflow-y-auto border-t border-[#E2E8F0] pt-2 text-[12.5px]">
          {audit.map((a, i) => (
            <li key={i} className="flex items-start gap-2 text-[#334155]">
              <span
                className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                  a.tone === "ok" ? "bg-[#059669]" : "bg-[#EA580C]"
                }`}
              />
              <span className="break-words">{formatTechnicalDiagnosticText(a.text)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
