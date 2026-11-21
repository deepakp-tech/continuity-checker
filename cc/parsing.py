"""Stage 0: load scripts, detect the format, parse into the internal Line format,
and measure parse coverage.

Adapters:
  * KukuAdapter        - Kuku's timestamped block format (**LABEL (emotion):** / Start: (..) | End: (..))
  * ScreenplayAdapter  - standard screenplay layout (INT./EXT. headings, CAPS character cues)
  * LLMFallbackAdapter - any other layout; an LLM converts it, and every returned line is
                         checked against the source text so invented lines are dropped
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Optional

from .schema import Episode, Line, ParseResult
from .textmatch import norm

TEXT_SUFFIXES = {".txt", ".md", ".fountain", ".text"}


# --------------------------------------------------------------------------- loading
def load_sources(path: str | Path) -> list[tuple[str, str]]:
    """Return [(name, text)] from a folder, a single file, or a .zip, sorted by episode number."""
    p = Path(path)
    items: list[tuple[str, str]] = []
    if p.is_file() and p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if Path(n).suffix.lower() in TEXT_SUFFIXES and not Path(n).name.startswith("."):
                    items.append((Path(n).name, z.read(n).decode("utf-8", errors="replace")))
    elif p.is_file():
        items.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
    elif p.is_dir():
        for f in p.rglob("*"):
            if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES and not f.name.startswith("."):
                items.append((f.name, f.read_text(encoding="utf-8", errors="replace")))
    else:
        raise FileNotFoundError(path)
    return sorted(items, key=lambda it: (_first_int(it[0]) if _first_int(it[0]) is not None else 10**9, it[0]))


def load_uploaded_zip(data: bytes) -> list[tuple[str, str]]:
    tmp = io.BytesIO(data)
    out = []
    with zipfile.ZipFile(tmp) as z:
        for n in z.namelist():
            if Path(n).suffix.lower() in TEXT_SUFFIXES and not Path(n).name.startswith("."):
                out.append((Path(n).name, z.read(n).decode("utf-8", errors="replace")))
    return sorted(out, key=lambda it: (_first_int(it[0]) or 10**9, it[0]))


def _first_int(s: str) -> Optional[int]:
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None


def _nonblank_chars(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


# --------------------------------------------------------------------------- Kuku adapter
class KukuAdapter:
    name = "kuku_timestamped_v1"
    # label followed by zero or more parentheticals: **NARRATOR (O.S.) (Calm):**
    BLOCK = re.compile(r"^\*\*(?P<label>[^*(]+?)\s*(?P<emo>(?:\([^)]*\)\s*)*):\*\*\s*$")
    TIMING = re.compile(r"^Start:\s*\(([\d:.]+)\)\s*\|\s*End:\s*\(([\d:.]+)\)\s*$")
    TYPE_MAP = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sfx", "SFX": "sfx"}

    def score(self, text: str) -> float:
        blocks = len(re.findall(r"^\*\*[^*]+:\*\*\s*$", text, re.M))
        timings = len(re.findall(r"^Start:\s*\(", text, re.M))
        return min(1.0, (blocks + timings) / 20) if blocks and timings else 0.0

    def parse_file(self, name: str, text: str, fallback_ep: int) -> tuple[Episode, int, list[str]]:
        rows = text.splitlines()
        consumed = [False] * len(rows)
        ep_no = fallback_ep
        ep = Episode(episode=ep_no, title=name)
        scene_no, heading = 0, ""
        line_counter = 0
        i = 0
        while i < len(rows):
            r = rows[i].strip()
            if not r:
                consumed[i] = True
                i += 1
                continue
            m_hdr = re.match(r"^EPISODE NUMBER:\s*(.*)$", r, re.I)
            if m_hdr:
                n = _first_int(m_hdr.group(1))
                if n is not None:
                    ep.episode = n
                consumed[i] = True
            elif r.upper().startswith("EPISODE LOGLINE:"):
                ep.logline = r.split(":", 1)[1].strip(); consumed[i] = True
            elif r.upper().startswith("CONTINUITY NOTES:"):
                ep.continuity_notes = r.split(":", 1)[1].strip(); consumed[i] = True
            elif re.match(r"^---\s*SCRIPT (START|END)\s*---$", r, re.I) or r.lower() == "actions and dialogue:":
                consumed[i] = True
            elif re.match(r"^SCENE\s+\d+", r, re.I):
                scene_no = _first_int(r) or scene_no + 1; consumed[i] = True
            elif r.lower().startswith("scene heading:"):
                heading = r.split(":", 1)[1].strip(); consumed[i] = True
            elif r.lower().startswith(("scene start:", "scene end:")):
                consumed[i] = True
            else:
                m = self.BLOCK.match(r)
                if m:
                    label = m.group("label").strip().upper()
                    emo = ", ".join(re.findall(r"\(([^)]*)\)", m.group("emo") or ""))
                    consumed[i] = True
                    j = i + 1
                    ts_s = ts_e = ""
                    if j < len(rows):
                        mt = self.TIMING.match(rows[j].strip())
                        if mt:
                            ts_s, ts_e = mt.group(1), mt.group(2)
                            consumed[j] = True
                            j += 1
                    body = []
                    while j < len(rows) and rows[j].strip() and not self.BLOCK.match(rows[j].strip()):
                        body.append(rows[j].strip()); consumed[j] = True; j += 1
                    text_body = " ".join(body).strip()
                    ltype = self.TYPE_MAP.get(label, "dialogue")
                    if ltype == "dialogue":
                        text_body = text_body.strip().strip('"').strip("“”").strip()
                    line_counter += 1
                    ep.lines.append(Line(
                        line_id=f"E{ep.episode:03d}-S{max(scene_no,1):02d}-L{line_counter:04d}",
                        episode=ep.episode, scene=max(scene_no, 1), scene_heading=heading,
                        ts_start=ts_s or f"L{line_counter}", ts_end=ts_e or f"L{line_counter}",
                        type=ltype, speaker=label if ltype == "dialogue" else "",
                        emotion=emo, text=text_body))
                    i = j
                    continue
            i += 1
        # line ids were built before the header number may have been read: rebuild them
        for k, ln in enumerate(ep.lines, 1):
            ln.episode = ep.episode
            ln.line_id = f"E{ep.episode:03d}-S{ln.scene:02d}-L{k:04d}"
        parsed = sum(_nonblank_chars(rows[k]) for k in range(len(rows)) if consumed[k])
        unparsed = [rows[k].strip() for k in range(len(rows)) if not consumed[k] and rows[k].strip()]
        return ep, parsed, unparsed


# --------------------------------------------------------------------------- screenplay adapter
class ScreenplayAdapter:
    """Standard screenplay layout. No timestamps, so line positions ('L12') are used."""
    name = "screenplay_v1"
    HEADING = re.compile(r"^(INT\.|EXT\.|INT/EXT\.|I/E\.)\s*(.+)$", re.I)
    EPISODE = re.compile(r"^(?:EPISODE|EP\.?)\s*(\d+)\b[:.\-\s]*(.*)$", re.I)
    CUE = re.compile(r"^([A-Z][A-Z0-9 .'\-]{0,40}?)(?:\s*\((?:V\.O\.|O\.S\.|CONT'D|O\.C\.)\))?$")
    PAREN = re.compile(r"^\((.+)\)$")

    def score(self, text: str) -> float:
        heads = len(re.findall(r"^\s*(INT\.|EXT\.)", text, re.M | re.I))
        return min(0.9, heads / 5) if heads else 0.0

    def parse_file(self, name: str, text: str, fallback_ep: int) -> tuple[Episode, int, list[str]]:
        rows = text.splitlines()
        eps: dict[int, Episode] = {}
        cur = Episode(episode=fallback_ep, title=name)
        eps[cur.episode] = cur
        scene_no, heading, counter = 0, "", 0
        parsed = 0
        unparsed: list[str] = []
        i = 0
        while i < len(rows):
            raw = rows[i]
            r = raw.strip()
            if not r:
                i += 1; continue
            me = self.EPISODE.match(r)
            if me and len(r) < 80:
                n = int(me.group(1))
                cur = eps.setdefault(n, Episode(episode=n, title=me.group(2).strip() or name))
                scene_no, counter = 0, 0
                parsed += _nonblank_chars(r); i += 1; continue
            if r.upper().startswith("LOGLINE:"):
                cur.logline = r.split(":", 1)[1].strip(); parsed += _nonblank_chars(r); i += 1; continue
            if r.upper().startswith("CONTINUITY NOTES:") or r.upper().startswith("NOTES:"):
                cur.continuity_notes = r.split(":", 1)[1].strip(); parsed += _nonblank_chars(r); i += 1; continue
            mh = self.HEADING.match(r)
            if mh:
                scene_no += 1; heading = r; parsed += _nonblank_chars(r); i += 1; continue
            mc = self.CUE.match(r)
            nxt = rows[i + 1].strip() if i + 1 < len(rows) else ""
            if mc and nxt and r.upper() == r and not r.endswith(".") and len(r.split()) <= 5:
                speaker = mc.group(1).strip()
                parsed += _nonblank_chars(r)
                j, emo, body = i + 1, "", []
                while j < len(rows) and rows[j].strip():
                    t = rows[j].strip()
                    mp = self.PAREN.match(t)
                    if mp and not body:
                        emo = mp.group(1)
                    else:
                        body.append(t)
                    parsed += _nonblank_chars(t); j += 1
                counter += 1
                cur.lines.append(self._line(cur.episode, scene_no, heading, counter, "dialogue", speaker, emo, " ".join(body)))
                i = j; continue
            # action paragraph
            body, j = [], i
            while j < len(rows) and rows[j].strip() and not self.HEADING.match(rows[j].strip()):
                t = rows[j].strip()
                if j > i and self.CUE.match(t) and t.upper() == t and len(t.split()) <= 5:
                    break
                body.append(t); parsed += _nonblank_chars(t); j += 1
            counter += 1
            cur.lines.append(self._line(cur.episode, scene_no, heading, counter, "action", "", "", " ".join(body)))
            i = j
        ep_list = sorted(eps.values(), key=lambda e: e.episode)
        ep_list = [e for e in ep_list if e.lines or e.logline]
        return ep_list, parsed, unparsed  # type: ignore[return-value]

    @staticmethod
    def _line(ep, scene, heading, k, typ, speaker, emo, text) -> Line:
        s = max(scene, 1)
        return Line(line_id=f"E{ep:03d}-S{s:02d}-L{k:04d}", episode=ep, scene=s, scene_heading=heading,
                    ts_start=f"L{k}", ts_end=f"L{k}", type=typ, speaker=speaker.upper(), emotion=emo,
                    text=text.strip().strip('"'))


# --------------------------------------------------------------------------- LLM fallback adapter
class LLMFallbackAdapter:
    name = "llm_fallback_v1"
    CHUNK = 5000

    def __init__(self, llm):
        self.llm = llm

    def parse_file(self, name: str, text: str, fallback_ep: int) -> tuple[list[Episode], int, list[str]]:
        from .prompts import PARSE_PROMPT
        eps: dict[int, Episode] = {}
        parsed = 0
        dropped: list[str] = []
        src_norm = norm(text)
        chunks = [text[i:i + self.CHUNK] for i in range(0, len(text), self.CHUNK)]
        counters: dict[int, int] = {}
        for ch in chunks:
            data = self.llm.complete_json(PARSE_PROMPT.format(default_episode=fallback_ep, chunk=ch), stage="parse")
            for item in (data or {}).get("lines", []):
                body = str(item.get("text", "")).strip()
                if not body:
                    continue
                if norm(body) not in src_norm:      # invented or rewritten line -> drop
                    dropped.append(body[:80]); continue
                ep_no = int(item.get("episode") or fallback_ep)
                ep = eps.setdefault(ep_no, Episode(episode=ep_no, title=name))
                counters[ep_no] = counters.get(ep_no, 0) + 1
                k = counters[ep_no]
                sc = int(item.get("scene") or 1)
                typ = item.get("type", "action")
                typ = typ if typ in ("action", "dialogue", "sfx", "camera") else "action"
                ep.lines.append(Line(line_id=f"E{ep_no:03d}-S{sc:02d}-L{k:04d}", episode=ep_no, scene=sc,
                                     scene_heading=str(item.get("scene_heading", "")), ts_start=f"L{k}", ts_end=f"L{k}",
                                     type=typ, speaker=str(item.get("speaker", "")).upper() if typ == "dialogue" else "",
                                     emotion=str(item.get("emotion", "")), text=body))
                parsed += _nonblank_chars(body) + _nonblank_chars(str(item.get("speaker", "")))
        return sorted(eps.values(), key=lambda e: e.episode), parsed, [f"dropped unverifiable line: {d}" for d in dropped]


# --------------------------------------------------------------------------- detection + coverage
def parse_sources(sources: list[tuple[str, str]], llm=None, min_coverage: float = 0.95,
                  force_format: Optional[str] = None) -> ParseResult:
    """Pick the best adapter, parse everything, and report coverage.
    If coverage is below `min_coverage` and an LLM is available, retry with the LLM fallback."""
    adapters = {"kuku": KukuAdapter(), "screenplay": ScreenplayAdapter()}
    sample = "\n".join(t for _, t in sources[:3])
    if force_format and force_format in adapters:
        chosen = adapters[force_format]
    elif force_format == "llm":
        chosen = None
    else:
        scored = sorted(((a.score(sample), k) for k, a in adapters.items()), reverse=True)
        chosen = adapters[scored[0][1]] if scored[0][0] > 0.2 else None

    warnings: list[str] = []
    if chosen is not None:
        res = _run_adapter(chosen, sources)
        if res.coverage >= min_coverage:
            return res
        warnings.append(f"{chosen.name}: coverage {res.coverage:.1%} is below {min_coverage:.0%}")
        if llm is None or not llm.available_for("parse"):
            res.warnings += warnings + ["no LLM available for the fallback parser; continuing with partial parse"]
            return res
    if llm is None or not llm.available_for("parse"):
        raise ValueError("Unknown script format and no LLM configured for the fallback parser.")
    res = _run_adapter(LLMFallbackAdapter(llm), sources)
    res.warnings = warnings + res.warnings
    return res


def _run_adapter(adapter, sources) -> ParseResult:
    episodes: list[Episode] = []
    total = parsed = 0
    unparsed: list[str] = []
    for idx, (name, text) in enumerate(sources, 1):
        fallback_ep = _first_int(name) or idx
        out, p, un = adapter.parse_file(name, text, fallback_ep)
        if isinstance(out, Episode):
            out = [out]
        episodes.extend(out)
        total += _nonblank_chars(text)
        parsed += p
        unparsed.extend(un[:5])
    # merge duplicates (same episode number from two files) and sort
    merged: dict[int, Episode] = {}
    for e in episodes:
        if e.episode in merged:
            merged[e.episode].lines.extend(e.lines)
        else:
            merged[e.episode] = e
    eps = sorted(merged.values(), key=lambda e: e.episode)
    cov = parsed / total if total else 0.0
    warnings = []
    nums = [e.episode for e in eps]
    missing = sorted(set(range(min(nums), max(nums) + 1)) - set(nums)) if nums else []
    if missing:
        warnings.append(f"missing episode numbers: {missing}")
    return ParseResult(episodes=eps, format_name=adapter.name, coverage=min(cov, 1.0),
                       unparsed_samples=unparsed[:20], warnings=warnings)


def parse_path(path, llm=None, min_coverage=0.95, force_format=None) -> ParseResult:
    return parse_sources(load_sources(path), llm=llm, min_coverage=min_coverage, force_format=force_format)


def dump_parse(res: ParseResult, out_path: Path) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        for e in res.episodes:
            for l in e.lines:
                f.write(json.dumps(l.to_dict(), ensure_ascii=False) + "\n")
