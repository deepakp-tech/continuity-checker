"""Plant known continuity errors (and harmless 'trap' changes) into a COPY of a Kuku-format show.

    python eval/plant_errors.py --input <show folder> --out <new folder> [--seed 7] [--per-type 2]

Writes the mutated scripts plus answer_key.json (same format as the synthetic keys).
Keep the output folder OUTSIDE the git repo when the input is confidential.

Honest limitation: planted errors are inserted ACTION sentences (several phrasings per type, some only a
model can parse). They are still easier than organic mistakes, so they measure recall on known error
types, not on everything - label the flags on the ORIGINAL pack too.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cc.aliases import resolve_aliases            # noqa: E402
from cc.parsing import load_sources, parse_sources  # noqa: E402

BLOCK = re.compile(r"\*\*ACTION:\*\*\nStart: \(([\d:.]+)\) \| End: \(([\d:.]+)\)\n[^\n]*\n")
OBJECTS = ["ledger", "signet ring", "silver locket", "sealed letter", "brass key"]
SECRETS = ["the estate deed was forged", "the steward had been paid to lie", "the royal seal was stolen"]
NUMBERS = [("twenty-four", "thirty-one"), ("twenty-six", "thirty-three"), ("thirty", "forty-one")]
RELS = [("cousin", "uncle"), ("cousin", "stepbrother"), ("uncle", "cousin")]

# Several phrasings per error type: the first is regex-friendly, the others are natural prose that only a
# model that understands the sentence will catch. This keeps the benchmark from rewarding pattern matching.
T = {
    "age1": ["{A} is {x} years old.", "{A}, barely {x}, already carries the weight of the whole house.", "At only {x}, {A} has outlived most of the family."],
    "age2": ["{A} is {y} years old.", "At {y}, {A} is older than anyone at the table guessed.", "The parish register lists {A}'s age as {y}."],
    "rel": ["{B} is {A}'s {r}.", "Everyone at court knows {B} as {A}'s {r}.", "{B}, {A}'s {r} by blood, says nothing."],
    "burn": ["{A} burns the {obj} in the hearth.", "The {obj} curls into ash as {A} feeds it to the fire."],
    "hold": ["{A} holds the {obj} up to the candlelight.", "{A} slides the {obj}, perfectly intact, across the table."],
    "learn": ["{A} learns that {sec}.", "It finally dawns on {A}: {sec}."],
    "die": ["{C} dies quietly in the night, and the household mourns.", "By dawn {C} is dead; the bells toll across the valley."],
    "hurt": ["{A} is badly injured in the struggle.", "{A} limps away from the struggle, bleeding heavily."],
    "heal": ["{A} has recovered from the wounds.", "{A}'s wounds have healed and the limp is gone."],
    "wear": ["{A} wears the jade bracelet.", "A jade bracelet glints on {A}'s wrist."],
    "lose": ["{A} loses the jade bracelet in the crowd.", "Somewhere in the crowd, {A}'s jade bracelet slips away for good."],
}


def say(rng, kind, **kw):
    return rng.choice(T[kind]).format(**kw)


def insert_action(text: str, sentence: str, where: str, rng: random.Random) -> str:
    blocks = list(BLOCK.finditer(text))
    if not blocks:
        raise ValueError("no ACTION block to anchor the insertion")
    b = blocks[-1] if where == "end" else blocks[rng.randrange(0, max(1, len(blocks) - 1))]
    new = f"\n**ACTION:**\nStart: ({b.group(1)}) | End: ({b.group(2)})\n{sentence}\n"
    return text[:b.end()] + new + text[b.end():]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=7); ap.add_argument("--per-type", type=int, default=2)
    ap.add_argument("--genre", default="period_drama")
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)
    sources = load_sources(a.input)
    pr = parse_sources(sources)
    names = resolve_aliases(pr.episodes, llm=None, log=lambda *_: None)
    eps = sorted(e.episode for e in pr.episodes)
    # main characters: speak in many episodes, single-word display name usable in prose
    speak_eps: dict[str, set[int]] = {}
    for e in pr.episodes:
        for l in e.lines:
            if l.type == "dialogue":
                speak_eps.setdefault(names.resolve_label(l.speaker, e.episode), set()).add(e.episode)
    mains = sorted([c for c, s in speak_eps.items() if len(s) >= 6 and len(c.split()) == 1], key=lambda c: -len(speak_eps[c]))
    if len(mains) < 3:
        raise SystemExit("not enough recurring characters to plant errors")
    files = {}
    by_ep = {}
    for name, text in sources:
        ep = next((e.episode for e in parse_sources([(name, text)]).episodes), None)
        files[name] = text
        by_ep[ep] = name
    expected, log = [], []

    def two_eps(gap=3):
        i = rng.choice([e for e in eps if e + gap <= eps[-1]])
        j = rng.choice([e for e in eps if e >= i + gap])
        return i, j

    def plant(ep, sentence, where="mid"):
        files[by_ep[ep]] = insert_action(files[by_ep[ep]], sentence, where, rng)
        log.append({"episode": ep, "inserted": sentence})

    n = 0
    for k in range(a.per_type):
        A, B = rng.sample(mains, 2)
        i, j = two_eps(); x, y = NUMBERS[k % len(NUMBERS)]
        plant(i, say(rng, "age1", A=A, x=x)); plant(j, say(rng, "age2", A=A, y=y))
        n += 1; expected.append({"id": f"P{n}", "kind": "error", "type": "identity", "subjects": [A], "episodes": [i, j], "desc": f"{A} age {x} then {y}"})
        i, j = two_eps(); r1, r2 = RELS[k % len(RELS)]
        plant(i, say(rng, "rel", A=A, B=B, r=r1)); plant(j, say(rng, "rel", A=A, B=B, r=r2))
        n += 1; expected.append({"id": f"P{n}", "kind": "error", "type": "relationship", "subjects": [A, B], "episodes": [i, j], "desc": f"{B} is {A}'s {r1} then {r2}"})
        i, j = two_eps(); obj = OBJECTS[k % len(OBJECTS)]
        plant(i, say(rng, "burn", A=A, obj=obj)); plant(j, say(rng, "hold", A=A, obj=obj))
        n += 1; expected.append({"id": f"P{n}", "kind": "error", "type": "possession", "subjects": [A], "episodes": [i, j], "desc": f"{obj} burned then held"})
        i, j = two_eps(); sec = SECRETS[k % len(SECRETS)]
        plant(i, say(rng, "learn", A=A, sec=sec)); plant(j, say(rng, "learn", A=A, sec=sec))
        n += 1; expected.append({"id": f"P{n}", "kind": "error", "type": "knowledge", "subjects": [A], "episodes": [i, j], "desc": f"learns '{sec}' twice"})
        # death: pick a character who speaks in a later episode
        C = rng.choice(mains)
        cand = [e for e in eps if any(x > e for x in speak_eps[C])]
        d = rng.choice(cand[: max(1, len(cand) * 2 // 3)])
        nxt = min(x for x in speak_eps[C] if x > d)
        plant(d, say(rng, "die", C=C), where="end")
        n += 1; expected.append({"id": f"P{n}", "kind": "error", "type": "status", "subjects": [C], "episodes": sorted({d, nxt}), "desc": f"{C} dies in ep {d}, speaks in ep {nxt}"})
    # traps (legitimate changes - must NOT be flagged as contradictions)
    A = rng.choice(mains); i, j = two_eps()
    plant(i, say(rng, "hurt", A=A)); plant(j, say(rng, "heal", A=A))
    expected.append({"id": "T1", "kind": "trap", "type": "status", "subjects": [A], "episodes": [i, j], "desc": "injured then recovered"})
    A = rng.choice(mains); i, j = two_eps()
    plant(i, say(rng, "wear", A=A)); plant(j, say(rng, "lose", A=A))
    expected.append({"id": "T2", "kind": "trap", "type": "possession", "subjects": [A], "episodes": [i, j], "desc": "has bracelet then loses it"})

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    key = {"show": Path(a.input).name, "format": "kuku", "genre": a.genre, "seed": a.seed, "alias_groups": [],
           "expected": expected, "insertions": log}
    (out.parent / f"{out.name}_answer_key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"planted {sum(e['kind']=='error' for e in expected)} errors and {sum(e['kind']=='trap' for e in expected)} traps -> {out}")
    print(f"answer key -> {out.parent / (out.name + '_answer_key.json')}")


if __name__ == "__main__":
    main()
