"""Stage 7 outputs: flags.csv (ranked), facts.csv, characters.csv, alias_pairs.csv, report.md, run_log.json."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .judge import SEV_RANK, VERDICT_RANK


def ranked_flags(bible, include_all: bool = False) -> list[dict]:
    flags = [bible.flag_detail(r["flag_id"]) for r in bible.q("SELECT flag_id FROM flags")]
    if not include_all:
        flags = [f for f in flags if f["verdict"] in ("contradiction", "unsure")]
    flags.sort(key=lambda f: (VERDICT_RANK.get(f["verdict"], 9), SEV_RANK.get(f["severity"], 9), -(f["episode_max"] or 0)))
    return flags


def _side(f: dict, key: str) -> tuple[str, str, str]:
    """(where, who/what, quote) for one side of a flag."""
    x = f.get(key) or {}
    if x:
        return f"Ep {x['episode']} {x['line_id']}", f"{x['subject']} | {x['type']} | {x['attribute']} = {x['value']} ({x['source']})", x["quote"]
    if key == "b" and f.get("line_b_row"):
        l = f["line_b_row"]
        return f"Ep {l['episode']} {l['line_id']} @{l['ts_start']}", f"{l.get('character') or l.get('speaker')} speaks", l["text"][:160]
    p = json.loads(f.get("payload") or "{}")
    if f["rule"] == "notes_mismatch" and key == "a":
        return f"Ep {p.get('episode')} notes", "continuity note claim", p.get("claim", "")
    return "", "", ""


def write_outputs(bible, out: Path, summary: dict) -> None:
    flags_all = ranked_flags(bible, include_all=True)
    with open(out / "flags.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["flag_id", "verdict", "severity", "rule", "where_a", "fact_a", "quote_a", "where_b", "fact_b", "quote_b",
                    "reason", "reason_verified", "model", "status", "note"])
        for fl in flags_all:
            wa, fa, qa = _side(fl, "a"); wb, fb, qb = _side(fl, "b")
            w.writerow([fl["flag_id"], fl["verdict"], fl["severity"], fl["rule"], wa, fa, qa, wb, fb, qb, fl["reason"],
                        fl["reason_verified"], fl["model"], fl["status"], fl["note"]])
    facts = bible.q("SELECT * FROM facts ORDER BY episode, ord")
    if facts:
        with open(out / "facts.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(facts[0].keys())); w.writeheader(); w.writerows(facts)
    with open(out / "characters.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["character", "labels"])
        for r in bible.q("SELECT * FROM characters ORDER BY name"):
            w.writerow([r["name"], " | ".join(json.loads(r["labels"]))])
    pairs = bible.q("SELECT * FROM alias_pairs ORDER BY status, label_a")
    with open(out / "alias_pairs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["label_a", "label_b", "status", "tool_verdict", "confidence", "co_scenes", "reason"])
        for p in pairs:
            w.writerow([p["label_a"], p["label_b"], p["status"], p["verdict"], f"{p['confidence']:.2f}", p["co_scenes"], p["reason"]])
    (out / "run_log.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_md(bible, out, summary, flags_all, facts, pairs)


def _write_md(bible, out, s, flags_all, facts, pairs):
    open_flags = [f for f in flags_all if f["verdict"] in ("contradiction", "unsure")]
    by_v: dict = {}
    for f in flags_all:
        by_v[f["verdict"]] = by_v.get(f["verdict"], 0) + 1
    verified = sum(1 for f in facts if f["verified"])
    drops: dict = {}
    for f in facts:
        if not f["verified"]:
            k = f["drop_reason"].split("(")[0].split(":")[0].strip()
            drops[k] = drops.get(k, 0) + 1
    L = [f"# Continuity report", "",
         f"- Input: `{Path(s['show_input']).name}` · genre profile: **{s['genre']}** · mode: {s['mode']}",
         f"- Format: {s['format']} · parse coverage: **{s['coverage']:.1%}** · {s['episodes']} episodes · {s['lines']} lines",
         f"- Characters: {s['labels']} speaker labels → {s['characters']} characters "
         f"({s['merges_auto']} auto-merged, {s['merges_human']} by a human, **{s['merges_pending']} waiting for a human**, "
         f"{s['merges_vetoed_same_scene']} kept apart because they share a scene)",
         f"- Facts: {len(facts)} extracted, {verified} verified "
         f"(**{(verified / len(facts) if facts else 0):.1%} grounded**); dropped: {drops or 'none'}",
         f"- Candidates checked: {s['candidates']} · verdicts: {by_v or 'none'}",
         f"- Notes check: {s['notes_check']}",
         f"- Models: " + ", ".join(f"{k}={v}" for k, v in s["models"].items()),
         f"- LLM calls: {s['llm_stats']['calls']} (+{s['llm_stats']['cache_hits']} from cache), tokens in/out: "
         f"{s['llm_stats']['prompt_tokens']}/{s['llm_stats']['completion_tokens']} · run time {s['seconds']}s", ""]
    if s["warnings"]:
        L += ["## Warnings", ""] + [f"- {w}" for w in s["warnings"]] + [""]
    L += [f"## Flags to review ({len(open_flags)})", "",
          "Ranked: contradictions first (high → low severity), then unsure. Every flag cites the exact lines.", ""]
    for i, fl in enumerate(open_flags, 1):
        wa, fa, qa = _side(fl, "a"); wb, fb, qb = _side(fl, "b")
        L += [f"### {i}. [{fl['verdict']} · {fl['severity']}] {fl['note'] or fl['rule']}", "",
              f"- **A** ({wa}) {fa}", f"  > {qa}"]
        if wb:
            L += [f"- **B** ({wb}) {fb}", f"  > {qb}"]
        L += [f"- **Why:** {fl['reason']}" + ("" if fl["reason_verified"] else " _(reason failed verification)_"),
              f"- rule `{fl['rule']}` · judged by {fl['model']} · flag id {fl['flag_id']}", ""]
    pend = [p for p in pairs if p["status"] == "pending"]
    if pend:
        L += ["## Name decisions waiting for a human", "",
              "Fill in `alias_decisions_pending.csv` (decision = same / different, optional ep_from/ep_to), save it as "
              "`alias_decisions.csv` and re-run. Until then these labels are treated as **different** people.", "",
              "| Label A | Label B | Tool says | Confidence | Reason |", "|---|---|---|---|---|"]
        L += [f"| {p['label_a']} | {p['label_b']} | {p['verdict']} | {p['confidence']:.2f} | {p['reason'][:110].replace('|', '/')} |" for p in pend]
        L.append("")
    other = [f for f in flags_all if f["verdict"] not in ("contradiction", "unsure")]
    if other:
        L += [f"## Pairs judged NOT to be errors ({len(other)})", "", "Shown for transparency (story changes, intentional devices, extraction errors).", ""]
        for fl in other[:60]:
            L.append(f"- [{fl['verdict']}] {fl['note'] or fl['rule']} — {fl['reason'][:140]}")
        L.append("")
    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
