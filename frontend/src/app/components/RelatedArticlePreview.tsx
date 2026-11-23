import type { Article, Citation } from "../data";
import {
  doesClauseMatchCitation,
  doesPointMatchCitation,
  getPreviewClausesForCitation,
  parseCitationTarget,
} from "../utils/citationMatch";

type Props = {
  article: Article;
  citation: Citation;
  openActionLabel: string;
};

/** Render exact cited clause/point preview inside the Related Citations drawer. */
export function RelatedArticlePreview({ article, citation, openActionLabel }: Props) {
  const preview = getPreviewClausesForCitation(article, citation);
  const target = parseCitationTarget(citation);

  return (
    <div className="mt-2.5 text-[12.5px] leading-relaxed text-[#334155]">
      <div className="font-serif-legal font-semibold text-[#0F172A]">
        Điều {article.no}: {article.title}
      </div>
      <div className="font-serif-legal mt-2 space-y-2">
        {preview.clauses.map((cl, idx) => {
          const isExactClause = preview.isExactClauseMatch && target.level === "clause";
          const isExactDescendant =
            preview.isExactClauseMatch &&
            target.level === "point" &&
            doesClauseMatchCitation(cl, article, citation);

          return (
            <div
              key={cl.id || `${article.id}-prev-${idx}-${cl.n}`}
              className={
                isExactClause
                  ? "rounded-xl border-l-4 border-amber-500 bg-amber-50/90 px-3 py-2 text-[#0F172A]"
                  : isExactDescendant
                    ? "rounded-xl border-l-2 border-amber-300 pl-2.5"
                    : undefined
              }
            >
              <p className="m-0">
                <strong>{cl.label || cl.n}.</strong> {cl.text}
              </p>
              {cl.points && cl.points.length > 0 && (
                <div className="mt-1.5 space-y-1 pl-3 text-[12px]">
                  {cl.points.map((pt, pIdx) => {
                    const ptMatch = doesPointMatchCitation(pt, cl, article, citation);
                    return (
                      <div
                        key={pt.id || `${cl.n}-pt-${pIdx}-${pt.l}`}
                        className={
                          ptMatch
                            ? "rounded-lg border-l-4 border-amber-500 bg-amber-100/85 px-2 py-1 font-medium text-[#0F172A]"
                            : undefined
                        }
                      >
                        <span className="font-semibold text-[#1E3A8A]">{pt.l}) </span>
                        <span>{pt.text}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {preview.omittedCount > 0 && (
        <p className="mt-2 text-[11px] text-[#64748B]">
          Còn {preview.omittedCount} khoản khác trong Điều {article.no} — bấm “{openActionLabel}” để
          xem toàn văn có tô sáng.
        </p>
      )}
    </div>
  );
}

