import type React from "react";
import { BookOpen, History, MessageSquare, PanelLeft, Settings, SquarePen, Trash2, X } from "lucide-react";
import emblem from "@/imports/tai-xuong.png";

export type SidebarConversation = {
  id: string;
  title: string;
  updated: number;
};

type Props = {
  isDesktop: boolean;
  sidebarOpen: boolean;
  convs: SidebarConversation[];
  activeId: string;
  libraryCount: number;
  libraryOpen: boolean;
  onToggleSidebar: () => void;
  onCloseMobile: () => void;
  onNewChat: () => void;
  onSelectConv: (id: string) => void;
  onDeleteConv: (id: string, e: React.MouseEvent) => void;
  onOpenLibrary: () => void;
  onOpenSettings: () => void;
};

/** Left navigation rail and slide-over mobile drawer for library access, history, and settings. */
export function Sidebar({
  isDesktop,
  sidebarOpen,
  convs,
  activeId,
  libraryCount,
  libraryOpen,
  onToggleSidebar,
  onCloseMobile,
  onNewChat,
  onSelectConv,
  onDeleteConv,
  onOpenLibrary,
  onOpenSettings,
}: Props) {
  const expanded = isDesktop ? sidebarOpen : true;

  return (
    <>
      {!isDesktop && sidebarOpen && (
        <div
          aria-hidden="true"
          onClick={onCloseMobile}
          className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-[1px]"
        />
      )}

      <aside
        aria-label="Điều hướng và lịch sử tra cứu"
        aria-hidden={!isDesktop && !sidebarOpen ? true : undefined}
        className={
          isDesktop
            ? `relative z-30 flex h-full shrink-0 flex-col border-r border-[#E2E8F0] bg-[#F8FAFC] ${sidebarOpen ? "w-[280px]" : "w-[68px]"
            }`
            : `fixed inset-y-0 left-0 z-50 flex h-full w-[280px] max-w-[85vw] flex-col border-r border-[#E2E8F0] bg-[#F8FAFC] shadow-2xl transition-transform duration-200 ease-out ${sidebarOpen ? "translate-x-0" : "-translate-x-full pointer-events-none"
            }`
        }
      >
        <div className="flex h-14 items-center justify-between border-b border-[#E2E8F0]/80 px-3">
          <div className="flex min-w-0 items-center gap-2.5 overflow-hidden">
            {isDesktop ? (
              <button
                type="button"
                onClick={onToggleSidebar}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-[#475569] transition-colors hover:bg-[#E2E8F0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
                title={sidebarOpen ? "Thu gọn thanh bên" : "Mở rộng thanh bên"}
                aria-label={sidebarOpen ? "Thu gọn thanh bên" : "Mở rộng thanh bên"}
                aria-expanded={sidebarOpen}
              >
                <PanelLeft className="h-5 w-5" />
              </button>
            ) : null}

            {expanded && (
              <div className="flex min-w-0 items-center gap-2">
                <img
                  src={emblem}
                  alt="Biểu trưng Pháp luật Việt Nam"
                  className="h-7 w-auto shrink-0 object-contain drop-shadow-xs"
                />
                <div className="flex min-w-0 flex-col">
                  <span className="truncate text-[13.5px] font-bold text-[#1E3A8A]">
                    Pháp Lý Thuế GTGT
                  </span>
                  <span className="truncate text-[10px] text-[#64748B]">
                    Cổng tra cứu pháp luật công
                  </span>
                </div>
              </div>
            )}
          </div>

          {!isDesktop && (
            <button
              type="button"
              onClick={onCloseMobile}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-[#475569] transition-colors hover:bg-[#E2E8F0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
              title="Đóng thanh bên"
              aria-label="Đóng thanh bên"
            >
              <X className="h-5 w-5" />
            </button>
          )}
        </div>

        <div className="px-3 py-2.5">
          <button
            type="button"
            onClick={() => {
              onNewChat();
              if (!isDesktop) onCloseMobile();
            }}
            className={`group flex items-center rounded-full bg-[#1E3A8A] font-medium text-white shadow-xs transition-colors hover:bg-[#1E40AF] active:scale-98 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40 ${expanded ? "w-full gap-2.5 px-4 py-2.5 text-[13.5px]" : "h-11 w-11 justify-center p-0"
              }`}
            title="Tạo cuộc hội thoại mới"
            aria-label="Tạo cuộc hội thoại mới"
          >
            <SquarePen className="h-4 w-4 shrink-0" />
            {expanded && <span>Cuộc trò chuyện mới</span>}
          </button>
        </div>

        <div className="scroll-quiet flex-1 space-y-4 overflow-y-auto px-2.5 py-1 text-[13px]">
          <div className="space-y-0.5">
            <button
              type="button"
              onClick={() => {
                onOpenLibrary();
                if (!isDesktop) onCloseMobile();
              }}
              aria-expanded={libraryOpen}
              className={`flex w-full items-center rounded-xl py-2.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40 ${libraryOpen
                  ? "bg-[#EFF6FF] text-[#1E3A8A]"
                  : "text-[#334155] hover:bg-[#E2E8F0]"
                } ${expanded ? "gap-3 px-3" : "justify-center px-0"}`}
              title="Thư viện toàn bộ văn bản pháp luật GTGT"
              aria-label="Mở thư viện văn bản pháp luật GTGT"
            >
              <BookOpen className="h-4 w-4 shrink-0 text-[#1E3A8A]" />
              {expanded && (
                <span className="font-medium text-[#1E293B]">Thư viện văn bản</span>
              )}
            </button>
          </div>

          {expanded && (
            <div className="border-t border-[#E2E8F0]/70 pt-2">
              <div className="flex items-center gap-1.5 px-2.5 pb-1.5 text-[11px] font-bold uppercase tracking-wider text-[#64748B]">
                <History className="h-3.5 w-3.5 text-[#1E3A8A]" />
                <span>Lịch sử tra cứu</span>
              </div>
              {convs.length === 0 ? (
                <p className="px-2.5 py-2 text-[12px] text-[#64748B]">
                  Chưa có cuộc hội thoại nào được lưu.
                </p>
              ) : (
                <div className="space-y-0.5">
                  {convs.map((c) => {
                    const isActive = c.id === activeId;
                    return (
                      <div
                        key={c.id}
                        className={`group relative flex items-center justify-between gap-1 rounded-xl px-1.5 py-1 text-[12.5px] transition-colors ${isActive
                            ? "bg-[#E2E8F0] font-semibold text-[#1E293B]"
                            : "text-[#475569] hover:bg-[#F1F5F9]"
                          }`}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            onSelectConv(c.id);
                            if (!isDesktop) onCloseMobile();
                          }}
                          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 py-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
                          title={c.title}
                        >
                          <MessageSquare
                            className={`h-3.5 w-3.5 shrink-0 ${isActive ? "text-[#1E3A8A]" : "text-[#94A3B8]"}`}
                          />
                          <span className="truncate">{c.title}</span>
                        </button>
                        <button
                          type="button"
                          onClick={(e) => onDeleteConv(c.id, e)}
                          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[#94A3B8] opacity-100 transition-opacity hover:bg-black/10 hover:text-red-600 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/40 lg:h-6 lg:w-6 lg:opacity-0 lg:group-hover:opacity-100 lg:group-focus-within:opacity-100"
                          title="Xóa hội thoại"
                          aria-label="Xóa hội thoại"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="border-t border-[#E2E8F0] bg-[#F1F5F9]/60 p-2">
          <button
            type="button"
            onClick={onOpenSettings}
            className={`flex w-full items-center rounded-xl py-2.5 text-[#475569] transition-colors hover:bg-[#E2E8F0] hover:text-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40 ${expanded ? "gap-2.5 px-3 text-[13px] font-medium" : "justify-center px-0"
              }`}
            title="Cài đặt (Chế độ tra cứu, Mốc ngày, Trợ lý AI)"
            aria-label="Mở cài đặt"
          >
            <Settings className="h-4 w-4 shrink-0 text-[#1E3A8A]" />
            {expanded && <span>Cài đặt</span>}
          </button>
        </div>
      </aside>
    </>
  );
}

