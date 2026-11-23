from dataclasses import dataclass


@dataclass(frozen=True)
class PromptMeasurement:
    utf8_bytes: int
    input_limit: int
    fits: bool
    estimated_tokens: int = 0
    method: str = "conservative_utf8_2bytes_per_token_not_tokenizer_exact"


def measure_prompt(prompt: str, context: int, output_reserve: int,
                   template_reserve: int = 512) -> PromptMeasurement:
    if not isinstance(prompt, str):
        raise ValueError("Prompt must be text")
    values = (context, output_reserve, template_reserve)
    if any(type(value) is not int or value < 0 for value in values) or context == 0:
        raise ValueError("Invalid prompt budget")
    limit = max(0, context - output_reserve - template_reserve)
    size = len(prompt.encode("utf-8"))
    # Vietnamese UTF-8 empirically averages >3 bytes/token; ceil(bytes/2) leaves a conservative margin.
    estimated_tokens = (size + 1) // 2
    return PromptMeasurement(size, limit, estimated_tokens <= limit, estimated_tokens)


@dataclass(frozen=True)
class EvidencePack:
    articles: tuple
    omitted_ids: tuple[str, ...]
    ready: bool
    reason: str


def pack_evidence(articles, requested_ids, context, render_generation,
                  render_assessment, draft_reserve=1024, max_extra: int = 10):
    unique = {article.id: article for article in articles}
    required = set(requested_ids)
    for identity in requested_ids:
        if identity not in unique:
            children = {cid for cid in unique if cid.startswith(identity + "_")}
            if not children:
                return EvidencePack((), tuple(unique), False, "requested_evidence_unavailable")
            required.remove(identity)
            required.update(children)
    selected = [a for cid, a in unique.items() if cid in required]
    candidates = [a for cid, a in unique.items() if cid not in required]

    def fits(items):
        chosen = {a.id for a in items}
        omitted = tuple(cid for cid in unique if cid not in chosen)
        generation = render_generation(items, omitted)
        assessment = render_assessment(items, omitted)
        return (measure_prompt(generation, context, 1024).fits
                and measure_prompt(assessment, context, 2048 + draft_reserve).fits)

    if not fits(selected):
        return EvidencePack((), tuple(unique), False, "context_budget_exceeded")
    added = 0
    misses = 0
    for article in candidates:
        if added >= max_extra or misses >= 3:
            break
        trial = selected + [article]
        if fits(trial):
            selected = trial
            added += 1
            misses = 0
        else:
            misses += 1
    chosen = {a.id for a in selected}
    omitted = tuple(cid for cid in unique if cid not in chosen)
    return EvidencePack(tuple(selected), omitted, bool(selected),
                        "packed" if selected else "context_budget_exceeded")
