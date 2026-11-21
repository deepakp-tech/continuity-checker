"""Stages 2-3: extract facts per scene, then verify every fact.

Verification (the anti-hallucination layer):
  1. schema check (pydantic) - malformed facts are dropped
  2. line id must exist in this scene; quote must fuzzy-match that line (score >= threshold).
     If the id is wrong but the quote matches another line of the scene, the id is repaired.
  3. optional entailment check by a DIFFERENT model: does the quote actually state the fact?
Dropped facts are kept in the database with a reason, so the grounding rate can be measured.
"""
from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from pydantic import ValidationError

from .prompts import ENTAIL_PROMPT, EXTRACT_PROMPT, SYSTEM
from .rules import extract_rules, words_to_int
from .schema import Episode, Fact, Line
from .textmatch import norm, partial_ratio

REL_SYNONYMS = {"ex fiancee": "ex-fiance", "ex fiance": "ex-fiance", "former fiance": "ex-fiance", "fiancee": "fiance", "fiancé": "fiance", "fiancée": "fiance", "betrothed": "fiance", "spouse": "husband/wife",
                "hubby": "husband", "mom": "mother", "mum": "mother", "dad": "father", "sis": "sister", "bro": "brother"}


def scene_chunks(ep: Episode, max_chars: int) -> list[list[Line]]:
    """Non-camera lines grouped by scene, split if a scene is too long for one call."""
    by_scene: dict[int, list[Line]] = defaultdict(list)
    for l in ep.lines:
        if l.type in ("action", "dialogue"):
            by_scene[l.scene].append(l)
    chunks = []
    for sc in sorted(by_scene):
        cur, size = [], 0
        for l in by_scene[sc]:
            r = len(l.render())
            if cur and size + r > max_chars:
                chunks.append(cur); cur, size = [], 0
            cur.append(l); size += r
        if cur:
            chunks.append(cur)
    return chunks


def normalize_value(ftype: str, attribute: str, value: str) -> str:
    v = norm(value)
    if ftype == "relationship":
        v = REL_SYNONYMS.get(v, v)
        for pre in ("younger ", "older ", "elder ", "little ", "big ", "late ", "beloved ", "dear "):
            if v.startswith(pre):
                v = v[len(pre):]
    if ftype == "identity" and norm(attribute) == "age":
        n = words_to_int(v) if not v.isdigit() else int(v)
        v = str(n) if n else v
    return v


def extract_episode(ep: Episode, aliases, llm, genre_desc: str, cfg: dict, characters_block: str) -> list[dict]:
    """Return raw fact dicts (before verification) for one episode."""
    chunks = scene_chunks(ep, int(cfg.get("scene_chunk_chars", 6000)))
    raw: list[dict] = []
    use_llm = llm is not None and llm.available_for("extract")

    def run_chunk(chunk: list[Line]) -> list[dict]:
        if not use_llm:
            out = []
            for l in chunk:
                spk = aliases.resolve_label(l.speaker, l.episode) if l.speaker else ""
                out.extend(extract_rules(l, spk))
            return out
        prompt = EXTRACT_PROMPT.format(genre=genre_desc, episode=ep.episode, characters=characters_block,
                                       lines="\n".join(l.render() for l in chunk))
        data = llm.complete_json(prompt, stage="extract", system=SYSTEM) or {}
        items = data.get("facts", data.get("items", []))
        return [i for i in items if isinstance(i, dict)]

    workers = int(cfg.get("workers", 1))
    if workers > 1 and use_llm:
        with ThreadPoolExecutor(workers) as ex:
            results = list(ex.map(run_chunk, chunks))
    else:
        results = [run_chunk(c) for c in chunks]
    for chunk, items in zip(chunks, results):
        for it in items:
            it["_chunk"] = chunk
            raw.append(it)
    return raw


