import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { parseCitationId } from "../api";
import type { Citation } from "../data";
import { formatInlineCitationsToMarkdownLinks } from "../utils/citationMatch";

type Props = {
  text: string;
  className?: string;
  onCitationClick?: (citation: Citation) => void;
};

function safeUrlTransform(url: string): string {
  if (url.startsWith("#cite:") || /^(https?:|mailto:|#)/i.test(url)) {
    return url;
  }
  return "";
}

/** Render structured Markdown safely while converting recognized Neo4j citation tags into clickable pills. */
export function MarkdownContent({ text, className = "", onCitationClick }: Props) {
  const transformed = formatInlineCitationsToMarkdownLinks(text);

  return (
    <div className={`space-y-2 leading-relaxed ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrlTransform}
        components={{
          p: ({ children }) => <p className="m-0 leading-relaxed">{children}</p>,
          ul: ({ children }) => <ul className="my-1.5 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold text-[#0F172A]">{children}</strong>,
          code: ({ children }) => (
            <code className="font-mono-num rounded bg-[#E2E8F0]/70 px-1 py-0.5 text-[0.9em] text-[#1E3A8A]">
              {children}
            </code>
          ),
          a: ({ href, children }) => {
            if (href?.startsWith("#cite:")) {
              const rawId = decodeURIComponent(href.slice("#cite:".length));
              const citation = parseCitationId(rawId);
              return (
                <button
                  type="button"
                  data-inline-citation={citation.id}
                  onClick={() => onCitationClick?.(citation)}
                  title={`Bấm để xem & tô sáng trong Thư viện · Mã CSDL: ${citation.id}`}
                  className="mx-0.5 inline-flex items-center rounded-md border border-[#BFDBFE] bg-[#EFF6FF] px-1.5 py-0.5 align-baseline text-[12px] font-semibold text-[#1E3A8A] transition-colors hover:bg-[#DBEAFE] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
                >
                  {children}
                </button>
              );
            }
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-[#1E3A8A] underline hover:text-[#1E40AF]"
              >
                {children}
              </a>
            );
          },
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-[#93C5FD] pl-3 italic text-[#475569]">
              {children}
            </blockquote>
          ),
          table: ({ children }) => (
            <div className="my-2 overflow-x-auto rounded-lg border border-[#CBD5E1]">
              <table className="w-full border-collapse text-left text-[12px]">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border-b border-[#CBD5E1] bg-[#F1F5F9] px-2.5 py-1.5 font-semibold text-[#1E3A8A]">
              {children}
            </th>
          ),
          td: ({ children }) => <td className="border-b border-[#E2E8F0] px-2.5 py-1.5">{children}</td>,
        }}
      >
        {transformed}
      </ReactMarkdown>
    </div>
  );
}
