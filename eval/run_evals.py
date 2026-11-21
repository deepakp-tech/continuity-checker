"""Evaluation harness -> eval_report.md

  # synthetic shows (public): planted errors, traps, name traps, clean control
  python eval/run_evals.py --suite synthetic --out eval_out [--compare-rules]

  # the real pack (confidential - keep --work OUTSIDE the repo)
  python eval/run_evals.py --suite pack --input <pack folder> --work ../private_runs --out ../private_runs/eval [--compare-rules]

--compare-rules adds a 'rules only (no LLM)' column: the ablation baseline.
Hand labels (optional, in eval/labels/ or --labels): fill the *_TEMPLATE.csv files this script writes and save them
without the _TEMPLATE suffix; the next run then also reports name-merge, extraction and judge accuracy against your labels.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from cc.bible import Bible                          # noqa: E402
from cc.judge import judge_candidate                # noqa: E402
from cc.llm import LLM                              # noqa: E402
from cc.pipeline import load_config, load_profile, rules_only, run  # noqa: E402
from cc.schema import Candidate                     # noqa: E402
from metrics import alias_metrics, read_gold_aliases, score_run   # noqa: E402

TARGETS = {
    "recall": (">=", 0.8), "trap_pass_rate": (">=", 0.8), "precision": (">=", 0.7), "clean_false_alarms": ("<=", 1),
    "grounding_rate": (">=", 0.9), "merge_precision": (">=", 1.0), "wrong_merges": ("<=", 0), "merge_recall": (">=", 0.8),
    "order_swap_agreement": (">=", 0.85), "extraction_precision": (">=", 0.9), "judge_accuracy": (">=", 0.8),
    "twist_false_alarm_rate": ("<=", 0.2), "regression_agreement": (">=", 0.9),
}


def all_flags(run_dir: Path) -> list[dict]:
    b = Bible(run_dir / "bible.sqlite")
    return [b.flag_detail(r["flag_id"]) for r in b.q("SELECT flag_id FROM flags")]


def grounding(run_dir: Path) -> float | None:
    b = Bible(run_dir / "bible.sqlite")
    r = b.q("SELECT COUNT(*) n, SUM(verified) v FROM facts")[0]
    return (r["v"] or 0) / r["n"] if r["n"] else None


def characters(run_dir: Path) -> dict:
    b = Bible(run_dir / "bible.sqlite")
    return {r["name"]: json.loads(r["labels"]) for r in b.q("SELECT * FROM characters")}


def apply_patch(src: Path, dst: Path, patch: list[dict]):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    for p in patch:
        hits = [f for f in dst.glob("*.txt") if p["find"] in f.read_text(encoding="utf-8")]
        if len(hits) != 1:
            raise ValueError(f"clean patch text found {len(hits)} times: {p['find'][:50]}")
        t = hits[0].read_text(encoding="utf-8")
        hits[0].write_text(t.replace(p["find"], p["replace"]), encoding="utf-8")


def order_swap(run_dir: Path, cfg: dict, genre: str, n: int = 25) -> float | None:
    """Re-judge pairs with A and B swapped; a stable judge gives the same verdict."""
    llm = LLM(cfg, cache_path=ROOT / ".cache" / "llm_cache.sqlite")
    if not llm.available_for("judge"):
        return None
    b = Bible(run_dir / "bible.sqlite")
    prof = load_profile(genre)
    rows = b.q("SELECT * FROM flags WHERE fact_a IS NOT NULL AND fact_b IS NOT NULL LIMIT ?", (n,))
    same = 0
    for r in rows:
        c = Candidate(r["rule"], b.fact(r["fact_b"]), b.fact(r["fact_a"]), note=r["note"])
        same += judge_candidate(c, b, llm, prof)["verdict"] == r["verdict"]
    return same / len(rows) if rows else None


def regression(run_dir: Path, feedback: Path) -> float | None:
    """Editor decisions from earlier runs must still hold: accepted -> still a contradiction, dismissed -> not."""
    if not feedback or not feedback.exists():
        return None
    flags = all_flags(run_dir)
    def key(fl):
        return ((fl.get("a") or {}).get("quote", ""), (fl.get("b") or {}).get("quote", ""), fl["rule"])
    now = {key(f): f["verdict"] for f in flags}
    ok = total = 0
    for line in feedback.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        k = key(d["flag"])
        if k not in now:
            continue
        total += 1
        want_contra = d["decision"] == "accepted"
        ok += (now[k] == "contradiction") == want_contra
    return ok / total if total else None


def run_system(name: str, cfg: dict, show_dir: Path, out_dir: Path, genre: str, fmt: str | None) -> Path:
    run(show_dir, out_dir, cfg=cfg, genre=genre, force_format=fmt, log=lambda *_: None)
    return out_dir


def eval_synthetic(systems: dict, out: Path) -> dict:
    res = {}
    for kf in sorted((ROOT / "eval" / "answer_keys").glob("*.json")):
        key = json.loads(kf.read_text(encoding="utf-8"))
        show = key["show"]
        src = ROOT / "eval" / "synthetic" / show
        fmt = key.get("format")
        for sname, cfg in systems.items():
            is_rules = sname.startswith("rules")
            d = out / sname / show
            run_system(sname, cfg, src, d / "planted", key["genre"], fmt)
            flags = all_flags(d / "planted")
            sc = score_run(flags, key, rules_only=is_rules)
            clean_src = out / "_clean_inputs" / show
            apply_patch(src, clean_src, key["clean_patch"])
            run_system(sname, cfg, clean_src, d / "clean", key["genre"], fmt)
            cf = [f for f in all_flags(d / "clean") if f["verdict"] == "contradiction"]
            am = alias_metrics(characters(d / "planted"), key["alias_groups"])
            res.setdefault(show, {})[sname] = {
                **sc, "clean_false_alarms": len(cf), "clean_false_alarm_notes": [f["note"] for f in cf],
                "grounding_rate": grounding(d / "planted"), **am,
                "order_swap_agreement": order_swap(d / "planted", cfg, key["genre"]),
            }
    return res


def eval_pack(systems: dict, pack: Path, work: Path, out: Path, labels: Path, seed: int, feedback: Path | None) -> dict:
    sys.path.insert(0, str(ROOT / "eval"))
    import plant_errors
    planted = work / f"pack_planted_seed{seed}"
    plant_errors.main(["--input", str(pack), "--out", str(planted), "--seed", str(seed)])
    key = json.loads((work / f"{planted.name}_answer_key.json").read_text(encoding="utf-8"))
    res = {}
    for sname, cfg in systems.items():
        is_rules = sname.startswith("rules")
        d = work / "runs" / sname
        run_system(sname, cfg, planted, d / "planted", key["genre"], "kuku")
        sc = score_run(all_flags(d / "planted"), key, rules_only=is_rules)
        run_system(sname, cfg, pack, d / "original", key["genre"], "kuku")
        orig = [f for f in all_flags(d / "original") if f["verdict"] in ("contradiction", "unsure")]
        r = {**sc, "grounding_rate": grounding(d / "planted"),
             "original_flags_to_review": len(orig), "order_swap_agreement": order_swap(d / "planted", cfg, key["genre"]),
             "regression_agreement": regression(d / "original", feedback) if feedback else None}
        # ---- label templates + label-based metrics
        labels.mkdir(parents=True, exist_ok=True)
        chars = characters(d / "original")
        if not is_rules or len(systems) == 1:
            _templates(d / "original", labels, sname)
        gold = labels / "pack_alias_gold.csv"
        if gold.exists():
            r.update(alias_metrics(chars, read_gold_aliases(gold)))
        fl = labels / f"facts_sample_{sname}.csv"
        if fl.exists():
            rows = [x for x in csv.DictReader(open(fl, encoding="utf-8")) if x.get("label")]
            if rows:
                r["extraction_precision"] = sum(x["label"].strip().lower() == "supported" for x in rows) / len(rows)
                r["extraction_labeled"] = len(rows)
        jl = labels / f"judge_pairs_{sname}.csv"
        if jl.exists():
            rows = [x for x in csv.DictReader(open(jl, encoding="utf-8")) if x.get("gold_verdict")]
            if rows:
                r["judge_accuracy"] = sum(x["gold_verdict"].strip() == x["tool_verdict"] for x in rows) / len(rows)
                tw = [x for x in rows if x["gold_verdict"].strip() == "intentional"]
                r["twist_false_alarm_rate"] = (sum(x["tool_verdict"] == "contradiction" for x in tw) / len(tw)) if tw else None
                r["judge_confusion"] = _confusion(rows)
        res[sname] = r
    return {"pack": res}


def _confusion(rows):
    m: dict = {}
    for x in rows:
        m.setdefault(x["gold_verdict"].strip(), {}).setdefault(x["tool_verdict"], 0)
        m[x["gold_verdict"].strip()][x["tool_verdict"]] += 1
    return m


def _templates(run_dir: Path, labels: Path, sname: str):
    b = Bible(run_dir / "bible.sqlite")
    with open(labels / "pack_alias_gold_TEMPLATE.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["label", "character", "episodes", "lines"])
        for r in b.q("SELECT speaker, COUNT(*) n, MIN(episode) a, MAX(episode) z FROM lines WHERE type='dialogue' GROUP BY speaker ORDER BY n DESC"):
            w.writerow([r["speaker"], "", f"{r['a']}-{r['z']}", r["n"]])
    facts = b.q("SELECT fact_id, episode, line_id, subject, type, attribute_raw, value, source, quote FROM facts WHERE verified=1")
    random.Random(1).shuffle(facts)
    with open(labels / f"facts_sample_{sname}_TEMPLATE.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["fact_id", "episode", "line_id", "fact", "quote", "label (supported/misread/invented)"])
        for x in facts[:50]:
            w.writerow([x["fact_id"], x["episode"], x["line_id"], f"{x['subject']} | {x['type']} | {x['attribute_raw']} = {x['value']}", x["quote"], ""])
    with open(labels / f"judge_pairs_{sname}_TEMPLATE.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["flag_id", "rule", "fact_a", "quote_a", "fact_b", "quote_b", "tool_verdict",
                                       "gold_verdict (contradiction/story_change/intentional/extraction_error)"])
        for r in b.q("SELECT flag_id FROM flags WHERE fact_a IS NOT NULL LIMIT 60"):
            fl = b.flag_detail(r["flag_id"]); a, bb = fl.get("a") or {}, fl.get("b") or fl.get("line_b_row") or {}
            w.writerow([fl["flag_id"], fl["rule"], f"{a.get('subject')} | {a.get('attribute')} = {a.get('value')}", a.get("quote"),
                        f"{bb.get('subject', bb.get('character'))} | {bb.get('attribute', '')} = {bb.get('value', '(speaks)')}",
                        bb.get("quote", bb.get("text", ""))[:200], fl["verdict"], ""])


# ------------------------------------------------------------------------------------- report
def fmt(v):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.0%}" if v <= 1 else f"{v:.2f}"
    return str(v)


def passes(metric, v):
    if v is None or metric not in TARGETS:
        return ""
    op, t = TARGETS[metric]
    ok = v >= t if op == ">=" else v <= t
    return "PASS" if ok else "FAIL"


def write_report(results: dict, systems: dict, out: Path):
    L = ["# Evaluation report", "",
         "Targets were fixed before running. `n/a` = not applicable to this system or no labels yet.", "",
         "Systems: " + "; ".join(f"**{s}** = " + ", ".join(f"{k}:{(v.get('model') or v['provider'])}" for k, v in c["stages"].items() if k in ("extract", "judge", "alias"))
                                 for s, c in systems.items()), ""]
    rows = [("recall", "Planted errors caught (verdict = contradiction)"),
            ("recall_lenient", "Planted errors caught or sent to a human (unsure)"),
            ("trap_pass_rate", "Traps NOT flagged as contradictions (lies, disguises, recoveries, genre devices)"),
            ("precision", "Contradiction flags that are real (planted) errors"),
            ("clean_false_alarms", "Contradictions on the clean control show (false alarms)"),
            ("original_flags_to_review", "Flags on the unmodified pack (label them to get precision)"),
            ("grounding_rate", "Extracted facts whose quote verified"),
            ("merge_precision", "Name merges that are correct"), ("wrong_merges", "Wrong merges (two people as one)"),
            ("merge_recall", "Same-person label pairs found"),
            ("order_swap_agreement", "Judge gives the same verdict with A/B swapped"),
            ("extraction_precision", "Hand-checked facts that are supported (labels)"),
            ("judge_accuracy", "Judge verdict = your label (labels)"),
            ("twist_false_alarm_rate", "Intentional twists wrongly called contradictions (labels)"),
            ("regression_agreement", "Earlier editor decisions still respected")]
    sysnames = list(systems)
    for show, by_sys in results.items():
        L += [f"## {show}", "", "| Metric | Target | " + " | ".join(sysnames) + " |", "|---|---|" + "---|" * len(sysnames)]
        for m, label in rows:
            vals = [by_sys.get(s, {}).get(m) for s in sysnames]
            if all(v is None for v in vals):
                continue
            tgt = f"{TARGETS[m][0]} {fmt(TARGETS[m][1])}" if m in TARGETS else ""
            cells = [f"{fmt(v)} {passes(m, v)}".strip() for v in vals]
            L.append(f"| {label} | {tgt} | " + " | ".join(cells) + " |")
        L.append("")
        for s in sysnames:
            r = by_sys.get(s, {})
            if not r:
                continue
            L += [f"**Per item - {s}**", ""]
            for p in r.get("per_item", []):
                ok = ("caught" if "contradiction" in p["verdicts"] else "sent to human" if "unsure" in p["verdicts"] else "MISSED") \
                    if p["kind"] == "error" else ("FLAGGED (wrong)" if "contradiction" in p["verdicts"] else "ok, not flagged")
                if p["llm_only"] and s.startswith("rules"):
                    ok = "n/a (needs an LLM)"
                L.append(f"- {p['id']} [{p['kind']}, {p['type']}] {p['desc']} -> **{ok}** {p['verdicts'] or ''}")
            if r.get("wrong_merge_pairs"):
                L.append(f"- wrong merges: {r['wrong_merge_pairs']}")
            if r.get("clean_false_alarm_notes"):
                L.append(f"- false alarms on clean show: {r['clean_false_alarm_notes']}")
            if r.get("unexplained_flags"):
                L.append(f"- flags not matching any planted item (review by hand): ids {r['unexplained_flags'][:20]}")
            if r.get("judge_confusion"):
                L.append(f"- judge confusion (gold -> tool): {r['judge_confusion']}")
            L.append("")
    (out / "eval_report.md").write_text("\n".join(L), encoding="utf-8")
    (out / "eval_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["synthetic", "pack"], required=True)
    ap.add_argument("--out", default="eval_out"); ap.add_argument("--config")
    ap.add_argument("--compare-rules", action="store_true", help="add the rules-only baseline column")
    ap.add_argument("--rules-only", action="store_true", help="evaluate only the rules baseline (offline)")
    ap.add_argument("--input", help="pack folder (suite=pack)"); ap.add_argument("--work", help="working folder for planted copies (suite=pack)")
    ap.add_argument("--labels", default=str(ROOT / "eval" / "labels")); ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--feedback", help="feedback_log.jsonl from the app, for the regression check")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    systems = {"rules_only": rules_only(cfg)} if a.rules_only else {"llm": cfg}
    if a.compare_rules and not a.rules_only:
        systems["rules_only"] = rules_only(cfg)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if a.suite == "synthetic":
        results = eval_synthetic(systems, out)
    else:
        if not a.input or not a.work:
            raise SystemExit("--input and --work are required for the pack suite")
        results = eval_pack(systems, Path(a.input), Path(a.work), out, Path(a.labels), a.seed, Path(a.feedback) if a.feedback else None)
    write_report(results, systems, out)
    print(f"report -> {out / 'eval_report.md'}")


if __name__ == "__main__":
    main()