def verify_facts(raw: list[dict], aliases, llm, threshold: float, entailment: bool, model_name: str) -> list[dict]:
    """Turn raw LLM/rule output into verified fact rows (dicts ready for Bible.add_fact)."""
    rows: list[dict] = []
    for it in raw:
        chunk: list[Line] = it.pop("_chunk")
        by_id = {l.line_id: l for l in chunk}
        try:
            f = Fact(**{k: v for k, v in it.items() if k in Fact.model_fields})
        except (ValidationError, TypeError) as e:
            rows.append(_dropped(it, chunk, f"schema: {str(e).splitlines()[0][:120]}", model_name))
            continue
        line = by_id.get(f.line_id)
        score = partial_ratio(f.quote, line.text) if line else 0.0
        if score < threshold:  # wrong id? look for the quote elsewhere in the scene
            best = max(chunk, key=lambda l: partial_ratio(f.quote, l.text))
            s2 = partial_ratio(f.quote, best.text)
            if s2 >= threshold:
                line, score = best, s2
        if line is None or score < threshold:
            rows.append(_dropped(it, chunk, f"quote not found in scene (score {score:.0f})", model_name))
            continue
        ep = line.episode
        subj, ok = aliases.resolve_name(f.subject, ep)
        attr = f.attribute
        attr_canon = attr
        if f.type == "relationship":
            attr_canon, _ = aliases.resolve_name(attr, ep)
        rows.append({
            "subject": subj, "subject_raw": f.subject, "subject_resolved": int(ok), "type": f.type,
            "attribute": attr_canon if f.type == "relationship" else norm(attr), "attribute_raw": attr,
            "value": normalize_value(f.type, attr, f.value), "source": "dialogue" if line.type == "dialogue" else "action",   # decided by the line, not the model
            "speaker": line.speaker, "line_id": line.line_id, "episode": ep, "ord": _ord(line), "quote": f.quote,
            "quote_score": round(score, 1), "entailment": "", "verified": 1, "drop_reason": "", "model": model_name})
    if entailment and llm is not None and llm.available_for("entail"):
        _entailment(rows, llm)
    return rows


def _ord(line: Line) -> int:
    try:
        return int(line.line_id.rsplit("L", 1)[1]) - 1   # same 0-based order as lines.ord
    except Exception:
        return 0


def _dropped(it: dict, chunk: list[Line], reason: str, model_name: str) -> dict:
    ep = chunk[0].episode if chunk else 0
    return {"subject": str(it.get("subject", "")), "subject_raw": str(it.get("subject", "")), "subject_resolved": 0,
            "type": str(it.get("type", "")), "attribute": str(it.get("attribute", "")), "attribute_raw": str(it.get("attribute", "")),
            "value": str(it.get("value", "")), "source": str(it.get("source", "")), "speaker": str(it.get("speaker", "")),
            "line_id": str(it.get("line_id", "")), "episode": ep, "ord": 0, "quote": str(it.get("quote", "")),
            "quote_score": 0.0, "entailment": "", "verified": 0, "drop_reason": reason, "model": model_name}


def _entailment(rows: list[dict], llm, batch: int = 15):
    live = [r for r in rows if r["verified"]]
    for i in range(0, len(live), batch):
        part = live[i:i + batch]
        items = "\n".join(json.dumps({"id": k, "fact": f"{r['subject']} | {r['type']} | {r['attribute_raw']} = {r['value']}",
                                      "quote": r["quote"]}, ensure_ascii=False) for k, r in enumerate(part))
        data = llm.complete_json(ENTAIL_PROMPT.format(items=items), stage="entail", system=SYSTEM) or {}
        answers = {int(x.get("id", -1)): str(x.get("answer", "unsure")).lower() for x in data.get("results", []) if isinstance(x, dict)}
        for k, r in enumerate(part):
            a = answers.get(k, "unsure")
            r["entailment"] = a
            if a == "no":
                r["verified"], r["drop_reason"] = 0, "entailment check: quote does not state the fact"


def characters_block(aliases, limit: int = 60) -> str:
    items = sorted(aliases.characters.items(), key=lambda kv: -len(kv[1]))[:limit]
    return "\n".join(f"- {c}" + (f" [{', '.join(l for l in labs if l.title() != c)}]" if len(labs) > 1 else "") for c, labs in items)
