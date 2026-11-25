import { Settings } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./ui/dialog";

export type Pipeline = "P3" | "P2" | "P1";

export type SystemSettings = {
  pipeline: Pipeline;
  defaultDate: string;
  useLlm: boolean;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isStaging: boolean;
  settings: SystemSettings;
  onUpdateSettings: (next: SystemSettings, syncedDate?: string, syncedPipeline?: Pipeline) => void;
};

/** Modal dialog for configuring pipeline architecture, evaluation date default, and LLM usage. */
export function SettingsDialog({ open, onOpenChange, isStaging, settings, onUpdateSettings }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md rounded-2xl bg-white p-6 shadow-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-[17px] font-bold text-[#1E3A8A]">
            <Settings className="h-5 w-5" />
            <span>Cài đặt hệ thống</span>
          </DialogTitle>
          <DialogDescription className="text-[12.5px] text-[#64748B]">
            Thiết lập chế độ tra cứu, mốc ngày mặc định và hỗ trợ diễn giải bằng AI
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-2 text-[13px]">
          <div>
            <label htmlFor="settings-pipeline" className="mb-1 block font-bold text-[#0F172A]">
              1. Chế độ tra cứu
            </label>
            <select
              id="settings-pipeline"
              value={isStaging ? "P3" : settings.pipeline}
              disabled={isStaging}
              onChange={(e) => {
                const p = e.target.value as Pipeline;
                onUpdateSettings({ ...settings, pipeline: p }, undefined, p);
              }}
              className="w-full rounded-xl border border-[#CBD5E1] bg-[#F8FAFC] p-2.5 text-[13px] font-medium focus-visible:border-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/25 disabled:opacity-60"
            >
              <option value="P3">Đầy đủ &amp; đối chiếu hiệu lực [Khuyên dùng]</option>
              <option value="P2" disabled={isStaging}>
                Tra cứu theo mốc thời gian{isStaging ? " (chưa bật)" : ""}
              </option>
              <option value="P1" disabled={isStaging}>
                Tìm nhanh văn bản (không kiểm tra sửa đổi){isStaging ? " (chưa bật)" : ""}
              </option>
            </select>
            <p className="mt-1 text-[11px] text-[#64748B]">
              {isStaging
                ? "Hệ thống đang chạy chế độ Đầy đủ & đối chiếu hiệu lực tự động."
                : "Chế độ đầy đủ tự động kiểm tra mốc chuyển tiếp 01/07/2025 giữa Luật 13/2008 và Luật 48/2024."}
            </p>
          </div>

          <div>
            <label htmlFor="settings-default-date" className="mb-1 block font-bold text-[#0F172A]">
              2. Mốc ngày tra cứu mặc định
            </label>
            <input
              id="settings-default-date"
              type="date"
              value={settings.defaultDate}
              onChange={(e) => {
                const nextDate = e.target.value;
                onUpdateSettings({ ...settings, defaultDate: nextDate }, nextDate, undefined);
              }}
              className="font-mono-num w-full rounded-xl border border-[#CBD5E1] bg-[#F8FAFC] p-2.5 text-[13px] font-medium focus-visible:border-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/25"
            />
            <p className="mt-1 text-[11px] text-[#64748B]">
              Mốc ngày mặc định áp dụng khi mở phiên hỏi đáp mới.
            </p>
          </div>

          <div className="flex items-center justify-between gap-3 rounded-xl border border-[#CBD5E1] bg-[#F8FAFC] p-3">
            <label htmlFor="settings-use-llm" className="cursor-pointer">
              <div className="text-[13px] font-bold text-[#0F172A]">3. Bật trợ lý AI diễn giải</div>
              <div className="text-[11.5px] text-[#64748B]">
                Tổng hợp câu trả lời dễ hiểu từ các điều khoản đã đối chiếu hiệu lực
              </div>
            </label>
            <input
              id="settings-use-llm"
              type="checkbox"
              checked={settings.useLlm}
              onChange={(e) => onUpdateSettings({ ...settings, useLlm: e.target.checked })}
              className="h-5 w-5 shrink-0 cursor-pointer rounded accent-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

