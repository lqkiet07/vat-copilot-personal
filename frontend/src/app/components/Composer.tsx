import type React from "react";
import { ArrowUp, CalendarDays, ChevronDown, Square } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import type { Pipeline } from "./SettingsDialog";

export const PIPELINES = [
  {
    key: "P3",
    label: "P3: Đầy đủ (Graph + Rule + RAG)",
    badge: "Flash (P3)",
    desc: "Độ chính xác cao nhất cho quy định chuyển tiếp giáp ranh 01/07/2025",
  },
  {
    key: "P2",
    label: "P2: Rule Engine + Neo4j",
    badge: "Graph",
    desc: "Truy vấn quan hệ thời gian trực tiếp trên đồ thị tri thức",
  },
  {
    key: "P1",
    label: "P1: RAG cơ bản (Vector Search)",
    badge: "Vector",
    desc: "Chỉ tìm kiếm ngữ nghĩa phẳng, dễ dính bẫy niên đại luật cũ",
  },
] as const;

type Props = {
  variant: "welcome" | "active";
  taRef: React.RefObject<HTMLTextAreaElement | null>;
  query: string;
  date: string;
  pipeline: Pipeline;
  isStaging: boolean;
  loading: boolean;
  pipelineMenuOpen: boolean;
  onPipelineMenuChange: (open: boolean) => void;
  onQueryChange: (value: string) => void;
  onDateChange: (value: string) => void;
  onPipelineChange: (value: Pipeline) => void;
  onSend: () => void;
  onAbort: () => void;
};

