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
