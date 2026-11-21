"""Stage 1: decide which speaker labels are the same person.

Principles
  * Never merge on name similarity alone - merging is an evidence-based decision.
  * HARD VETO: two labels that both speak in the same scene are different people. Nothing overrides this
    except an explicit human decision.
  * A wrong merge (two people as one) is worse than a missed merge, so unclear cases stay SEPARATE
    and are queued for a human ("pending").
  * Human decisions live in alias_decisions.csv and can be limited to an episode range
    (e.g. "THE DUKE = the heir from episode 20"), because titles pass between people.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Optional

from .prompts import ALIAS_PROMPT, SYSTEM
from .rules import alias_rules
from .schema import Episode, Line
from .textmatch import norm, ratio

TITLE_WORDS = {"LADY", "LORD", "SIR", "MR", "MRS", "MS", "MISS", "MADAM", "THE", "DUKE", "DUCHESS", "DOWAGER", "PRINCE",
               "PRINCESS", "KING", "QUEEN", "YOUNG", "OLD", "ELDER", "DR", "DOCTOR", "CAPTAIN", "AUNT", "UNCLE", "MASTER",
               "MISTRESS", "FATHER", "MOTHER", "BROTHER", "SISTER", "O.S.", "V.O.", "A", "AN", "OF", "S", "FORMER", "NEW"}


def tokens(label: str) -> set[str]:
    return {t for t in re.split(r"[^A-Z0-9']+", label.upper().replace("'S", "")) if t}


def distinctive(label: str) -> set[str]:
    # "TOM'S MOTHER" is a different person FROM Tom: keep the possessive label whole
    if re.search(r"\w'S\s", label.upper()):
        return {re.sub(r"\s+", "_", label.upper().strip())}
    return {t for t in tokens(label) if t not in TITLE_WORDS and len(t) > 1}


@dataclass
class PairEvidence:
    a: str
    b: str
    co_scenes: int
    eps_a: list[int]
    eps_b: list[int]
    subset: bool
    address_hits: list[str] = field(default_factory=list)   # line ids
    action_hits: list[str] = field(default_factory=list)
    verdict: str = ""
    confidence: float = 0.0
    reason: str = ""
    status: str = ""       # merged_auto | merged_human | separate_coscene | separate | pending | separate_human
    ep_from: Optional[int] = None
    ep_to: Optional[int] = None


@dataclass
class AliasResult:
    characters: dict[str, list[str]]           # canonical name -> labels
    label_to_char: dict[str, str]
    scoped: list[tuple[str, str, int, int]]    # (label, canonical, ep_from, ep_to)
    pairs: list[PairEvidence]
    _name_index: dict = field(default_factory=dict)

    @classmethod
    def from_bible(cls, bible) -> "AliasResult":
        """Rebuild the name resolver from a saved bible (used by the MCP server and the app)."""
        chars = {r["name"]: json.loads(r["labels"]) for r in bible.q("SELECT * FROM characters")}
        l2c = {l: c for c, labs in chars.items() for l in labs}
        scoped = [tuple(x) for x in (bible.meta("alias_scoped", []) or [])]
        idx = {c: set().union(*(distinctive(l) for l in labs)) for c, labs in chars.items()}
        return cls(characters=chars, label_to_char=l2c, scoped=scoped, pairs=[], _name_index=idx)

    def resolve_label(self, label: str, episode: int) -> str:
        lab = label.upper().strip()
        for l, canon, a, b in self.scoped:
            if l == lab and a <= episode <= b:
                return canon
        return self.label_to_char.get(lab, lab.title())

    def resolve_name(self, name: str, episode: int) -> tuple[str, bool]:
        """Map a name written in text ("Nora", "Lady Nora") to a canonical character.
        Returns (name, resolved?) - unresolved names are kept as written (title-cased)."""
        n = (name or "").strip()
        if not n:
            return n, False
        up = re.sub(r"\s+", " ", n.upper())
        if up in self.label_to_char or any(up == s[0] for s in self.scoped):
            return self.resolve_label(up, episode), True
        if n in self.characters:
            return n, True
        toks = distinctive(up)
        if toks:
            hits = {c for c, ts in self._name_index.items() if toks <= ts}
            if len(hits) == 1:
                return hits.pop(), True
        return n.title() if n.isupper() else n, False


def _label_stats(episodes: list[Episode]):
    scenes = defaultdict(set)      # label -> {(ep, scene)}
    eps = defaultdict(set)
    counts = defaultdict(int)
    scene_lines = defaultdict(list)
    for e in episodes:
        for l in e.lines:
            scene_lines[(l.episode, l.scene)].append(l)
            if l.type == "dialogue" and l.speaker:
                scenes[l.speaker].add((l.episode, l.scene))
                eps[l.speaker].add(l.episode)
                counts[l.speaker] += 1
    return scenes, eps, counts, scene_lines


def _word_in(tok: str, text: str) -> bool:
    return re.search(r"\b" + re.escape(tok.lower()) + r"\b", text.lower()) is not None


def proper_name_tokens(episodes: list[Episode]) -> set[str]:
    """Tokens used as proper names in the script text: written capitalized mid-sentence
    much more often than in lower case ("Nora" yes, "woman"/"guard"/"priest" no)."""
    cap, low = defaultdict(int), defaultdict(int)
    for e in episodes:
        for l in e.lines:
            for m in re.finditer(r"(?<![.!?\"]\s)(?<!^)\b([A-Za-z][a-z]+)(?:'s)?\b", l.text):
                w = m.group(1)
                (cap if w[0].isupper() else low)[w.upper()] += 1
    return {w for w in cap if cap[w] >= 1 and cap[w] >= 3 * low.get(w, 0)}


def load_decisions(path: Optional[Path]) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("label_a") and r.get("label_b")]


def resolve_aliases(episodes: list[Episode], llm=None, decisions_path: Optional[Path] = None,
                    auto_conf: float = 0.85, log=print) -> AliasResult:
    scenes, eps, counts, scene_lines = _label_stats(episodes)
    labels = sorted(counts, key=lambda x: -counts[x])
    decisions = {(d["label_a"].upper().strip(), d["label_b"].upper().strip()): d for d in load_decisions(decisions_path)}

    def human(a, b):
        return decisions.get((a, b)) or decisions.get((b, a))

    names = proper_name_tokens(episodes)
    pairs: list[PairEvidence] = []
    for a, b in combinations(labels, 2):
        da, db = distinctive(a), distinctive(b)
        shares = bool(da & db)
        similar = ratio(a, b) >= 85
        h = human(a, b)
        if not (shares or similar or h):
            continue
        co = len(scenes[a] & scenes[b])
        ev = PairEvidence(a=a, b=b, co_scenes=co, eps_a=sorted(eps[a]), eps_b=sorted(eps[b]),
                          subset=bool(da and db and (da <= db or db <= da)))
        # evidence: in scenes where A speaks, do others address/mention B's distinctive name? (and vice versa)
        for x, y in ((a, b), (b, a)):
            toks = distinctive(y)
            if not toks:
                continue
            for sc in scenes[x]:
                for l in scene_lines[sc]:
                    if l.type == "dialogue" and l.speaker not in (x, y) and any(_word_in(t, l.text) for t in toks):
                        ev.address_hits.append(l.line_id)
                    elif l.type == "action" and any(_word_in(t, l.text) for t in toks):
                        ev.action_hits.append(l.line_id)
        ev.address_hits = sorted(set(ev.address_hits))[:30]
        ev.action_hits = sorted(set(ev.action_hits))[:30]

        if h:  # human decision wins (and can be scoped to an episode range)
            dec = h.get("decision", "").strip().lower()
            ev.verdict, ev.confidence, ev.reason = dec, 1.0, "human decision"
            ev.ep_from = int(h["ep_from"]) if h.get("ep_from") else None
            ev.ep_to = int(h["ep_to"]) if h.get("ep_to") else None
            ev.status = "merged_human" if dec == "same" else "separate_human"
        elif co > 0:
            ev.verdict, ev.confidence, ev.status = "different", 1.0, "separate_coscene"
            ev.reason = f"both speak in {co} shared scene(s) - cannot be one person"
        else:
            v = _alias_verdict(ev, episodes, scene_lines, llm)
            ev.verdict, ev.confidence, ev.reason = v.get("verdict", "unsure"), float(v.get("confidence", 0) or 0), v.get("reason", "")
            shared_names = (distinctive(a) & distinctive(b)) & names
            if ev.verdict == "same" and ev.confidence >= auto_conf and ev.subset and shared_names:
                ev.status = "merged_auto"
            elif ev.verdict == "same" and not shared_names:
                ev.status = "pending"
                ev.reason += " | shared word is a role, not a name - needs a human"
            elif ev.verdict == "different":
                ev.status = "separate"
            else:
                ev.status = "pending"   # stays separate until a human confirms
        pairs.append(ev)

    # union-find over merges, never joining two groups that share a scene
    parent = {l: l for l in labels}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    members = {l: {l} for l in labels}
    scoped: list[tuple[str, str, int, int]] = []
    for ev in sorted(pairs, key=lambda p: (p.status != "merged_human", -p.confidence)):
        if ev.status not in ("merged_auto", "merged_human"):
            continue
        if ev.ep_from is not None or ev.ep_to is not None:
            continue  # scoped merges handled below
        ra, rb = find(ev.a), find(ev.b)
        if ra == rb:
            continue
        if ev.status == "merged_auto" and any(scenes[x] & scenes[y] for x in members[ra] for y in members[rb]):
            ev.status, ev.reason = "pending", ev.reason + " | blocked: groups share a scene"
            continue
        parent[rb] = ra
        members[ra] |= members.pop(rb)

    groups: dict[str, list[str]] = defaultdict(list)
    for l in labels:
        groups[find(l)].append(l)
    characters: dict[str, list[str]] = {}
    label_to_char: dict[str, str] = {}
    mains = {root: max(labs, key=lambda x: (counts[x], -len(x))) for root, labs in groups.items()}
    short = defaultdict(list)
    for root, main in mains.items():
        short[_display(main)].append(root)
    for root, labs in groups.items():
        # two different people must never share a display name ("Lord Hale" vs "Lady Hale")
        canon = _display(mains[root]) if len(short[_display(mains[root])]) == 1 else mains[root].title()
        characters[canon] = sorted(labs, key=lambda x: -counts[x])
        for l in labs:
            label_to_char[l] = canon
    for ev in pairs:
        if ev.status == "merged_human" and (ev.ep_from is not None or ev.ep_to is not None):
            canon = label_to_char[ev.a]
            scoped.append((ev.b, canon, ev.ep_from or 0, ev.ep_to or 10**6))
    name_index = {c: set().union(*(distinctive(l) for l in labs)) for c, labs in characters.items()}
    res = AliasResult(characters=characters, label_to_char=label_to_char, scoped=scoped, pairs=pairs, _name_index=name_index)
    log(f"names: {len(labels)} labels -> {len(characters)} characters; "
        f"{sum(p.status=='merged_auto' for p in pairs)} auto-merged, {sum(p.status=='pending' for p in pairs)} pending, "
        f"{sum(p.status=='separate_coscene' for p in pairs)} vetoed (same scene)")
    return res


def _display(label: str) -> str:
    d = [t for t in re.split(r"\s+", label) if t.upper() not in TITLE_WORDS] or label.split()
    return " ".join(w.capitalize() if w.isupper() else w for w in d) if len(d) <= 3 else label.title()


def _alias_verdict(ev: PairEvidence, episodes, scene_lines, llm) -> dict:
    if llm is None or not llm.available_for("alias"):
        return alias_rules(ev.subset, len(ev.address_hits), len(ev.action_hits))
    by_id = {l.line_id: l for lines in scene_lines.values() for l in lines}
    ev_lines = [by_id[i].render() for i in (ev.address_hits[:10] + ev.action_hits[:8]) if i in by_id]
    toks = distinctive(ev.a) | distinctive(ev.b)
    logs = [f"Ep {e.episode}: {e.logline}" for e in episodes if e.logline and any(_word_in(t, e.logline) for t in toks)][:8]
    prompt = ALIAS_PROMPT.format(a=ev.a, b=ev.b, eps_a=_ranges(ev.eps_a), eps_b=_ranges(ev.eps_b),
                                 loglines="\n".join(logs) or "(none)", evidence="\n".join(ev_lines) or "(none)")
    v = llm.complete_json(prompt, stage="alias", system=SYSTEM) or {}
    # keep only cited ids that really exist
    v["evidence_line_ids"] = [i for i in v.get("evidence_line_ids", []) if i in by_id]
    if v.get("verdict") == "same" and not v["evidence_line_ids"]:
        v["verdict"], v["reason"] = "unsure", (v.get("reason", "") + " | no valid evidence cited").strip(" |")
    return v


def _ranges(nums: list[int]) -> str:
    if not nums:
        return "-"
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n; continue
        out.append(f"{start}-{prev}" if start != prev else str(start)); start = prev = n
    out.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(out)


def write_decisions_template(res: AliasResult, path: Path) -> None:
    """Pending pairs for a human, in the same format alias_decisions.csv is read in."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["label_a", "label_b", "decision", "ep_from", "ep_to", "tool_verdict", "confidence", "co_scenes", "evidence_line_ids", "reason"])
        for p in res.pairs:
            if p.status == "pending":
                w.writerow([p.a, p.b, "", "", "", p.verdict, f"{p.confidence:.2f}", p.co_scenes,
                            " ".join((p.address_hits + p.action_hits)[:6]), p.reason])
