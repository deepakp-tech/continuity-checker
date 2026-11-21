"""Matching flags to answer-key items, and the metric calculations used by run_evals.py."""
from __future__ import annotations

import csv
import json
from itertools import combinations
from pathlib import Path


def flag_signature(fl: dict) -> tuple[set[str], set[int], str]:
    """(subjects, episodes, rule) of a flag, from the bible's flag_detail()."""
    subs, eps = set(), set()
    for k in ("a", "b"):
        x = fl.get(k) or {}
        if x:
            subs.add(str(x.get("subject", "")).lower())
            if x.get("type") == "relationship":
                subs.add(str(x.get("attribute", "")).lower())
            eps.add(int(x["episode"]))
    lb = fl.get("line_b_row") or {}
    if lb:
        subs.add(str(lb.get("character") or lb.get("speaker", "")).lower())
        eps.add(int(lb["episode"]))
    if fl["rule"] == "notes_mismatch":
        p = json.loads(fl.get("payload") or "{}")
        eps |= {int(p.get("episode", 0)), int(p.get("ref_episode", 0))}
    return {s for s in subs if s}, eps, fl["rule"]


def matches(fl: dict, item: dict) -> bool:
    subs, eps, rule = flag_signature(fl)
    exp_eps = set(item["episodes"])
    if item.get("type") == "notes":
        return rule == "notes_mismatch" and eps == exp_eps
    if rule in ("notes_mismatch", "possible_wrong_merge"):
        return False
    if not eps or not eps <= exp_eps:
        return False
    want = {s.lower() for s in item.get("subjects", [])}
    return bool(want & subs) if want else True


def score_run(flags: list[dict], key: dict, rules_only: bool) -> dict:
    """Recall on planted errors, trap pass rate, precision of contradiction flags."""
    errors = [e for e in key["expected"] if e["kind"] == "error"]
    traps = [e for e in key["expected"] if e["kind"] == "trap"]
    applicable = [e for e in errors if not (rules_only and e.get("llm_only"))]
    contra = [f for f in flags if f["verdict"] == "contradiction"]
    flagged = [f for f in flags if f["verdict"] in ("contradiction", "unsure")]
    per_item = []
    for e in key["expected"]:
        m = [f for f in flags if matches(f, e)]
        verdicts = sorted({f["verdict"] for f in m})
        per_item.append({"id": e["id"], "kind": e["kind"], "type": e.get("type", ""), "desc": e["desc"],
                         "llm_only": bool(e.get("llm_only")), "verdicts": verdicts,
                         "flag_ids": [f["flag_id"] for f in m]})
    def got(item_id, ok):
        return any(p["id"] == item_id and set(p["verdicts"]) & ok for p in per_item)
    strict = sum(got(e["id"], {"contradiction"}) for e in applicable)
    lenient = sum(got(e["id"], {"contradiction", "unsure"}) for e in applicable)
    traps_ok = sum(not got(t["id"], {"contradiction"}) for t in traps)
    tp = sum(1 for f in contra if any(matches(f, e) for e in errors))
    return {
        "errors_applicable": len(applicable), "errors_caught": strict, "errors_caught_or_sent_to_human": lenient,
        "recall": strict / len(applicable) if applicable else None,
        "recall_lenient": lenient / len(applicable) if applicable else None,
        "traps": len(traps), "traps_passed": traps_ok, "trap_pass_rate": traps_ok / len(traps) if traps else None,
        "contradiction_flags": len(contra), "true_positive_flags": tp,
        "precision": tp / len(contra) if contra else None,
        "unexplained_flags": [f["flag_id"] for f in flagged if not any(matches(f, e) for e in key["expected"])],
        "per_item": per_item,
    }


def alias_metrics(characters: dict[str, list[str]], gold_groups: list[list[str]]) -> dict:
    """Pairwise precision/recall of 'same person' decisions over labels in the gold key."""
    gold_of = {l.upper(): i for i, g in enumerate(gold_groups) for l in g}
    tool_of = {l.upper(): c for c, labs in characters.items() for l in labs}
    labels = [l for l in gold_of if l in tool_of]
    tp = fp = fn = 0
    wrong = []
    for a, b in combinations(labels, 2):
        g, t = gold_of[a] == gold_of[b], tool_of[a] == tool_of[b]
        if g and t: tp += 1
        elif t and not g: fp += 1; wrong.append(f"{a} + {b}")
        elif g and not t: fn += 1
    return {"labels_scored": len(labels), "merge_precision": tp / (tp + fp) if tp + fp else 1.0,
            "merge_recall": tp / (tp + fn) if tp + fn else 1.0, "wrong_merges": fp, "wrong_merge_pairs": wrong, "missed_merges": fn}


def read_gold_aliases(path: Path) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("character"):
                groups.setdefault(r["character"].strip().lower(), []).append(r["label"].strip().upper())
    return list(groups.values())
