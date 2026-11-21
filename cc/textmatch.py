"""Fuzzy matching helpers. Uses rapidfuzz when installed, else a stdlib fallback
with the same 0-100 scale, so results are comparable either way."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

try:  # optional speed-up
    from rapidfuzz import fuzz as _rf
except Exception:  # pragma: no cover
    _rf = None

_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "–": "-", "…": "..."})


def norm(s: str) -> str:
    """Lower-case, unify quotes/dashes, drop punctuation, collapse spaces.
    Keeps letters of every script (works for Devanagari/Hinglish too)."""
    s = unicodedata.normalize("NFKC", s or "").translate(_QUOTES).lower()
    s = re.sub(r"[^\w\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def partial_ratio(needle: str, hay: str) -> float:
    """Best match score (0-100) of `needle` against any same-length window of `hay`."""
    a, b = norm(needle), norm(hay)
    if not a or not b:
        return 0.0
    if a in b:
        return 100.0
    if _rf is not None:
        return float(_rf.partial_ratio(a, b))
    if len(a) >= len(b):
        return SequenceMatcher(None, a, b).ratio() * 100
    best, best_i, n = 0.0, 0, len(a)
    step = max(1, n // 6)
    for i in range(0, len(b) - n + 1, step):
        r = SequenceMatcher(None, a, b[i:i + n]).ratio()
        if r > best:
            best, best_i = r, i
    # refine around the best coarse window with step 1
    for i in range(max(0, best_i - step), min(len(b) - n, best_i + step) + 1):
        r = SequenceMatcher(None, a, b[i:i + n]).ratio()
        if r > best:
            best = r
    return best * 100


def ratio(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if _rf is not None:
        return float(_rf.ratio(a, b))
    return SequenceMatcher(None, a, b).ratio() * 100
