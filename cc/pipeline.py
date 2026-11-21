"""Runs the whole workflow: parse -> names -> extract -> verify -> bible -> conflicts -> judge -> notes -> report.

Modes
  full audit   : process every episode (default)
  incremental  : keep the existing bible, extract only episodes not seen before, judge only
                 conflicts that involve them (how it would sit in a weekly writing workflow)
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Callable, Optional

import yaml

from .aliases import resolve_aliases, write_decisions_template
from .bible import Bible
from .conflicts import find_candidates
from .extract import characters_block, extract_episode, verify_facts
from .judge import VERDICT_RANK, check_notes, judge_candidate
from .llm import LLM, LLMError
from .parsing import dump_parse, parse_sources, load_sources
from .report import write_outputs

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Optional[str | Path] = None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_profile(name: str) -> dict:
    p = ROOT / "profiles" / f"{name}.yaml"
    if not p.exists():
        p = ROOT / "profiles" / "generic.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def rules_only(cfg: dict) -> dict:
    c = copy.deepcopy(cfg)
    c["stages"] = {k: {"provider": "rules"} for k in list(c.get("stages", {}).keys()) + ["default"]}
    return c


def run(input_path: str | Path, out_dir: str | Path, cfg: Optional[dict] = None, genre: Optional[str] = None,
        use_rules_only: bool = False, incremental: bool = False, force_format: Optional[str] = None,
        decisions_path: Optional[str | Path] = None, sources: Optional[list] = None,
        progress: Optional[Callable[[str, float], None]] = None, log: Callable[[str], None] = print,
        on_wait: Optional[Callable[[dict], None]] = None) -> dict:
    t0 = time.time()
    cfg = cfg or load_config()
    if use_rules_only:
        cfg = rules_only(cfg)
    pc = cfg.get("pipeline", {})
    genre = genre or pc.get("genre", "generic")
    profile = load_profile(genre)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    llm = LLM(cfg, cache_path=ROOT / ".cache" / "llm_cache.sqlite")
    llm.on_wait = on_wait   # lets the app show why the check is waiting
    prog = progress or (lambda msg, frac: None)
    warnings: list[str] = []

    # ---- 0. parse + coverage
    prog("Parsing scripts", 0.02)
    srcs = sources if sources is not None else load_sources(input_path)
    pr = parse_sources(srcs, llm=llm, min_coverage=float(pc.get("min_parse_coverage", 0.95)), force_format=force_format)
    warnings += pr.warnings
    if pr.coverage < float(pc.get("min_parse_coverage", 0.95)):
        warnings.append(f"LOW PARSE COVERAGE {pr.coverage:.1%}: results may miss parts of the script. Samples: {pr.unparsed_samples[:3]}")
    dump_parse(pr, out / "parsed_lines.jsonl")
    log(f"parsed {len(pr.episodes)} episodes, {len(pr.all_lines())} lines, format={pr.format_name}, coverage={pr.coverage:.2%}")

    # ---- 1. names
    prog("Resolving character names", 0.08)
    bible = Bible(out / "bible.sqlite")
    existing = bible.episodes_present() if incremental else set()
    bible.reset_derived(keep_facts=incremental)
    dpath = Path(decisions_path) if decisions_path else out / "alias_decisions.csv"
    aliases = resolve_aliases(pr.episodes, llm=llm, decisions_path=dpath,
                              auto_conf=float(pc.get("alias_auto_merge_confidence", 0.85)), log=log)
    bible.save_aliases(aliases)
    bible.set_meta("alias_scoped", aliases.scoped)
    bible.set_meta("genre", genre)
    bible.save_episodes(pr.episodes, aliases.resolve_label)
    write_decisions_template(aliases, out / "alias_decisions_pending.csv")
    if incremental and existing:   # names may have been merged since last run: re-resolve old facts
        for f in bible.q("SELECT fact_id, subject_raw, attribute_raw, type, episode FROM facts"):
            s, _ = aliases.resolve_name(f["subject_raw"], f["episode"])
            a = aliases.resolve_name(f["attribute_raw"], f["episode"])[0] if f["type"] == "relationship" else None
            bible.db.execute("UPDATE facts SET subject=?" + (", attribute=?" if a else "") + " WHERE fact_id=?",
                             (s, a, f["fact_id"]) if a else (s, f["fact_id"]))
        bible.commit()

    # ---- 2-3. extract + verify
    todo = [e for e in pr.episodes if e.episode not in existing]
    chars = characters_block(aliases)
    genre_desc = f"{profile.get('name')}: {profile.get('description', '')}"
    model_name = llm.describe("extract")
    n_raw = n_ok = 0
    for i, ep in enumerate(todo):
        prog(f"Extracting facts: episode {ep.episode}", 0.10 + 0.55 * i / max(1, len(todo)))
        try:
            raw = extract_episode(ep, aliases, llm, genre_desc, pc, chars)
            rows = verify_facts(raw, aliases, llm, float(pc.get("quote_match_threshold", 90)),
                                bool(pc.get("entailment_check", True)), model_name)
        except LLMError as e:
            warnings.append(f"episode {ep.episode}: extraction failed ({e}); continuing")
            log(f"WARNING episode {ep.episode}: {e}")
            continue
        for r in rows:
            bible.add_fact(r)
        bible.commit()
        n_raw += len(rows); n_ok += sum(r["verified"] for r in rows)
    log(f"facts: {n_raw} extracted, {n_ok} verified ({(n_ok / n_raw if n_raw else 0):.1%} grounded) from {len(todo)} episodes")

    # ---- 5-6. conflicts + judge
    prog("Finding conflicts", 0.68)
    cands = find_candidates(bible)
    new_eps = {e.episode for e in todo}
    if incremental and existing:
        cands = [c for c in cands if max(c.fact_a["episode"], (c.fact_b or c.line_b or {}).get("episode", 0)) in new_eps]
        bible.delete_flags_for_episodes(sorted(new_eps))
    cap = int(pc.get("max_judge_pairs", 400))
    if len(cands) > cap:
        warnings.append(f"{len(cands)} candidates; judged only the first {cap} (raise pipeline.max_judge_pairs)")
    for i, c in enumerate(cands[:cap]):
        prog(f"Judging candidate {i + 1}/{min(len(cands), cap)}", 0.70 + 0.2 * i / max(1, min(len(cands), cap)))
        try:
            bible.add_flag(judge_candidate(c, bible, llm, profile))
        except LLMError as e:
            warnings.append(f"judge failed on a candidate ({e})")
    bible.commit()

    # ---- notes check
    prog("Checking continuity notes", 0.92)
    if pc.get("notes_check", True):
        try:
            nflags, nmsg = check_notes(pr.episodes, bible, llm, only_episodes=new_eps if incremental and existing else None)
        except LLMError as e:
            nflags, nmsg = [], f"notes check failed: {e}"
        for f in nflags:
            bible.add_flag(f)
        bible.commit()
    else:
        nmsg = "disabled in config"
    log(f"notes check: {nmsg}")

    # ---- merge sanity: many contradictions that straddle the episodes where DIFFERENT labels of one
    #      merged character were used suggest the merge joined two people
    for ch, labs in aliases.characters.items():
        if len(labs) < 2:
            continue
        lab_eps = {l: {r["episode"] for r in bible.q("SELECT DISTINCT episode FROM lines WHERE speaker=?", (l,))} for l in labs}

        def labels_in(ep):
            return {l for l, es in lab_eps.items() if ep in es}
        rows = bible.q("SELECT a.episode ea, b.episode eb FROM flags f JOIN facts a ON a.fact_id=f.fact_a "
                       "JOIN facts b ON b.fact_id=f.fact_b WHERE f.verdict='contradiction' AND a.subject=?", (ch,))
        cross = sum(1 for r in rows if labels_in(r["ea"]) and labels_in(r["eb"]) and not (labels_in(r["ea"]) & labels_in(r["eb"])))
        if cross >= 4 and cross >= 0.5 * len(rows):
            bible.add_flag({"rule": "possible_wrong_merge", "verdict": "unsure", "severity": "medium", "reason_verified": 1,
                            "reason": f"{cross} of {len(rows)} contradictions about '{ch}' compare episodes where different labels "
                                      f"({', '.join(labs)}) were used. Check the merge before the flags.",
                            "note": f"merged labels: {labs}", "model": "rules", "episode_max": 0, "payload": json.dumps({"labels": labs})})
    bible.commit()

    # ---- 7. outputs
    prog("Writing report", 0.97)
    summary = {
        "show_input": str(input_path), "genre": genre, "format": pr.format_name, "coverage": round(pr.coverage, 4),
        "episodes": len(pr.episodes), "lines": len(pr.all_lines()), "episodes_extracted_this_run": sorted(new_eps),
        "labels": len(aliases.label_to_char), "characters": len(aliases.characters),
        "merges_auto": sum(p.status == "merged_auto" for p in aliases.pairs),
        "merges_human": sum(p.status == "merged_human" for p in aliases.pairs),
        "merges_pending": sum(p.status == "pending" for p in aliases.pairs),
        "merges_vetoed_same_scene": sum(p.status == "separate_coscene" for p in aliases.pairs),
        "candidates": len(cands), "notes_check": nmsg,
        "models": {s: llm.describe(s) for s in ("parse", "alias", "extract", "entail", "judge", "notes")},
        "llm_stats": {"calls": llm.stats.calls, "cache_hits": llm.stats.cache_hits, "prompt_tokens": llm.stats.prompt_tokens,
                      "completion_tokens": llm.stats.completion_tokens, "errors": llm.stats.errors, "per_stage": llm.stats.per_stage},
        "seconds": round(time.time() - t0, 1), "warnings": warnings, "mode": "incremental" if incremental and existing else "full",
    }
    bible.set_meta("summary", summary)
    write_outputs(bible, out, summary)
    prog("Done", 1.0)
    log(f"done in {summary['seconds']}s -> {out / 'report.md'}")
    return summary
