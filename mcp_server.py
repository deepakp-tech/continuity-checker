"""Read-only MCP server over a show's story bible.

Lets an AI writing tool (Claude Desktop, Cursor, an in-house script agent) check a draft scene
against everything already established BEFORE the episode is written - prevention, not just detection.
All tools are read-only: nothing here can change the bible.

    python -m cc mcp --out outputs/my_show          # stdio transport
Claude Desktop config example (claude_desktop_config.json):
    {"mcpServers": {"continuity": {"command": "python", "args": ["-m", "cc", "mcp", "--out", "/abs/path/outputs/my_show"],
                                   "cwd": "/abs/path/continuity-checker"}}}
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:                                   # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except Exception:                      # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server   # type: ignore

from cc.aliases import AliasResult
from cc.bible import Bible
from cc.extract import extract_episode, verify_facts
from cc.llm import LLM
from cc.parsing import KukuAdapter
from cc.pipeline import load_config, load_profile
from cc.schema import Episode, Line
from cc.textmatch import ratio


def build_server(bible_path: Path, config_path: str | None = None):
    bible = Bible(bible_path)
    names = AliasResult.from_bible(bible)
    cfg = load_config(config_path)
    llm = LLM(cfg, cache_path=ROOT / ".cache" / "llm_cache.sqlite")
    profile = load_profile(bible.meta("genre", "generic") or "generic")
    srv = _Server("continuity-checker")

    @srv.tool()
    def get_character(name: str) -> dict:
        """Everything established about a character (verified facts with episode, line id and quote),
        plus the speaker labels merged into this character. Read-only."""
        canon, ok = names.resolve_name(name, 10**6)
        facts = bible.character_facts(canon)
        return {"character": canon, "resolved": ok, "labels": names.characters.get(canon, []),
                "facts": facts[:200], "fact_count": len(facts)}

    @srv.tool()
    def get_facts(entity: str, fact_type: str = "") -> list[dict]:
        """Verified facts about a character or object. fact_type: relationship|status|identity|possession|knowledge|time (optional)."""
        canon, _ = names.resolve_name(entity, 10**6)
        rows = bible.q("SELECT subject, type, attribute, value, source, episode, line_id, quote FROM facts WHERE verified=1 "
                       "AND (subject=? OR attribute=? OR attribute LIKE ?) ORDER BY episode, ord",
                       (canon, canon, f"%{entity.lower()}%"))
        return [r for r in rows if not fact_type or r["type"] == fact_type][:300]

    @srv.tool()
    def list_open_flags(limit: int = 20) -> list[dict]:
        """Open continuity flags (contradiction or unsure), most severe first."""
        rows = bible.q("SELECT flag_id FROM flags WHERE status='open' AND verdict IN ('contradiction','unsure') "
                       "ORDER BY CASE verdict WHEN 'contradiction' THEN 0 ELSE 1 END, "
                       "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = bible.flag_detail(r["flag_id"])
            out.append({"flag_id": d["flag_id"], "verdict": d["verdict"], "severity": d["severity"], "summary": d["note"],
                        "reason": d["reason"], "a": {k: (d.get("a") or {}).get(k) for k in ("episode", "line_id", "quote")},
                        "b": {k: (d.get("b") or d.get("line_b_row") or {}).get(k) for k in ("episode", "line_id", "quote", "text")}})
        return out

    @srv.tool()
    def check_scene(scene_text: str, episode: int) -> dict:
        """Check a DRAFT scene against the story bible before it is written into the show.
        scene_text: Kuku block format, or simple lines like 'ANNA: I never had a sister.' and plain action lines.
        Returns facts found in the draft and any that conflict with established facts (with the quotes they clash with).
        Read-only: the draft is not saved."""
        lines = _scene_lines(scene_text, episode)
        ep = Episode(episode=episode, lines=lines)
        pc = cfg.get("pipeline", {})
        raw = extract_episode(ep, names, llm, f"{profile.get('name')}: {profile.get('description', '')}", pc,
                              "\n".join(f"- {c}" for c in list(names.characters)[:60]))
        facts = [f for f in verify_facts(raw, names, llm, float(pc.get("quote_match_threshold", 90)), False, llm.describe("extract")) if f["verified"]]
        conflicts = []
        for f in facts:
            for old in bible.q("SELECT * FROM facts WHERE verified=1 AND subject=? AND type=? AND episode<=?", (f["subject"], f["type"], episode)):
                same_attr = old["attribute"] == f["attribute"] or ratio(old["attribute"], f["attribute"]) >= 85
                if same_attr and old["value"] != f["value"] and f["type"] != "location":
                    conflicts.append({"draft": {k: f[k] for k in ("subject", "type", "attribute", "value", "quote")},
                                      "established": {k: old[k] for k in ("episode", "line_id", "value", "quote", "source")}})
        speakers = {names.resolve_label(l.speaker, episode) for l in lines if l.type == "dialogue"}
        for s in speakers:
            dead = bible.q("SELECT episode, line_id, quote FROM facts WHERE verified=1 AND subject=? AND type='status' "
                           "AND value='dead' AND source='action' AND episode<=? LIMIT 1", (s, episode))
            if dead:
                conflicts.append({"draft": {"subject": s, "type": "status", "value": "speaks"}, "established": {**dead[0], "value": "dead"}})
        return {"facts_in_draft": facts, "conflicts": conflicts,
                "note": "Conflicts are candidates; story changes (a recovery, a divorce) can be legitimate. Run the full check for verdicts."}

    return srv


def _scene_lines(text: str, episode: int) -> list[Line]:
    if re.search(r"^\*\*[^*]+:\*\*\s*$", text, re.M):
        ep, _, _ = KukuAdapter().parse_file("draft", f"EPISODE NUMBER: Episode {episode}\n" + text, episode)
        for k, l in enumerate(ep.lines, 1):
            l.line_id = f"DRAFT-L{k:04d}"
        return [l for l in ep.lines if l.type != "camera"]
    out = []
    for k, raw in enumerate([r.strip() for r in text.splitlines() if r.strip()], 1):
        m = re.match(r"^([A-Z][A-Z .'\-]{1,40}?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$", raw)
        if m:
            out.append(Line(f"DRAFT-L{k:04d}", episode, 1, "", f"L{k}", f"L{k}", "dialogue", m.group(1).strip().upper(),
                            m.group(2) or "", m.group(3).strip().strip('"')))
        else:
            out.append(Line(f"DRAFT-L{k:04d}", episode, 1, "", f"L{k}", f"L{k}", "action", "", "", raw))
    return out


def serve(bible_path: Path, config_path: str | None = None):
    build_server(bible_path, config_path).run()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--config")
    a = ap.parse_args()
    serve(Path(a.out) / "bible.sqlite", a.config)
