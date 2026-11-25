import type React from "react";
import { FileSearch, X } from "lucide-react";

type Props = {
  isDesktop: boolean;
  hasCitations: boolean;
  onClose: () => void;
  children: React.ReactNode;
};

/** Responsive drawer container and empty state for the Related Citations panel. */
export function RelatedDrawerFrame({ isDesktop, hasCitations, onClose, children }: Props) {
  return (
    <>
      {!isDesktop && (
        <div
          aria-hidden="true"
          onClick={onClose}
          className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-[1px]"
        />
      )}
      <aside
        aria-label="Văn bản liên quan đến câu trả lời"
        className={
          isDesktop
            ? "relative z-30 flex h-full w-[440px] shrink-0 flex-col border-l border-[#CBD5E1] bg-[#F8FAFC] shadow-2xl"
            : "fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-[440px] flex-col border-l border-[#CBD5E1] bg-[#F8FAFC] shadow-2xl"
        }
      >
        <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-[#CBD5E1] bg-white px-4">
          <div className="flex min-w-0 items-center gap-2">
            <FileSearch className="h-4 w-4 shrink-0 text-[#1E3A8A]" />
            <span className="truncate text-[14px] font-bold text-[#1E3A8A]">
              Văn Bản Liên Quan Đến Câu Trả Lời
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[#64748B] transition-colors hover:bg-[#F1F5F9] hover:text-[#0F172A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
            title="Đóng"
            aria-label="Đóng"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="scroll-quiet flex-1 space-y-3.5 overflow-y-auto p-4">
          {!hasCitations ? (
            <div className="rounded-2xl border border-dashed border-[#CBD5E1] p-6 text-center text-[#64748B]">
              <FileSearch className="mx-auto mb-2 h-8 w-8 text-[#94A3B8]" />
              <div className="text-[13px] font-semibold text-[#0F172A]">
                Chưa có trích dẫn pháp lý nào
              </div>
              <p className="mt-1 text-[12px]">
                Hãy gửi một câu hỏi pháp lý hoặc chọn một kịch bản mẫu để hệ thống trích xuất các
                điều khoản liên quan.
              </p>
            </div>
          ) : (
            children
          )}
        </div>
      </aside>
    </>
  );
}
