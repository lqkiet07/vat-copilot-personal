import type React from "react";
import {
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  FileSearch,
  FileText,
  Loader2,
  PanelLeft,
} from "lucide-react";
import emblem from "@/imports/tai-xuong.png";
import { SCENARIOS } from "../data";
import { REASONING_STEPS } from "../hooks/useChatSession";
import { fmtDate } from "../utils/citationMatch";
import { Composer } from "./Composer";
import type { Pipeline } from "./SettingsDialog";

const SHORT_SCENARIO_MAP: Record<number, string> = {
  1: "Xuất khẩu phần mềm (0%)",
  2: "Bẫy niên đại 01/07/2025",
  3: "Sửa hóa đơn NĐ 144/2026",
};

/** Top portal header with mobile sidebar trigger, knowledge base badge, and related panel toggle. */
export function PortalHeader({
  showOpenSidebarBtn,
  isStaging,
  docCount,
  freezeDate,
  relatedOpen,
  citationsCount,
  onOpenSidebar,
  onToggleRelated,
}: {
  showOpenSidebarBtn: boolean;
  isStaging: boolean;
  docCount: number;
  freezeDate: string;
  relatedOpen: boolean;
  citationsCount: number;
  onOpenSidebar: () => void;
  onToggleRelated: () => void;
}) {
  return (
    <header className="relative z-20 flex h-14 shrink-0 items-center justify-between gap-2 border-b border-[#E2E8F0] bg-white/90 px-3 backdrop-blur-md sm:px-5">
      <div className="flex min-w-0 items-center gap-2 sm:gap-3">
        {showOpenSidebarBtn && (
          <button
            type="button"
            onClick={onOpenSidebar}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-[#475569] transition-colors hover:bg-[#F1F5F9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
            title="Mở thanh bên"
            aria-label="Mở thanh bên"
          >
            <PanelLeft className="h-5 w-5" />
          </button>
        )}

        <div className="flex min-w-0 items-center gap-2">
          <img src={emblem} alt="Quốc huy Việt Nam" className="h-6 w-auto shrink-0 object-contain" />
          <span className="truncate text-[14.5px] font-bold tracking-tight text-[#1E3A8A] sm:text-[16.5px]">
            Hệ thống Hỏi đáp Pháp lý Thuế GTGT
          </span>
        </div>

        <span className="font-mono-num hidden shrink-0 rounded-full border border-[#BFDBFE] bg-[#EFF6FF] px-2.5 py-0.5 text-[10.5px] font-semibold text-[#1E3A8A] md:inline-flex">
          {isStaging
            ? `Staging (${docCount} VB · ${fmtDate(freezeDate)})`
            : `Cơ sở tri thức: Đóng băng ${fmtDate(freezeDate)}`}
        </span>
      </div>

      <button
        type="button"
        onClick={onToggleRelated}
        aria-expanded={relatedOpen}
        aria-label="Mở danh sách văn bản liên quan"
        className={`flex min-h-[36px] shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40 ${
          relatedOpen
            ? "bg-[#1E3A8A] text-white shadow-xs"
            : citationsCount > 0
              ? "border border-[#BFDBFE] bg-[#EFF6FF] text-[#1E3A8A] hover:bg-[#DBEAFE]"
              : "border border-[#E2E8F0] bg-white text-[#64748B] hover:bg-[#F8FAFC]"
        }`}
        title="Mở danh sách văn bản và điều khoản pháp luật được trích dẫn"
      >
        <FileSearch className="h-3.5 w-3.5 shrink-0" />
        <span className="hidden sm:inline">Văn bản liên quan</span>
        {citationsCount > 0 && <span className="font-mono-num font-bold">({citationsCount})</span>}
      </button>
    </header>
  );
}

/** Freeze-date notice bar displayed when chosen evaluation date exceeds loaded corpus cutoff. */
export function FreezeWarningBanner({ freezeDate }: { freezeDate: string }) {
  return (
    <div className="relative z-20 flex items-center justify-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-[12px] text-amber-800">
      <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
      <span>
        Mốc thời gian vượt quá ngày đóng băng dữ liệu ({fmtDate(freezeDate)}). Hệ thống sẽ thẩm định
        theo quy định có hiệu lực tính đến {fmtDate(freezeDate)}.
      </span>
    </div>
  );
}

/** Initial portal welcome screen with primary composer and benchmark scenarios. */
export function WelcomeScreen({
  taRef,
  query,
  date,
  pipeline,
  isStaging,
  loading,
  pipelineMenuOpen,
  onPipelineMenuChange,
  onQueryChange,
  onDateChange,
  onPipelineChange,
  onSend,
  onAbort,
  onPickScenario,
}: {
  taRef: React.RefObject<HTMLTextAreaElement | null>;
  query: string;
  date: string;
  pipeline: Pipeline;
  isStaging: boolean;
  loading: boolean;
  pipelineMenuOpen: boolean;
  onPipelineMenuChange: (open: boolean) => void;
  onQueryChange: (q: string) => void;
  onDateChange: (d: string) => void;
  onPipelineChange: (p: Pipeline) => void;
  onSend: () => void;
  onAbort: () => void;
  onPickScenario: (query: string, date: string) => void;
}) {
  return (
    <div className="my-auto flex flex-col items-center justify-center text-center">
      <div className="mb-3.5 flex h-14 w-14 items-center justify-center rounded-2xl border border-[#BFDBFE] bg-[#EFF6FF] shadow-sm">
        <img src={emblem} alt="Biểu trưng Quốc gia" className="h-9 w-auto object-contain" />
      </div>

      <h1 className="text-[26px] font-medium tracking-tight text-[#0F172A] sm:text-[34px]">
        Hệ thống Tra cứu &amp; Thẩm định Thuế GTGT
      </h1>
      <p className="mt-2.5 max-w-lg text-[14px] leading-relaxed text-[#475569]">
        Nhập tình huống phát sinh để hệ thống đối chiếu qua đồ thị tri thức thời gian và thẩm định
        hiệu lực các quy định thuế GTGT.
      </p>

      <div className="mt-7 w-full max-w-[720px] text-left">
        <Composer
          variant="welcome"
          taRef={taRef}
          query={query}
          date={date}
          pipeline={pipeline}
          isStaging={isStaging}
          loading={loading}
          pipelineMenuOpen={pipelineMenuOpen}
          onPipelineMenuChange={onPipelineMenuChange}
          onQueryChange={onQueryChange}
          onDateChange={onDateChange}
          onPipelineChange={onPipelineChange}
          onSend={onSend}
          onAbort={onAbort}
        />

        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          {SCENARIOS.map((s) => (
            <button
              type="button"
              key={s.id}
              onClick={() => onPickScenario(s.query, s.date)}
              className="flex items-center gap-1.5 rounded-full border border-[#CBD5E1] bg-white px-3.5 py-1.5 text-[12px] font-medium text-[#334155] shadow-2xs transition-colors hover:border-[#1E3A8A] hover:bg-[#F8FAFC] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
            >
              <FileText className="h-3 w-3 text-[#1E3A8A]" />
              <span>{SHORT_SCENARIO_MAP[s.id] || s.label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/** User prompt chat bubble with event evaluation date and selected pipeline. */
export function UserPromptBubble({ q, date, pipeline }: { q: string; date: string; pipeline: Pipeline }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[88%] rounded-3xl rounded-br-md border border-[#CBD5E1] bg-[#F1F5F9] px-4 py-3 text-[#0F172A] shadow-2xs sm:max-w-[85%] sm:px-5 sm:py-3.5">
        <p className="text-[14.5px] leading-relaxed">{q}</p>
        <div className="mt-2 flex flex-wrap items-center justify-end gap-2 text-[11px] text-[#64748B]">
          <CalendarDays className="h-3 w-3 text-[#1E3A8A]" />
          <span>
            Mốc sự kiện: <strong className="font-mono-num">{fmtDate(date)}</strong>
          </span>
          <span>•</span>
          <span className="font-mono-num font-semibold text-[#1E3A8A]">{pipeline}</span>
        </div>
      </div>
    </div>
  );
}

/** Progress feedback card shown while querying the backend with cancel action and elapsed timer. */
export function ReasoningProgressCard({
  step,
  elapsedSec,
  onAbort,
}: {
  step: number;
  elapsedSec: number;
  onAbort: () => void;
}) {
  return (
    <div className="rounded-2xl border border-[#CBD5E1] bg-white p-4 shadow-xs">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-[13px] font-bold text-[#1E3A8A]">
        <div className="flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin text-[#1E3A8A]" />
          <span>
            Đang thẩm định Temporal GraphRAG{elapsedSec > 0 ? ` (${elapsedSec}s)` : "..."}
          </span>
        </div>
        <button
          type="button"
          onClick={onAbort}
          className="rounded-full border border-red-200 bg-red-50 px-2.5 py-0.5 text-[11px] font-semibold text-red-700 hover:bg-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/40"
        >
          Hủy
        </button>
      </div>
      <ul className="space-y-2 text-[12.5px]">
        {REASONING_STEPS.map((l, i) => (
          <li key={l} className={`flex items-center gap-2.5 ${i <= step ? "text-[#0F172A]" : "text-[#94A3B8]"}`}>
            {i < step ? (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-[#059669]" />
            ) : i === step ? (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[#1E3A8A]" />
            ) : (
              <span className="h-4 w-4 shrink-0 rounded-full border border-[#CBD5E1]" />
            )}
            <span>{l}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
