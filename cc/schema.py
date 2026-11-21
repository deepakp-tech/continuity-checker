"""Internal data model. Every input format is converted into these types;
nothing after the parser knows which format a show came in."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

LineType = Literal["action", "dialogue", "sfx", "camera", "other"]

FACT_TYPES = ("relationship", "status", "identity", "possession", "knowledge", "time", "location")
VERDICTS = ("contradiction", "story_change", "intentional", "extraction_error", "unsure")
SEVERITIES = ("high", "medium", "low")


@dataclass
class Line:
    line_id: str            # e.g. "E01-S01-L0005" (stable across runs)
    episode: int
    scene: int
    scene_heading: str
    ts_start: str           # "00:11.30" or "L5" when the source has no timestamps
    ts_end: str
    type: str               # LineType
    speaker: str = ""       # raw speaker label as written, e.g. "LADY ANNA"
    emotion: str = ""
    text: str = ""

    def render(self) -> str:
        """Compact one-line form used in LLM prompts."""
        if self.type == "dialogue":
            return f"[{self.line_id}] {self.speaker}: {self.text}"
        return f"[{self.line_id}] {self.type.upper()}: {self.text}"

    def to_dict(self):
        return asdict(self)


@dataclass
class Episode:
    episode: int
    title: str = ""
    logline: str = ""
    continuity_notes: str = ""
    lines: list[Line] = field(default_factory=list)


@dataclass
class ParseResult:
    episodes: list[Episode]
    format_name: str
    coverage: float                     # share of non-blank source text that was parsed
    unparsed_samples: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def all_lines(self) -> list[Line]:
        return [l for e in self.episodes for l in e.lines]


class Fact(BaseModel):
    """One fact extracted from the script. `quote` must be copied from the line."""
    subject: str
    type: str
    attribute: str = Field(description="what the fact is about: other person, 'age', 'job', object, secret, event")
    value: str
    source: Literal["action", "dialogue", "notes"] = "action"
    line_id: str
    quote: str
    speaker: str = ""

    @field_validator("type")
    @classmethod
    def _type_ok(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in FACT_TYPES:
            raise ValueError(f"unknown fact type {v!r}")
        return v

    @field_validator("subject", "attribute", "value", "quote")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("empty field")
        return v


class Verdict(BaseModel):
    verdict: str
    severity: str = "medium"
    reason: str = ""

    @field_validator("verdict")
    @classmethod
    def _v(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in VERDICTS else "unsure"

    @field_validator("severity")
    @classmethod
    def _s(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in SEVERITIES else "medium"


@dataclass
class Candidate:
    """A pair of facts (or a fact and a line) that code rules think may conflict."""
    rule: str
    fact_a: dict
    fact_b: Optional[dict]
    note: str = ""
    line_b: Optional[dict] = None   # used by the 'acts after death' rule
