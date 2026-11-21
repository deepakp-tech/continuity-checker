"""Rule-based baseline for every LLM stage.

Used when a stage's provider is "rules": offline runs, tests, and the 'no LLM' row of the
ablation table. It is deliberately simple (regex patterns), so it shows how much the LLM adds.
"""
from __future__ import annotations

import re
from typing import Optional

from .schema import Line

REL = r"(?:ex-fianc[eé]e?|sister|brother|husband|wife|mother|father|son|daughter|cousin|fianc[eé]e?|uncle|aunt|grandmother|grandfather|stepmother|stepfather|stepsister|stepbrother)"
NAME = r"(?P<{g}>[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)"
NOT_NAMES = {"The", "She", "He", "His", "Her", "They", "It", "This", "That", "My", "Your", "Our", "Their",
             "A", "An", "And", "But", "When", "Then", "Now", "If", "Why", "What", "Who", "Yes", "No", "Lady", "Lord", "Sir"}

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
             "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70}


def words_to_int(s: str) -> Optional[int]:
    s = s.lower().strip()
    if s.isdigit():
        return int(s)
    parts = re.split(r"[-\s]+", s)
    total = 0
    for p in parts:
        if p not in NUM_WORDS:
            return None
        total += NUM_WORDS[p]
    return total or None


def _n(g: str) -> str:
    return NAME.format(g=g)


PATTERNS = [
    # (regex, kind, applies_to) - kind decides how groups map to a fact
    (re.compile(_n("a") + r" is my (?:\w+ )?(?P<rel>" + REL + r")\b"), "rel_of_speaker", "dialogue"),
    (re.compile(r"\b[Mm]y (?:\w+ )?(?P<rel>" + REL + r"),? " + _n("a") + r"\b"), "rel_of_speaker", "dialogue"),
    (re.compile(r"\bI am " + _n("b") + r"'s (?:\w+ )?(?P<rel>" + REL + r")\b"), "speaker_rel_of", "dialogue"),
    (re.compile(_n("a") + r" is " + _n("b") + r"'s (?:\w+ )?(?P<rel>" + REL + r")\b"), "rel", "any"),
    (re.compile(_n("a") + r" (?:dies|is dead|has died|is killed|was killed|died|lies dead)\b"), "dead", "any"),
    (re.compile(_n("a") + r" (?:is|was) (?:badly |gravely |seriously )?(?:injured|wounded)\b"), "injured", "any"),
    (re.compile(_n("a") + r" (?:has recovered|recovers|is fully recovered)\b"), "recovered", "any"),
    (re.compile(_n("a") + r" is (?P<age>[\w-]+) years old\b"), "age", "any"),
    (re.compile(r"\bI am (?P<age>[\w-]+) years old\b"), "age_speaker", "dialogue"),
    (re.compile(_n("a") + r" works as an? (?P<job>[a-z]+(?: [a-z]+)?)"), "job", "any"),
    (re.compile(r"\bI work as an? (?P<job>[a-z]+(?: [a-z]+)?)"), "job_speaker", "dialogue"),
    (re.compile(_n("a") + r" (?:burns|destroys|smashes|tears up|shatters) (?:the|her|his|their) (?P<obj>[a-z' ]{2,30}?)(?=[.,;!]|\s(?:to|into|and|in|on|with|before|until)\b)"), "destroyed", "action"),
    (re.compile(_n("a") + r" (?:loses|has lost) (?:the|her|his|their) (?P<obj>[a-z' ]{2,30}?)(?=[.,;!]|\s(?:to|into|and|in|on|at|during|somewhere)\b)"), "lost", "action"),
    (re.compile(_n("a") + r" (?:holds|wears|clutches|opens|takes out|fastens) (?:the|her|his|their) (?P<obj>[a-z' ]{2,30}?)(?=[.,;!]|\s(?:to|into|and|in|around|tightly|up|close|high|against|over|out|with|toward|towards)\b)"), "has", "action"),
    (re.compile(_n("a") + r" (?:learns|discovers|finds out|realizes) that (?P<sec>[^.,;!]+)"), "learns", "any"),
]


