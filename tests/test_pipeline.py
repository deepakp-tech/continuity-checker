"""Offline tests. The LLM code path is exercised against a local fake OpenAI-compatible server,
including a 429 retry, JSON parsing, caching, a hallucinated quote and a judge that invents evidence."""
from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cc.aliases import resolve_aliases                         # noqa: E402
from cc.llm import LLM, parse_json_loose                       # noqa: E402
from cc.parsing import parse_sources                           # noqa: E402
from cc.pipeline import load_config, run                       # noqa: E402

KUKU = """EPISODE NUMBER: Episode {ep}
EPISODE LOGLINE: Test.
CONTINUITY NOTES: Follows Episode {prev}.

--- SCRIPT START ---

SCENE 01
Scene heading: INT. ROOM – DAY
Scene start: 00:00.00
Scene end: 00:20.00

Actions and dialogue:

**CAMERA:**
Start: (00:00.00) | End: (00:02.00)
Wide shot.

{blocks}
--- SCRIPT END ---"""


def block(label, text, t=1):
    q = text if label in ("ACTION", "CAMERA", "SOUND FX") else f'"{text}"'
    return f"**{label}:**\nStart: (00:0{t}.00) | End: (00:0{t}.50)\n{q}\n"


def show(episodes: dict[int, list[tuple[str, str]]]):
    return [(f"episode-{ep:03d}.txt", KUKU.format(ep=ep, prev=ep - 1, blocks="\n".join(block(l, t) for l, t in bl)))
            for ep, bl in sorted(episodes.items())]


# ------------------------------------------------------------------------------------------- parsing
def test_kuku_parse_full_coverage_and_double_parenthetical():
    src = show({1: [("NARRATOR (O.S.) (Calm)", "Nora is the pride of this family."), ("ACTION", "Mia waits.")]})
    pr = parse_sources(src)
    assert pr.format_name.startswith("kuku") and pr.coverage > 0.99
    d = [l for l in pr.all_lines() if l.type == "dialogue"][0]
    assert d.speaker == "NARRATOR" and d.emotion == "O.S., Calm" and d.text.startswith("Nora")


def test_screenplay_adapter_detected():
    txt = "EPISODE 1: Pilot\nLOGLINE: x\n\nINT. HOUSE - NIGHT\n\nMeera waits.\n\nMEERA\n(quiet)\nArjun is my brother.\n\nEXT. ROAD - DAY\n\nRain."
    pr = parse_sources([("ep1.txt", txt)])
    assert pr.format_name.startswith("screenplay")
    d = [l for l in pr.all_lines() if l.type == "dialogue"][0]
    assert d.speaker == "MEERA" and d.emotion == "quiet" and d.ts_start.startswith("L")


# ------------------------------------------------------------------------------------------- names
def test_same_scene_veto_and_possessive_label():
    src = show({1: [("LORD HALE", "Hello."), ("LADY HALE", "Hello, Hale.")],
                2: [("TOM'S MOTHER", "Tom, come here."), ("ACTION", "Tom's mother waits.")],
                3: [("TOM", "Mother."), ("ACTION", "Tom bows.")]})
    res = resolve_aliases(parse_sources(src).episodes, llm=None, log=lambda *_: None)
    assert res.label_to_char["LORD HALE"] != res.label_to_char["LADY HALE"]
    assert res.label_to_char["TOM'S MOTHER"] != res.label_to_char["TOM"]


def test_relabel_merges_and_scoped_human_decision(tmp_path):
    src = show({1: [("JULIA", "I am here."), ("ACTION", "Julia smiles.")],
                2: [("LADY JULIA", "Again."), ("ACTION", "Julia waits for Julia's maid.")],
                3: [("THE DUKE", "Kneel."), ("ACTION", "The Duke rises.")]})
    dec = tmp_path / "d.csv"
    dec.write_text("label_a,label_b,decision,ep_from,ep_to\nJULIA,THE DUKE,same,3,3\n")
    res = resolve_aliases(parse_sources(src).episodes, llm=None, decisions_path=dec, log=lambda *_: None)
    assert res.label_to_char["JULIA"] == res.label_to_char["LADY JULIA"]
    assert res.resolve_label("THE DUKE", 3) == res.label_to_char["JULIA"]
    assert res.resolve_label("THE DUKE", 9) != res.label_to_char["JULIA"]