/** Question input bar supporting welcome/active layouts, evaluation date selection, and cancellation. */
export function Composer({
  variant,
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
}: Props) {
  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  }

  if (variant === "welcome") {
    return (
      <div className="rounded-[28px] border border-[#CBD5E1] bg-[#F8FAFC]/90 p-3.5 pl-5 transition-colors hover:bg-[#F8FAFC] focus-within:border-[#93C5FD] focus-within:bg-white focus-within:shadow-[0_4px_24px_rgba(30,58,138,0.08)]">
        <textarea
          ref={taRef}
          value={query}
          rows={2}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={handleKeyDown}
          aria-label="Nhập câu hỏi pháp lý thuế GTGT"
          placeholder="Hỏi về thuế suất GTGT, dịch vụ phần mềm, hoàn thuế, xuất hóa đơn..."
          className="block w-full resize-none bg-transparent pt-1 text-[15px] leading-relaxed outline-none placeholder:text-[#94A3B8]"
        />

        <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 border-t border-[#E2E8F0] pt-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <label
              className="flex min-h-[36px] cursor-pointer items-center gap-1.5 rounded-full border border-[#CBD5E1] bg-white px-3 py-1 text-[11.5px] font-medium text-[#334155] shadow-2xs hover:bg-[#F8FAFC]"
              title="Mốc thời điểm diễn ra sự việc để đối chiếu luật có hiệu lực"
            >
              <CalendarDays className="h-3.5 w-3.5 text-[#1E3A8A]" />
              <span>Mốc:</span>
              <input
                type="date"
                value={date}
                onChange={(e) => onDateChange(e.target.value)}
                aria-label="Mốc thời gian thẩm định"
                className="font-mono-num cursor-pointer bg-transparent text-[11.5px] font-bold text-[#0F172A] focus-visible:outline-none"
              />
            </label>

            <Popover open={pipelineMenuOpen} onOpenChange={onPipelineMenuChange}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className="flex min-h-[36px] items-center gap-1.5 rounded-full border border-[#CBD5E1] bg-white px-3 py-1 text-[11.5px] font-semibold text-[#1E3A8A] shadow-2xs hover:bg-[#F8FAFC] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
                  title="Kiến trúc suy luận AI"
                  aria-label="Chọn kiến trúc suy luận AI"
                >
                  <span>
                    {pipeline}: {PIPELINES.find((p) => p.key === pipeline)?.badge}
                  </span>
                  <ChevronDown className="h-3.5 w-3.5 text-[#64748B]" />
                </button>
              </PopoverTrigger>
              <PopoverContent
                align="start"
                className="w-[280px] rounded-2xl border border-[#CBD5E1] bg-white p-1.5 shadow-xl"
              >
                <div className="px-2 py-1.5 text-[11px] font-bold uppercase tracking-wider text-[#64748B]">
                  Chọn Pipeline suy luận
                </div>
                {PIPELINES.map((p) => {
                  const disabled = isStaging && p.key !== "P3";
                  return (
                    <button
                      type="button"
                      key={p.key}
                      disabled={disabled}
                      onClick={() => {
                        if (!disabled) {
                          onPipelineChange(p.key);
                          onPipelineMenuChange(false);
                        }
                      }}
                      className={`flex w-full flex-col rounded-xl px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40 ${
                        disabled
                          ? "cursor-not-allowed text-[#94A3B8] opacity-45"
                          : pipeline === p.key
                            ? "bg-[#EFF6FF] text-[#1E3A8A]"
                            : "text-[#0F172A] hover:bg-[#F8FAFC]"
                      }`}
                    >
                      <span className="text-[12.5px] font-bold">
                        {p.label}
                        {disabled ? " (chỉ canonical)" : ""}
                      </span>
                      <span className="text-[11px] text-[#64748B]">{p.desc}</span>
                    </button>
                  );
                })}
              </PopoverContent>
            </Popover>
          </div>

          <div className="flex items-center gap-1.5">
            {loading ? (
              <button
                type="button"
                onClick={onAbort}
                className="flex h-10 items-center gap-1.5 rounded-full border border-red-200 bg-red-50 px-3 text-[12px] font-semibold text-red-700 transition-colors hover:bg-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/40"
                title="Hủy truy vấn đang chạy"
                aria-label="Hủy truy vấn đang chạy"
              >
                <Square className="h-3.5 w-3.5 fill-current" />
                <span>Dừng</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={onSend}
                disabled={!query.trim()}
                className="flex h-10 w-10 items-center justify-center rounded-full bg-[#1E3A8A] text-white shadow-2xs transition-colors hover:bg-[#1E40AF] active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40 disabled:bg-[#CBD5E1] disabled:text-[#94A3B8]"
                title="Gửi câu hỏi"
                aria-label="Gửi câu hỏi"
              >
                <ArrowUp className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="relative z-20 shrink-0 border-t border-[#E2E8F0] bg-white/95 px-3 py-2.5 backdrop-blur-md sm:px-4 sm:py-3">
      <div className="mx-auto w-full max-w-[780px]">
        <div className="flex flex-col gap-2 rounded-2xl border border-[#CBD5E1] bg-[#F8FAFC] px-3 py-2 shadow-xs transition-colors focus-within:border-[#1E3A8A] focus-within:bg-white focus-within:ring-2 focus-within:ring-[#1E3A8A]/15 sm:flex-row sm:items-center sm:rounded-full sm:pl-4 sm:pr-2 sm:py-1.5">
          <textarea
            ref={taRef}
            value={query}
            rows={1}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={handleKeyDown}
            aria-label="Nhập câu hỏi tiếp theo về thuế GTGT"
            placeholder="Nhập câu hỏi tiếp theo về thuế GTGT..."
            className="w-full flex-1 resize-none bg-transparent py-1 text-[14px] outline-none placeholder:text-[#94A3B8]"
          />

          <div className="flex flex-wrap items-center justify-between gap-1.5 border-t border-[#E2E8F0] pt-1.5 sm:justify-end sm:border-t-0 sm:pt-0">
            <div className="flex items-center gap-1.5">
              <label
                className="flex min-h-[34px] cursor-pointer items-center gap-1 rounded-full border border-[#CBD5E1] bg-white px-2.5 py-1 text-[11px] font-medium text-[#334155] shadow-2xs"
                title="Mốc thời điểm thẩm định"
              >
                <CalendarDays className="h-3 w-3 text-[#1E3A8A]" />
                <input
                  type="date"
                  value={date}
                  onChange={(e) => onDateChange(e.target.value)}
                  aria-label="Mốc thời gian thẩm định"
                  className="font-mono-num cursor-pointer bg-transparent text-[11px] font-semibold text-[#0F172A] focus-visible:outline-none"
                />
              </label>

              <select
                value={pipeline}
                onChange={(e) => onPipelineChange(e.target.value as Pipeline)}
                aria-label="Chọn pipeline suy luận"
                className="min-h-[34px] cursor-pointer rounded-full border border-[#CBD5E1] bg-white px-2.5 py-1 text-[11px] font-semibold text-[#1E3A8A] shadow-2xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
              >
                <option value="P3">P3 Full</option>
                <option value="P2" disabled={isStaging}>
                  P2 Graph{isStaging ? " (chỉ canonical)" : ""}
                </option>
                <option value="P1" disabled={isStaging}>
                  P1 Flat{isStaging ? " (chỉ canonical)" : ""}
                </option>
              </select>
            </div>

            {loading ? (
              <button
                type="button"
                onClick={onAbort}
                className="flex h-9 items-center gap-1.5 rounded-full border border-red-200 bg-red-50 px-3 text-[11.5px] font-semibold text-red-700 transition-colors hover:bg-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/40"
                title="Hủy truy vấn đang chạy"
                aria-label="Hủy truy vấn đang chạy"
              >
                <Square className="h-3 w-3 fill-current" />
                <span>Dừng</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={onSend}
                disabled={!query.trim()}
                className="flex h-9 w-9 items-center justify-center rounded-full bg-[#1E3A8A] text-white transition-colors hover:bg-[#1E40AF] active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40 disabled:bg-[#CBD5E1] disabled:text-[#94A3B8]"
                title="Gửi câu hỏi"
                aria-label="Gửi câu hỏi"
              >
                <ArrowUp className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

