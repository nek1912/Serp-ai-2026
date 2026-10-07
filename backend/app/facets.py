from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Facet:
    facet_id: str
    domain: str
    query: str
    state: str | None


_SCHEME_CODES = ["pmfby", "pm-kisan", "pmkisan", "farmer registry", "farmer id"]


def _slice_query(english_query: str, domain: str, state: str | None) -> str:
    words = english_query.split()
    keep: list[str] = []
    low = english_query.lower()
    for w in words:
        if w.lower() in low and (w.lower() in domain or len(keep) < 24):
            keep.append(w)
        if len(keep) >= 24:
            break
    base = " ".join(keep) if keep else english_query[:300]
    if state and state.lower() not in base.lower():
        base = f"{base} {state}"
    return base[:400]


def split_facets(english_query, hits, classification, complexity, state):
    uniq: list[str] = []
    for h in hits or []:
        if h not in uniq:
            uniq.append(h)
    if classification is not None and getattr(classification, "domain", None):
        d = classification.domain
        if d not in ("general", "out_of_scope") and d not in uniq:
            uniq.append(d)
    multi = len(uniq) >= 2 or (complexity in ("multi_condition", "comparison", "multi_hop") and len(uniq) >= 2)
    if not multi:
        dom = uniq[0] if uniq else (getattr(classification, "domain", "general") or "general")
        return [Facet(facet_id=dom, domain=dom, query=english_query, state=state)]
    out: list[Facet] = []
    for d in uniq[:3]:
        out.append(Facet(facet_id=d, domain=d, query=_slice_query(english_query, d, state), state=state))
    return out


def select_coverage(facet_chunks: dict[str, list]) -> tuple[list, dict]:
    merged: list = []
    coverage: dict = {}
    for fid, chunks in facet_chunks.items():
        good = [c for c in chunks if (c.dense_score or c.rerank_score or 0) >= 0.40]
        if good:
            status = "supported"
        elif chunks:
            status = "partial"
        else:
            status = "unsupported"
        coverage[fid] = {"status": status, "chunk_ids": [c.chunk_id for c in chunks[:3]]}
    # round-robin: 2 per supported facet first
    for fid, chunks in facet_chunks.items():
        merged.extend(chunks[:2])
    # fill to 12 by score order
    seen = {c.chunk_id for c in merged}
    rest: list = []
    for chunks in facet_chunks.values():
        for c in chunks[2:]:
            if c.chunk_id not in seen:
                rest.append(c)
    rest.sort(key=lambda c: -((c.dense_score or 0) + (c.rerank_score or 0)))
    merged = (merged + rest)[:12]
    return merged, coverage