# ------------------------------------------------------------------------------------------- fake LLM server
class FakeAPI(BaseHTTPRequestHandler):
    calls = {"n": 0, "429_sent": False}

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAPI.calls["n"] += 1
        if not FakeAPI.calls["429_sent"]:
            FakeAPI.calls["429_sent"] = True
            self.send_response(429); self.send_header("retry-after", "0"); self.end_headers(); return
        prompt = body["messages"][-1]["content"]
        out = self.answer(prompt)
        data = {"choices": [{"message": {"content": out}}], "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 50}}
        raw = json.dumps(data).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    @staticmethod
    def answer(p: str) -> str:
        if p.startswith("Extract story FACTS"):
            facts = []
            for lid, spk, text in re.findall(r"\[(E\d+-S\d+-L\d+)\] ([A-Z .']+): (.*)", p):
                m = re.search(r"(\w+) is my (sister|cousin)", text)
                if m:
                    facts.append({"subject": m.group(1), "type": "relationship", "attribute": spk.title(), "value": m.group(2),
                                  "source": "dialogue", "speaker": spk, "line_id": lid, "quote": m.group(0)})
                if "burns the letter" in text:
                    facts.append({"subject": "Mia", "type": "possession", "attribute": "letter", "value": "destroyed",
                                  "source": "action", "line_id": lid, "quote": "burns the letter"})
            # a hallucinated fact: quote does not exist anywhere
            facts.append({"subject": "Mia", "type": "identity", "attribute": "job", "value": "spy", "source": "action",
                          "line_id": "E001-S01-L0002", "quote": "Mia secretly works for the crown"})
            return "```json\n" + json.dumps({"facts": facts}) + "\n```"     # fenced on purpose
        if p.startswith("For each item"):
            ids = re.findall(r'"id": (\d+)', p)
            return json.dumps({"results": [{"id": int(i), "answer": "yes"} for i in ids]})
        if p.startswith("Two facts"):
            if "| letter =" in p:   # judge invents a quote -> must be downgraded
                return json.dumps({"verdict": "contradiction", "severity": "high", "reason": 'She says "I kept the letter in my boot all along".'})
            return json.dumps({"verdict": "contradiction", "severity": "high", "reason": 'Ep 1 says "Rae is my sister" but later "Rae is my cousin".'})
        if p.startswith("Two speaker labels"):
            return json.dumps({"verdict": "same", "confidence": 0.95, "evidence_line_ids": [], "reason": "names match"})
        if p.startswith("The writer's CONTINUITY NOTES"):
            return json.dumps({"claims": []})
        return "{}"


@pytest.fixture
def fake_api():
    srv = HTTPServer(("127.0.0.1", 0), FakeAPI)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def fake_cfg(url):
    cfg = load_config(ROOT / "config.yaml")
    cfg["providers"] = {"fake": {"base_url": url, "api_key_env": "", "json_mode": True, "min_seconds_between_calls": 0}}
    cfg["stages"] = {k: {"provider": "fake", "model": "m"} for k in ("default", "parse", "alias", "extract", "entail", "judge", "notes")}
    cfg["retry"] = {"max_attempts": 3, "base_seconds": 0.01}
    return cfg


def test_llm_path_end_to_end(fake_api, tmp_path, monkeypatch):
    import cc.pipeline as pl
    monkeypatch.setattr(pl, "ROOT", tmp_path)          # isolated cache
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "generic.yaml").write_text((ROOT / "profiles" / "generic.yaml").read_text())
    src = show({1: [("MIA", "Rae is my sister."), ("ACTION", "Mia burns the letter.")],
                2: [("MIA", "Rae is my cousin."), ("ACTION", "Mia holds the letter.")]})
    s = run("x", tmp_path / "out", cfg=fake_cfg(fake_api), genre="generic", sources=src, log=lambda *_: None)
    from cc.bible import Bible
    b = Bible(tmp_path / "out" / "bible.sqlite")
    facts = b.q("SELECT * FROM facts")
    assert any(f["verified"] == 0 and "quote not found" in f["drop_reason"] for f in facts), "hallucinated quote must be dropped"
    assert any(f["verified"] == 1 and f["value"] == "sister" for f in facts)
    flags = b.q("SELECT * FROM flags")
    rel = [f for f in flags if "sister" in (f["note"] or "")]
    assert rel and rel[0]["verdict"] == "contradiction" and rel[0]["reason_verified"] == 1
    assert s["llm_stats"]["calls"] > 0 and FakeAPI.calls["429_sent"]
    # second run: everything comes from the cache
    n_before = FakeAPI.calls["n"]
    run("x", tmp_path / "out2", cfg=fake_cfg(fake_api), genre="generic", sources=src, log=lambda *_: None)
    assert FakeAPI.calls["n"] == n_before


def test_judge_invented_quote_is_downgraded(fake_api, tmp_path, monkeypatch):
    import cc.pipeline as pl
    monkeypatch.setattr(pl, "ROOT", tmp_path)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "generic.yaml").write_text((ROOT / "profiles" / "generic.yaml").read_text())
    cfg = fake_cfg(fake_api)
    cfg["stages"]["extract"] = {"provider": "rules"}     # rules find 'burns the letter' / 'holds the letter'
    src = show({1: [("ACTION", "Mia burns the letter.")], 2: [("ACTION", "Mia holds the letter.")]})
    run("x", tmp_path / "o", cfg=cfg, genre="generic", sources=src, log=lambda *_: None)
    from cc.bible import Bible
    fl = Bible(tmp_path / "o" / "bible.sqlite").q("SELECT * FROM flags WHERE rule='value_change'")
    assert fl and fl[0]["verdict"] == "unsure" and fl[0]["reason_verified"] == 0


def test_parse_json_loose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! Here it is: {"a": 2} hope that helps') == {"a": 2}
    assert parse_json_loose("no json") is None


def test_rules_mode_on_synthetic_show(tmp_path):
    s = run(ROOT / "eval" / "synthetic" / "contract_heiress", tmp_path / "o", genre="billionaire_romance",
            use_rules_only=True, log=lambda *_: None)
    assert s["coverage"] > 0.99 and s["merges_auto"] == 2 and s["merges_vetoed_same_scene"] >= 1
