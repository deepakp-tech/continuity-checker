"""Stage 5: code rules that pick candidate conflicts from the story bible.

Rules keep LLM cost linear: facts are grouped by (character, fact type, attribute) and only
CHANGE POINTS are sent to the judge, never all pairs.
  value_change         same character/type/attribute, value changes (sister -> cousin, destroyed -> has)
  relationship_inverse A says B is her sister, B says A is his cousin
  speaks_after_death   an action line says X died, later X speaks
  learns_twice         the same secret is learned again in a later episode
Location and most time facts are skipped (they change legitimately all the time); past-event
times ("five years ago") are kept because they should stay consistent.
"""
from __future__ import annotations

from collections import defaultdict

from .schema import Candidate
from .textmatch import norm, ratio

REL_CLASS = {"sister": "sibling", "brother": "sibling", "sibling": "sibling", "stepsister": "stepsibling", "stepbrother": "stepsibling",
             "husband": "spouse", "wife": "spouse", "husband/wife": "spouse", "fiance": "engaged", "ex-fiance": "ex-engaged",
             "mother": "parent", "father": "parent", "parent": "parent", "stepmother": "stepparent", "stepfather": "stepparent",
             "son": "child", "daughter": "child", "child": "child", "stepson": "stepchild", "stepdaughter": "stepchild",
             "cousin": "cousin", "uncle": "uncle/aunt", "aunt": "uncle/aunt", "nephew": "nephew/niece", "niece": "nephew/niece",
             "grandmother": "grandparent", "grandfather": "grandparent", "grandson": "grandchild", "granddaughter": "grandchild"}
INVERSE = {"parent": "child", "child": "parent", "stepparent": "stepchild", "stepchild": "stepparent",
           "uncle/aunt": "nephew/niece", "nephew/niece": "uncle/aunt", "grandparent": "grandchild", "grandchild": "grandparent"}


def _cluster_key(store: dict, group: tuple, text: str, thr: float = 85) -> str:
    """Map near-identical attribute strings ('pendant', 'oval pendant') onto one key."""
    t = norm(text)
    for k in store.setdefault(group, []):
        if t == k or ratio(t, k) >= thr or (len(t) > 3 and (t in k or k in t)):
            return k
    store[group].append(t)
    return t


def find_candidates(bible) -> list[Candidate]:
    facts = bible.q("SELECT * FROM facts WHERE verified=1 ORDER BY episode, ord")
    cands: list[Candidate] = []
    seen: set = set()

    def add(c: Candidate):
        k = (c.rule, c.fact_a.get("fact_id"), (c.fact_b or {}).get("fact_id"), (c.line_b or {}).get("line_id"))
        if k not in seen:
            seen.add(k); cands.append(c)

    # 1) value changes within (subject, type, attribute)
    clusters: dict = {}
    groups: dict = defaultdict(list)
    for f in facts:
        t = f["type"]
        if t in ("location", "knowledge"):
            continue
        if t == "time" and not any(w in f["value"] for w in ("ago", "years", "year")):
            continue
        attr = f["attribute"] if t == "relationship" else _cluster_key(clusters, (f["subject"], t), f["attribute"])
        groups[(f["subject"], t, attr)].append(f)
    for (subj, t, attr), fs in groups.items():
        prev = None
        for f in fs:
            if prev is not None and prev["value"] != f["value"] and prev["line_id"] != f["line_id"]:
                add(Candidate("value_change", prev, f, note=f"{subj} / {t} / {attr}: '{prev['value']}' then '{f['value']}'"))
            prev = f

    # 2) relationship stated from both sides must agree
    rel = defaultdict(list)
    for f in facts:
        if f["type"] == "relationship":
            rel[(f["subject"], f["attribute"])].append(f)
    for (a, b), fs in rel.items():
        if (b, a) not in rel or a >= b:
            continue
        for fa in fs:
            ca = REL_CLASS.get(fa["value"])
            for fb in rel[(b, a)]:
                cb = REL_CLASS.get(fb["value"])
                if ca and cb and ca != INVERSE.get(cb, cb):
                    add(Candidate("relationship_inverse", fa, fb, note=f"{a} is {b}'s {fa['value']}, but {b} is {a}'s {fb['value']}"))
                    break

    # 3) a character speaks after an action line says they died
    deaths = {}
    for f in facts:
        if f["type"] == "status" and f["value"] == "dead" and f["subject"] not in deaths:
            deaths[f["subject"]] = f
    for subj, d in deaths.items():
        later = bible.q("SELECT * FROM lines WHERE type='dialogue' AND character=? AND (episode > ? OR (episode = ? AND ord > ?)) "
                        "ORDER BY episode, ord LIMIT 1", (subj, d["episode"], d["episode"], d["ord"]))
        if later:
            add(Candidate("speaks_after_death", d, None, line_b=later[0],
                          note=f"{subj} is said to die in ep {d['episode']} but speaks in ep {later[0]['episode']}"))

    # 4) the same secret learned twice (in different episodes)
    know = defaultdict(list)
    kclusters: dict = {}
    for f in facts:
        if f["type"] == "knowledge" and f["value"] == "learns":
            know[(f["subject"], _cluster_key(kclusters, (f["subject"],), f["attribute"], thr=75))].append(f)
    for (subj, sec), fs in know.items():
        eps = sorted({f["episode"] for f in fs})
        if len(eps) > 1:
            first = fs[0]
            second = next(f for f in fs if f["episode"] != first["episode"])
            add(Candidate("learns_twice", first, second, note=f"{subj} learns '{sec}' in ep {first['episode']} and again in ep {second['episode']}"))

    cands.sort(key=lambda c: (max(c.fact_a["episode"], (c.fact_b or c.line_b or {}).get("episode", 0)), c.rule))
    return cands
