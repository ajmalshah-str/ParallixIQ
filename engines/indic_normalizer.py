"""
PARALLIX IQ — INDIC CASE SUFFIX NORMALIZER
Deterministic morphological helper that isolates common Malayalam, Manglish,
and Hindi/Hinglish case suffixes from named entities/recipients while keeping
the original text intact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple


@dataclass(frozen=True)
class NormalizedRecipient:
    original: str
    canonical: str
    suffix: str | None
    language: str
    has_suffix: bool


class SuffixRule(NamedTuple):
    pattern: str
    suffix_label: str
    language: str
    root_transform: str | None = None


# Order matters: longer suffixes checked before shorter ones
MANGLISH_SUFFIX_RULES: list[tuple[str, str]] = [
    # Allative / Locative: -ilekku, -lekku, -il
    (r"(?i)^([A-Za-z]+?)-?ilekku$", "-ilekku"),
    (r"(?i)^([A-Za-z]+?)-?lekku$", "-lekku"),
    (r"(?i)^([A-Za-z]+?)-?il$", "-il"),
    # Genitive: -kinte, -inte, -nte
    (r"(?i)^([A-Za-z]+?)kk?inte$", "-inte"),
    (r"(?i)^([A-Za-z]+?)-?nte$", "-nte"),
    # Dative: -kku, -ku
    (r"(?i)^([A-Za-z]+?)-?kku$", "-kku"),
    (r"(?i)^([A-Za-z]+?)-?ku$", "-ku"),
    # Dative: -inu, -nu, -in
    (r"(?i)^([A-Za-z]+?)-?inu$", "-inu"),
    (r"(?i)^([A-Za-z]+?an)u$", "-nu"),  # Chettanu -> Chettan + -nu
    (r"(?i)^([A-Za-z]+?)-?nu$", "-nu"),
    (r"(?i)^([A-Za-z]+?)-?in$", "-in"),
]

MALAYALAM_SCRIPT_RULES: list[tuple[str, str, str | None]] = [
    # Allative: -ിലേക്ക്, -ലേക്ക്
    (r"^(.+?)(?:-)?ലേക്ക്$", "-ലേക്ക്", None),
    (r"^(.+?)(?:-)?ിലേക്ക്$", "-ിലേക്ക്", None),
    # Genitive: -ിന്റെ, -ന്റെ
    (r"^(.+?)(?:-)?ിന്റെ$", "-ിന്റെ", None),
    (r"^(.+?)(?:-)?ന്റെ$", "-ന്റെ", None),
    # Dative: -ക്ക്, -യ്ക്ക്
    (r"^(.+?)(?:-)?യ്ക്ക്$", "-ക്ക്", "glide_ya"),
    (r"^(.+?)(?:-)?ക്ക്$", "-ക്ക്", None),
    # Dative: -ിന്, -ന്
    (r"^(.+?)(?:-)?ിന്$", "-ിന്", "chillu_l"),
    (r"^(.+?)(?:-)?ന്$", "-ന്", "chillu_n"),
    # Locative: -ിൽ, -ൽ
    (r"^(.+?)(?:-)?ിൽ$", "-ിൽ", None),
    (r"^(.+?)(?:-)?ൽ$", "-ൽ", None),
]

# Chillu letter substitutions when removing -ിന് / -ന് in Malayalam
# e.g., രാഹുൽ + ിന് -> രാഹുലിന് => base without ിന് is രാഹുല, canonical is രാഹുൽ
MALAYALAM_CHILLU_MAP = {
    "chillu_l": ("ല", "ൽ"),   # la -> chillu l
    "chillu_n": ("ന", "ൻ"),   # na -> chillu n
    "chillu_r": ("ര", "ർ"),   # ra -> chillu r
}

HINDI_DEVANAGARI_POSTPOSITIONS = [
    (r"^(.+?)\s*[-–—]?\s*को$", "को"),
    (r"^(.+?)\s*[-–—]?\s*के\s+लिए$", "के लिए"),
    (r"^(.+?)\s*[-–—]?\s*पर$", "पर"),
    (r"^(.+?)\s*[-–—]?\s*से$", "से"),
]

HINGLISH_POSTPOSITIONS = [
    (r"(?i)^([A-Za-z]+?)\s*[-–—]?\s*ko$", "ko"),
    (r"(?i)^([A-Za-z]+?)\s*[-–—]?\s*par$", "par"),
    (r"(?i)^([A-Za-z]+?)\s*[-–—]?\s*ke\s+liye$", "ke liye"),
]


class IndicNormalizer:
    """
    Lightweight deterministic normalizer for Indic recipient names.
    Extracts canonical entity while preserving the original input string.
    """

    @classmethod
    def normalize_recipient(cls, recipient: str | None) -> NormalizedRecipient:
        if not recipient or not recipient.strip():
            return NormalizedRecipient(
                original=recipient or "",
                canonical=recipient or "",
                suffix=None,
                language="unknown",
                has_suffix=False,
            )

        original = recipient.strip()

        # 1. Check Hindi Devanagari postpositions
        for pattern, suffix_label in HINDI_DEVANAGARI_POSTPOSITIONS:
            m = re.match(pattern, original)
            if m:
                canonical = m.group(1).strip()
                if canonical:
                    return NormalizedRecipient(
                        original=original,
                        canonical=canonical,
                        suffix=suffix_label,
                        language="hindi",
                        has_suffix=True,
                    )

        # 2. Check Malayalam script suffixes
        for pattern, suffix_label, transform in MALAYALAM_SCRIPT_RULES:
            m = re.match(pattern, original)
            if m:
                base = m.group(1).strip()
                canonical = base
                if transform and transform in MALAYALAM_CHILLU_MAP:
                    target_char, chillu_char = MALAYALAM_CHILLU_MAP[transform]
                    if base.endswith(target_char):
                        canonical = base[: -len(target_char)] + chillu_char
                elif transform == "glide_ya":
                    # e.g., അമ്മയ്ക്ക് -> അമ്മ
                    canonical = re.sub(r"[ാ്്യ]+$", "", base).strip()
                    if not canonical:
                        canonical = base
                if canonical:
                    return NormalizedRecipient(
                        original=original,
                        canonical=canonical,
                        suffix=suffix_label,
                        language="malayalam",
                        has_suffix=True,
                    )

        # 3. Check Hinglish postpositions (Latin script + ko/par)
        for pattern, suffix_label in HINGLISH_POSTPOSITIONS:
            m = re.match(pattern, original)
            if m:
                canonical = m.group(1).strip()
                if canonical:
                    return NormalizedRecipient(
                        original=original,
                        canonical=canonical,
                        suffix=suffix_label,
                        language="hinglish",
                        has_suffix=True,
                    )

        # 4. Check Manglish agglutinative suffixes (Latin script)
        for pattern, suffix_label in MANGLISH_SUFFIX_RULES:
            m = re.match(pattern, original)
            if m:
                base = m.group(1).strip()
                # If geminated consonant formed before suffix (e.g. Deepakkinte -> Deepak)
                if suffix_label == "-inte" and base.endswith("kk"):
                    base = base[:-1]
                if len(base) >= 2:
                    return NormalizedRecipient(
                        original=original,
                        canonical=base,
                        suffix=suffix_label,
                        language="manglish",
                        has_suffix=True,
                    )

        # Default: no suffix detected, return original as canonical
        return NormalizedRecipient(
            original=original,
            canonical=original,
            suffix=None,
            language="standard",
            has_suffix=False,
        )

    @classmethod
    def separate_suffix(cls, recipient: str | None) -> tuple[str, str | None]:
        """Convenience method returning (canonical_name, suffix)."""
        res = cls.normalize_recipient(recipient)
        return res.canonical, res.suffix


# Standalone function helpers
def normalize_indic_recipient(recipient: str | None) -> tuple[str, str | None]:
    """
    Separates Indic case suffixes (-inu, -nu, -ക്ക്, को, etc.) from recipient names.
    Returns (canonical_recipient, extracted_suffix).
    The original text is left completely intact.
    """
    return IndicNormalizer.separate_suffix(recipient)


def separate_indic_suffixes(recipient: str | None) -> NormalizedRecipient:
    """Returns a full NormalizedRecipient dataclass."""
    return IndicNormalizer.normalize_recipient(recipient)
