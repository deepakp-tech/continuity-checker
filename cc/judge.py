"""Stage 6: the judge classifies each candidate, and the notes check verifies the writer's
CONTINUITY NOTES against the episodes they reference.

Anti-hallucination: the judge only sees quotes already in the bible. Any text it quotes in its
reason must exist in that context - if not, the verdict is downgraded to 'unsure'."""
from __future__ import annotations

import json
import re

from .prompts import JUDGE_PROMPT, NOTES_PROMPT, SYSTEM
from .rules import judge_rules
from .schema import Candidate, Verdict
from .textmatch import partial_ratio

SEV_RANK = {"high": 0, "medium": 1, "low": 2}
VERDICT_RANK = {"contradiction": 0, "unsure": 1, "extraction_error": 2, "intentional": 3, "story_change": 4}


def _ctx_text(rows: list[dict]) -> str:
    out = []
    for r in rows:
        who = r["speaker"] if r["type"] == "dialogue" else r["type"].upper()
        out.append(f"  [{r['line_id']} {r['ts_start']}] {who}: {r['text']}")
    return "\n".join(out) or "  (no context)"


def judge_candidate(c: Candidate, bible, llm, profile: dict) -> dict:
    a = c.fact_a
    if c.rule == "speaks_after_death":
        lb = c.line_b
        b = {"subject": lb["character"], "type": "status", "attribute": "life", "value": "alive (speaks)", "source": "dialogue",
             "speaker": lb["speaker"], "episode": lb["episode"], "line_id": lb["line_id"], "quote": lb["text"][:200], "fact_id": None}
    else:
        b = c.fact_b
    ctx_a, ctx_b = bible.context(a["line_id"]), bible.context(b["line_id"])
    use_llm = llm is not None and llm.available_for("judge")
    if use_llm:
        prompt = JUDGE_PROMPT.format(
            genre=f"{profile.get('name')}: {profile.get('description','')}", allowed=", ".join(profile.get("allowed_devices", [])),
            notes=" ".join(str(profile.get("judge_notes", "")).split()), rule=c.note or c.rule,
            ep_a=a["episode"], ts_a=bible.line(a["line_id"]).get("ts_start", ""), src_a=a["source"],
            spk_a=f", said by {a['speaker']}" if a.get("speaker") else "", subj_a=a["subject"], type_a=a["type"],
            attr_a=a.get("attribute_raw") or a["attribute"], val_a=a["value"], quote_a=a["quote"], ctx_a=_ctx_text(ctx_a),
            ep_b=b["episode"], ts_b=bible.line(b["line_id"]).get("ts_start", ""), src_b=b["source"],
            spk_b=f", said by {b['speaker']}" if b.get("speaker") else "", subj_b=b["subject"], type_b=b["type"],
            attr_b=b.get("attribute_raw") or b.get("attribute", ""), val_b=b["value"], quote_b=b["quote"], ctx_b=_ctx_text(ctx_b))
        raw = llm.complete_json(prompt, stage="judge", system=SYSTEM) or {}
        model = llm.describe("judge")
    else:
        raw = judge_rules(c, profile)
        model = "rules"
    # models sometimes send null or a non-string for a field: treat it as blank so the defaults apply
    v = Verdict(**{k: str(raw.get(k) or "") for k in ("verdict", "severity", "reason")})
    # every quoted span in the reason must exist in what the judge was shown
    shown = " ".join([a["quote"], b["quote"]] + [r["text"] for r in ctx_a + ctx_b])
    quoted = [q for q in re.findall(r'"([^"]{8,})"', v.reason)]
    reason_ok = all(partial_ratio(q, shown) >= 85 for q in quoted)
    verdict, reason = v.verdict, v.reason
    if not reason_ok:
        verdict, reason = "unsure", "[reason cited text not in the script - downgraded] " + reason
    return {"rule": c.rule, "fact_a": a.get("fact_id"), "fact_b": b.get("fact_id"), "line_b": (c.line_b or {}).get("line_id"),
            "verdict": verdict, "severity": v.severity, "reason": reason, "reason_verified": int(reason_ok), "note": c.note,
            "model": model, "episode_max": max(a["episode"], b["episode"]),
            "payload": json.dumps({"a": {k: a.get(k) for k in ("subject", "type", "attribute", "value", "quote", "episode", "line_id", "source", "speaker")},
                                   "b": {k: b.get(k) for k in ("subject", "type", "attribute", "value", "quote", "episode", "line_id", "source", "speaker")}},
                                  ensure_ascii=False)}


def check_notes(episodes, bible, llm, only_episodes=None, max_chars: int = 9000) -> tuple[list[dict], str]:
    if llm is None or not llm.available_for("notes"):
        return [], "skipped: the notes check needs an LLM (stage 'notes' is set to rules)"
    by_ep = {e.episode: e for e in episodes}
    flags: list[dict] = []
    checked = 0
    for e in episodes:
        if not e.continuity_notes or (only_episodes and e.episode not in only_episodes):
            continue
        refs = sorted({int(x) for x in re.findall(r"Episode\s+(\d+)", e.continuity_notes) if int(x) < e.episode}) or [e.episode - 1]
        for ref in refs[:2]:
            if ref not in by_ep:
                continue
            lines = [l for l in by_ep[ref].lines if l.type in ("action", "dialogue")]
            text, ids = "", set()
            for l in lines:
                if len(text) > max_chars:
                    break
                text += l.render() + "\n"; ids.add(l.line_id)
            data = llm.complete_json(NOTES_PROMPT.format(episode=e.episode, ref_episode=ref, notes=e.continuity_notes, lines=text),
                                     stage="notes", system=SYSTEM) or {}
            checked += 1
            for cl in data.get("claims", []):
                st = str(cl.get("status", "")).lower()
                if st not in ("contradicted", "not_found"):
                    continue
                ev = [i for i in cl.get("evidence_line_ids", []) if i in ids]
                if st == "contradicted" and not ev:
                    st = "not_found"   # a contradiction must cite real lines
                flags.append({"rule": "notes_mismatch", "fact_a": None, "fact_b": None, "line_b": ev[0] if ev else None,
                              "verdict": "contradiction" if st == "contradicted" else "unsure",
                              "severity": "medium" if st == "contradicted" else "low",
                              "reason": str(cl.get("reason", "")), "reason_verified": 1,
                              "note": f"Ep {e.episode} notes claim about ep {ref}: {cl.get('claim','')}",
                              "model": llm.describe("notes"), "episode_max": e.episode,
                              "payload": json.dumps({"episode": e.episode, "ref_episode": ref, "claim": cl.get("claim", ""),
                                                     "status": st, "evidence": ev}, ensure_ascii=False)})
    return flags, f"checked {checked} note/episode pairs"