def extract_rules(line: Line, speaker_name: str) -> list[dict]:
    """Return fact dicts (same shape as the LLM output) for one line."""
    if line.type not in ("action", "dialogue"):
        return []
    out = []
    src = "dialogue" if line.type == "dialogue" else "action"
    for rx, kind, applies in PATTERNS:
        if applies != "any" and applies != src:
            continue
        for m in rx.finditer(line.text):
            g = m.groupdict()
            a = g.get("a")
            if a and a.split()[0] in NOT_NAMES:        # "The Raja dies" -> "Raja"
                rest = a.split()[1:]
                if not rest or rest[0] in NOT_NAMES:
                    continue
                a = " ".join(rest)
            f = None
            if kind == "rel_of_speaker" and speaker_name:
                f = (a, "relationship", speaker_name, _rel(g["rel"]))
            elif kind == "speaker_rel_of" and speaker_name:
                f = (speaker_name, "relationship", g["b"], _rel(g["rel"]))
            elif kind == "rel":
                f = (a, "relationship", g["b"], _rel(g["rel"]))
            elif kind in ("dead", "injured", "recovered"):
                f = (a, "status", "life", kind)
            elif kind in ("age", "age_speaker"):
                subj = a if kind == "age" else speaker_name
                n = words_to_int(g["age"])
                if subj and n:
                    f = (subj, "identity", "age", str(n))
            elif kind in ("job", "job_speaker"):
                subj = a if kind == "job" else speaker_name
                if subj:
                    f = (subj, "identity", "job", g["job"].strip())
            elif kind in ("destroyed", "lost", "has"):
                obj = g["obj"].strip().split()[-1]
                f = (a, "possession", obj, kind)
            elif kind == "learns":
                f = (a, "knowledge", " ".join(g["sec"].split()[:6]).lower(), "learns")
            if f and f[0]:
                out.append({"subject": f[0], "type": f[1], "attribute": f[2], "value": f[3], "source": src,
                            "speaker": line.speaker, "line_id": line.line_id, "quote": m.group(0)})
    return out


def _rel(r: str) -> str:
    r = r.lower().replace("é", "e")
    if r.startswith("ex-fiance"):
        return "ex-fiance"
    return "fiance" if r.startswith("fiance") else r


# ----------------------------------------------------------------------------- alias verdict
def alias_rules(subset: bool, address_hits: int, action_hits: int) -> dict:
    if subset and (address_hits + action_hits) >= 1:
        return {"verdict": "same", "confidence": 0.9, "evidence_line_ids": [], "reason": "name contained in the other label and supported by address/action lines"}
    return {"verdict": "unsure", "confidence": 0.4, "evidence_line_ids": [], "reason": "not enough evidence for a rule-based decision"}


# ----------------------------------------------------------------------------- judge
def judge_rules(cand, profile: dict) -> dict:
    """Very small rule set; the LLM judge replaces this."""
    a, b = cand.fact_a, cand.fact_b
    allowed = " ".join(profile.get("allowed_devices", [])).lower()
    if cand.rule == "speaks_after_death":
        if "resurrection" in allowed or "rebirth" in allowed:
            return {"verdict": "unsure", "severity": "medium", "reason": "genre allows resurrection; needs a human check"}
        if a.get("source") == "dialogue":
            return {"verdict": "unsure", "severity": "medium", "reason": "the death is only claimed in dialogue"}
        return {"verdict": "contradiction", "severity": "high", "reason": "character speaks after an action line says they died"}
    if cand.rule == "learns_twice":
        return {"verdict": "contradiction", "severity": "medium", "reason": "the same secret is learned twice"}
    t = a.get("type")
    va, vb = a.get("value", ""), (b or {}).get("value", "")
    if t == "status":
        if va == "dead" and vb in ("alive", "recovered", "injured"):
            return {"verdict": "contradiction", "severity": "high", "reason": "status changes after death"}
        return {"verdict": "story_change", "severity": "low", "reason": "status changed over time"}
    if t == "possession":
        if va in ("destroyed", "lost") and vb == "has":
            return {"verdict": "contradiction", "severity": "medium", "reason": "object reappears after being destroyed or lost"}
        return {"verdict": "story_change", "severity": "low", "reason": "possession changed"}
    if t in ("relationship", "identity"):
        if "dialogue" in (a.get("source"), (b or {}).get("source")) and "disguise" in allowed and t == "identity":
            return {"verdict": "unsure", "severity": "medium", "reason": "identity claimed in dialogue; could be a disguise"}
        return {"verdict": "contradiction", "severity": "high" if t == "relationship" else "medium", "reason": f"{t} stated differently"}
    return {"verdict": "unsure", "severity": "low", "reason": "no rule for this fact type"}
