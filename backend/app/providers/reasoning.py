"""Deterministic rule-based reasoning provider: query → structured intent.

It only *interprets* language against a vocabulary built from structured state; it
holds no world knowledge. An LLM-backed provider can implement the same interface
(spec §14: the LLM may help with language understanding, never be the source of
truth).
"""
import re
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Tuple

from backend.app.core.time import ensure_utc
from backend.app.domain.models import QueryIntent
from backend.app.domain.types import QueryKind
from backend.app.providers.base import ReasoningProvider, Vocabulary

_KIND_RULES: List[Tuple[QueryKind, re.Pattern]] = [
    (QueryKind.CONTINUE, re.compile(r"\b(continue|resume|carry on|what'?s next|what is next|next step)\b")),
    (QueryKind.WHAT_CHANGED, re.compile(r"\b(what (has |have )?changed|what'?s different|what is different|any changes)\b")),
    (QueryKind.WHY, re.compile(r"\b(why|what caused|cause of|because of what)\b")),
    (QueryKind.WHAT_HAPPENED, re.compile(r"\b(what happened|history of|what did .* do)\b")),
    (QueryKind.WHERE_IS, re.compile(r"\b(where|wheres|find|locate)\b")),
    (QueryKind.CONTENTS, re.compile(r"\bwhat'?s? (is )?(on|in|at)\b|\bwhat is (on|in|at)\b|\bwhat'?s (on|in|at)\b")),
]
_CLOCK = re.compile(r"\b(?:at|around|by)\s+(\d{1,2}):(\d{2})\b")
_SINCE_CLOCK = re.compile(r"\bsince\s+(\d{1,2}):(\d{2})\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?")


def normalise(text: str) -> str:
    text = text.lower().replace("'", "").replace("’", "").replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9: ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _find(form: str, text: str) -> List[Tuple[int, int]]:
    f = normalise(form)
    if not f:
        return []
    return [(m.start(), m.end()) for m in re.finditer(rf"(?<![a-z0-9]){re.escape(f)}(?![a-z0-9])", text)]


def _overlaps(span: Tuple[int, int], taken: List[Tuple[int, int]]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in taken)


class RuleBasedReasoningProvider(ReasoningProvider):
    name = "rule-based-v1"

    def interpret(self, query: str, vocabulary: Vocabulary, now: datetime) -> QueryIntent:
        text = normalise(query)
        kind = next((k for k, rx in _KIND_RULES if rx.search(text)), QueryKind.UNKNOWN)
        intent = QueryIntent(kind=kind, raw=query)
        self._times(query, text, now, intent)

        taken: List[Tuple[int, int]] = []
        # Specific mentions (ids, names) first, longest first, so "pump new" beats "pump".
        specific: List[Tuple[int, str, Tuple[int, int]]] = []
        for eid, forms in vocabulary.entities.items():
            for form in forms:
                for span in _find(form, text):
                    specific.append((span[1] - span[0], eid, span))
        found: List[str] = []
        for _, eid, span in sorted(specific, key=lambda x: -x[0]):
            if not _overlaps(span, taken):
                taken.append(span)
                if eid not in found:
                    found.append(eid)

        anchor_spans: List[Tuple[int, int]] = []
        for aid, forms in vocabulary.anchors.items():
            for form in forms:
                for span in _find(form, text):
                    if not _overlaps(span, taken):
                        intent.anchor_id = intent.anchor_id or aid
                        anchor_spans.append(span)
        taken += anchor_spans

        # Type mentions ("the bottle") resolve only if unambiguous.
        by_type: Dict[str, List[str]] = {}
        for eid, etype in vocabulary.entity_types.items():
            by_type.setdefault(etype, []).append(eid)
        for etype, ids in sorted(by_type.items()):
            for span in _find(etype, text):
                if _overlaps(span, taken) or any(e in found for e in ids):
                    continue
                taken.append(span)
                if len(ids) == 1:
                    found.append(ids[0])
                else:
                    intent.ambiguous[etype] = sorted(ids)
        intent.entity_ids = found

        attrs = [a for a in vocabulary.attributes if a != "location" and _find(a, text)]
        if attrs:
            intent.attribute = max(attrs, key=len)
        if kind == QueryKind.UNKNOWN and intent.attribute and (found or intent.ambiguous):
            intent.kind = QueryKind.ATTRIBUTE
        if intent.kind == QueryKind.CONTENTS and not intent.anchor_id and found:
            intent.anchor_id = found[0]

        for tid, forms in vocabulary.tasks.items():
            if any(_find(f, text) for f in forms):
                intent.task_id = tid
                break
        return intent

    @staticmethod
    def _times(raw: str, text: str, now: datetime, intent: QueryIntent) -> None:
        def clock(h: str, m: str) -> datetime:
            t = datetime.combine(now.date(), time(int(h), int(m)), tzinfo=timezone.utc)
            return t - timedelta(days=1) if t > now else t

        iso = _ISO.search(raw)
        if iso:
            intent.as_of = ensure_utc(datetime.fromisoformat(iso.group(0).replace("Z", "+00:00")))
        elif (m := _SINCE_CLOCK.search(text)) is not None:
            intent.since = clock(*m.groups())
        elif (m := _CLOCK.search(text)) is not None:
            intent.as_of = clock(*m.groups())
        midnight = datetime.combine(now.date(), time(0), tzinfo=timezone.utc)
        if re.search(r"\byesterday\b", text):
            intent.since, intent.until = midnight - timedelta(days=1), midnight
        elif re.search(r"\btoday\b", text):
            intent.since = intent.since or midnight
