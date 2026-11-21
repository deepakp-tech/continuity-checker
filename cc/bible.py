"""Stage 4: the story bible - one SQLite file per show/run.
Also stores editor decisions so they survive re-runs and feed the regression tests."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (episode INTEGER PRIMARY KEY, title TEXT, logline TEXT, continuity_notes TEXT);
CREATE TABLE IF NOT EXISTS lines (line_id TEXT PRIMARY KEY, episode INTEGER, scene INTEGER, scene_heading TEXT,
    ts_start TEXT, ts_end TEXT, type TEXT, speaker TEXT, character TEXT, emotion TEXT, text TEXT, ord INTEGER);
CREATE TABLE IF NOT EXISTS characters (name TEXT PRIMARY KEY, labels TEXT);
CREATE TABLE IF NOT EXISTS alias_pairs (label_a TEXT, label_b TEXT, status TEXT, verdict TEXT, confidence REAL,
    co_scenes INTEGER, evidence TEXT, reason TEXT, PRIMARY KEY (label_a, label_b));
CREATE TABLE IF NOT EXISTS facts (fact_id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, subject_raw TEXT,
    subject_resolved INTEGER, type TEXT, attribute TEXT, attribute_raw TEXT, value TEXT, source TEXT, speaker TEXT,
    line_id TEXT, episode INTEGER, ord INTEGER, quote TEXT, quote_score REAL, entailment TEXT, verified INTEGER,
    drop_reason TEXT, model TEXT);
CREATE TABLE IF NOT EXISTS flags (flag_id INTEGER PRIMARY KEY AUTOINCREMENT, rule TEXT, fact_a INTEGER, fact_b INTEGER,
    line_b TEXT, verdict TEXT, severity TEXT, reason TEXT, reason_verified INTEGER, note TEXT, model TEXT,
    episode_max INTEGER, status TEXT DEFAULT 'open', payload TEXT);
CREATE TABLE IF NOT EXISTS decisions (flag_id INTEGER, decision TEXT, editor_note TEXT, ts REAL, payload TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class Bible:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------------ writes
    def reset_derived(self, keep_facts: bool = False):
        """Clear tables that are recomputed each run (facts kept in incremental mode)."""
        self.db.execute("DELETE FROM alias_pairs")
        self.db.execute("DELETE FROM characters")
        if not keep_facts:
            self.db.execute("DELETE FROM facts")
            self.db.execute("DELETE FROM flags")
        self.db.commit()

    def save_episodes(self, episodes, label_resolver):
        cur = self.db.cursor()
        for e in episodes:
            cur.execute("INSERT OR REPLACE INTO episodes VALUES (?,?,?,?)", (e.episode, e.title, e.logline, e.continuity_notes))
            for k, l in enumerate(e.lines):
                ch = label_resolver(l.speaker, l.episode) if l.speaker else ""
                cur.execute("INSERT OR REPLACE INTO lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (l.line_id, l.episode, l.scene, l.scene_heading, l.ts_start, l.ts_end, l.type,
                             l.speaker, ch, l.emotion, l.text, k))
        self.db.commit()

    def save_aliases(self, res):
        cur = self.db.cursor()
        for c, labs in res.characters.items():
            cur.execute("INSERT OR REPLACE INTO characters VALUES (?,?)", (c, json.dumps(labs)))
        for p in res.pairs:
            cur.execute("INSERT OR REPLACE INTO alias_pairs VALUES (?,?,?,?,?,?,?,?)",
                        (p.a, p.b, p.status, p.verdict, p.confidence, p.co_scenes,
                         json.dumps({"address": p.address_hits[:10], "action": p.action_hits[:10],
                                     "eps_a": p.eps_a, "eps_b": p.eps_b}), p.reason))
        self.db.commit()

    def add_fact(self, f: dict) -> int:
        cols = ["subject", "subject_raw", "subject_resolved", "type", "attribute", "attribute_raw", "value", "source",
                "speaker", "line_id", "episode", "ord", "quote", "quote_score", "entailment", "verified", "drop_reason", "model"]
        cur = self.db.execute(f"INSERT INTO facts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [f.get(c) for c in cols])
        return cur.lastrowid

    def add_flag(self, fl: dict) -> int:
        cols = ["rule", "fact_a", "fact_b", "line_b", "verdict", "severity", "reason", "reason_verified", "note", "model", "episode_max", "payload"]
        cur = self.db.execute(f"INSERT INTO flags ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [fl.get(c) for c in cols])
        return cur.lastrowid

    def delete_flags_for_episodes(self, eps: list[int]):
        if eps:
            self.db.execute(f"DELETE FROM flags WHERE episode_max IN ({','.join('?' * len(eps))})", eps)
            self.db.commit()

    def decide(self, flag_id: int, decision: str, note: str = "", feedback_log: Path | None = None):
        """Editor decision: accepted | dismissed | intentional. Logged for regression tests."""
        payload = json.dumps(self.flag_detail(flag_id), ensure_ascii=False)
        self.db.execute("INSERT INTO decisions VALUES (?,?,?,?,?)", (flag_id, decision, note, time.time(), payload))
        self.db.execute("UPDATE flags SET status=? WHERE flag_id=?", (decision, flag_id))
        self.db.commit()
        if feedback_log:
            with open(feedback_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"decision": decision, "note": note, "flag": json.loads(payload)}, ensure_ascii=False) + "\n")

    def set_meta(self, k: str, v):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(v)))
        self.db.commit()

    def commit(self):
        self.db.commit()

    # ------------------------------------------------------------------ reads
    def q(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def meta(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def episodes_present(self) -> set[int]:
        return {r["episode"] for r in self.q("SELECT DISTINCT episode FROM facts")}

    def fact(self, fid: int) -> dict:
        r = self.q("SELECT * FROM facts WHERE fact_id=?", (fid,))
        return r[0] if r else {}

    def line(self, line_id: str) -> dict:
        r = self.q("SELECT * FROM lines WHERE line_id=?", (line_id,))
        return r[0] if r else {}

    def context(self, line_id: str, before: int = 2, after: int = 2) -> list[dict]:
        l = self.line(line_id)
        if not l:
            return []
        rows = self.q("SELECT * FROM lines WHERE episode=? AND type!='camera' ORDER BY ord", (l["episode"],))
        idx = next((i for i, r in enumerate(rows) if r["ord"] >= l["ord"]), len(rows) - 1)
        return rows[max(0, idx - before): idx + after + 1]

    def flag_detail(self, flag_id: int) -> dict:
        fl = self.q("SELECT * FROM flags WHERE flag_id=?", (flag_id,))
        if not fl:
            return {}
        fl = fl[0]
        fl["a"] = self.fact(fl["fact_a"]) if fl["fact_a"] else {}
        fl["b"] = self.fact(fl["fact_b"]) if fl["fact_b"] else {}
        fl["line_b_row"] = self.line(fl["line_b"]) if fl.get("line_b") else {}
        return fl

    def character_facts(self, name: str) -> list[dict]:
        return self.q("SELECT type, attribute, value, source, episode, line_id, quote FROM facts "
                      "WHERE verified=1 AND (subject=? OR attribute=?) ORDER BY episode, ord", (name, name))
