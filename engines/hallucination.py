import re
from typing import Any
from storage.schemas import ClaimCheck, HallucinationResult

OPINION_CUES = (
    "i think", "in my opinion", "probably", "seems like",
    "recommend", "should consider", "best to", "better to",
)

ASSUMPTION_CUES = (
    "assuming", "if ", "suppose", "likely", "may be",
    "might be", "expected to", "usually",
)

CONTRADICTION_PAIRS = (
    ({"succeeded", "completed", "approved", "sent"}, {"failed", "blocked", "cancelled", "rejected"}),
    ({"active", "verified", "valid"}, {"expired", "suspended", "invalid", "unverified"}),
    ({"free", "zero fee", "no charge"}, {"charged", "fee applies", "deducted"}),
)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?।])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _extract_numbers(text: str) -> list[float]:
    cleaned = text.replace(",", "")
    matches = re.findall(r"-?\d+(?:\.\d+)?", cleaned)
    return [float(m) for m in matches]


def _classify_claim(sentence: str) -> str:
    lowered = sentence.lower()
    if any(cue in lowered for cue in OPINION_CUES):
        return "opinion"
    if any(cue in lowered for cue in ASSUMPTION_CUES):
        return "assumption"
    return "verifiable_fact"


def _key_tokens(key: str) -> set[str]:
    tokens = re.split(r"[_\W]+", key.lower())
    return {t for t in tokens if len(t) > 2}


class HallucinationDetector:
    def _find_internal_contradictions(self, sentences: list[str]) -> list[str]:
        contradictions: list[str] = []
        lowered_sentences = [s.lower() for s in sentences]

        # 1. Check opposing state keywords across sentences
        for pos_words, neg_words in CONTRADICTION_PAIRS:
            pos_hit = next((s for s in sentences if any(w in s.lower() for w in pos_words)), None)
            neg_hit = next((s for s in sentences if any(w in s.lower() for w in neg_words)), None)
            if pos_hit and neg_hit and pos_hit != neg_hit:
                contradictions.append(
                    f"Conflicting status claims: '{pos_hit}' vs '{neg_hit}'"
                )

        # 2. Check conflicting numbers on the same topic (e.g., two different balances)
        topic_numbers: dict[str, set[float]] = {}
        for s in lowered_sentences:
            nums = _extract_numbers(s)
            if not nums:
                continue
            for topic in ("balance", "limit", "amount", "fee"):
                if topic in s:
                    topic_numbers.setdefault(topic, set()).update(nums)

        for topic, values in topic_numbers.items():
            if len(values) > 1:
                sorted_vals = sorted(values)
                contradictions.append(
                    f"Conflicting figures for '{topic}': {sorted_vals}"
                )

        return contradictions

    def _verify_claim(
        self,
        sentence: str,
        category: str,
        trusted_evidence: dict[str, Any],
    ) -> ClaimCheck:
        if category in ("opinion", "assumption"):
            return ClaimCheck(
                claim=sentence,
                category=category,
                status="unverified",
                evidence_ref=None,
                risk_level="low",
            )

        lowered = sentence.lower()
        claim_numbers = _extract_numbers(lowered)

        for key, expected_val in trusted_evidence.items():
            tokens = _key_tokens(key)
            if not tokens or not tokens.issubset(set(re.split(r"\W+", lowered))):
                continue

            ref = f"{key}={expected_val}"

            # Numeric comparison against trusted evidence
            if isinstance(expected_val, (int, float)) and not isinstance(expected_val, bool):
                if claim_numbers:
                    if float(expected_val) in claim_numbers:
                        return ClaimCheck(
                            claim=sentence,
                            category="verifiable_fact",
                            status="confirmed",
                            evidence_ref=ref,
                            risk_level="low",
                        )
                    return ClaimCheck(
                        claim=sentence,
                        category="verifiable_fact",
                        status="contradicted",
                        evidence_ref=ref,
                        risk_level="high",
                    )

            # Text or boolean comparison against trusted evidence
            expected_str = str(expected_val).lower()
            if expected_str in lowered:
                return ClaimCheck(
                    claim=sentence,
                    category="verifiable_fact",
                    status="confirmed",
                    evidence_ref=ref,
                    risk_level="low",
                )
            return ClaimCheck(
                claim=sentence,
                category="verifiable_fact",
                status="contradicted",
                evidence_ref=ref,
                risk_level="high",
            )

        # Evidence unavailable -> mark unverified, never mark false
        return ClaimCheck(
            claim=sentence,
            category="verifiable_fact",
            status="unverified",
            evidence_ref=None,
            risk_level="medium",
        )

    def evaluate(
        self,
        ai_response: str,
        trusted_evidence: dict[str, Any] | None = None,
    ) -> HallucinationResult:
        evidence = trusted_evidence or {}
        sentences = _split_sentences(ai_response)
        contradictions = self._find_internal_contradictions(sentences)

        claims: list[ClaimCheck] = []
        for sentence in sentences:
            category = _classify_claim(sentence)
            checked = self._verify_claim(sentence, category, evidence)
            claims.append(checked)

        has_contradiction = bool(contradictions) or any(
            c.status == "contradicted" for c in claims
        )
        has_unverified_fact = any(
            c.category == "verifiable_fact" and c.status == "unverified"
            for c in claims
        )

        if has_contradiction:
            risk = "high"
        elif has_unverified_fact:
            risk = "medium"
        else:
            risk = "low"

        return HallucinationResult(
            hallucination_risk=risk,
            internal_contradictions=contradictions,
            claims=claims,
        )