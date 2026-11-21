"""Provider-agnostic LLM client.

Every provider is reached through the OpenAI-style /chat/completions endpoint, so switching
between Groq, Gemini, OpenRouter, Ollama (or a paid API) is a config change, not a code change.
Each pipeline stage can use its own provider/model (config.yaml -> stages).

provider "rules" means: no LLM for that stage; the caller uses the rule-based baseline instead.
That is how the whole pipeline runs offline (tests, CI, and the no-LLM baseline in the evals).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


@dataclass
class StageCfg:
    provider: str
    model: str = ""
    temperature: float = 0.0
    max_tokens: int = 2048


@dataclass
class Stats:
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: int = 0
    seconds: float = 0.0
    per_stage: dict = field(default_factory=dict)

    def add(self, stage: str, pt: int, ct: int, secs: float):
        self.calls += 1
        self.prompt_tokens += pt
        self.completion_tokens += ct
        self.seconds += secs
        s = self.per_stage.setdefault(stage, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
        s["calls"] += 1; s["prompt_tokens"] += pt; s["completion_tokens"] += ct


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict, cache_path: str | Path = ".cache/llm_cache.sqlite"):
        self.providers: dict = cfg.get("providers", {})
        self.stages: dict[str, StageCfg] = {k: StageCfg(**v) for k, v in cfg.get("stages", {}).items()}
        self.retry = cfg.get("retry", {"max_attempts": 6, "base_seconds": 2.0})
        self.stats = Stats()
        self._last_call: dict[str, float] = {}
        self._lock = threading.Lock()
        # Optional callback, told whenever a provider makes us wait or gives up (used by the app to show
        # "Gemini's daily limit is used up" style messages). Receives a dict, see _notify().
        self.on_wait: Optional[Callable[[dict], None]] = None
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(cache_path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, model TEXT, ts REAL)")

    # ------------------------------------------------------------------ public
    def stage(self, stage: str) -> StageCfg:
        return self.stages.get(stage) or self.stages.get("default") or StageCfg(provider="rules")

    def available_for(self, stage: str) -> bool:
        return self.stage(stage).provider != "rules"

    def describe(self, stage: str) -> str:
        s = self.stage(stage)
        return "rules (no LLM)" if s.provider == "rules" else f"{s.provider}:{s.model}"

    def complete_json(self, prompt: str, stage: str, system: str = "") -> Optional[dict]:
        """Send a prompt, return the parsed JSON object (or None if the stage has no LLM).
        Results are cached by (provider, model, prompt) so re-runs cost nothing."""
        sc = self.stage(stage)
        if sc.provider == "rules":
            return None
        key = hashlib.sha256(f"{sc.provider}|{sc.model}|{sc.temperature}|{system}|{prompt}".encode()).hexdigest()
        row = self._db.execute("SELECT v FROM cache WHERE k=?", (key,)).fetchone()
        if row:
            self.stats.cache_hits += 1
            return json.loads(row[0])
        text = self._call(sc, stage, prompt, system)
        data = parse_json_loose(text)
        if data is None:
            # one repair attempt: ask the model to return valid JSON only
            text = self._call(sc, stage, prompt + "\n\nYour previous answer was not valid JSON. Return ONLY the JSON object.", system)
            data = parse_json_loose(text)
        if data is None:
            self.stats.errors += 1
            raise LLMError(f"[{stage}] model returned non-JSON output: {text[:200]!r}")
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)", (key, json.dumps(data), f"{sc.provider}:{sc.model}", time.time()))
            self._db.commit()
        return data

    # ------------------------------------------------------------------ internals
    def _call(self, sc: StageCfg, stage: str, prompt: str, system: str) -> str:
        if requests is None:
            raise LLMError("the 'requests' package is required for LLM calls")
        p = self.providers.get(sc.provider)
        if not p:
            raise LLMError(f"provider {sc.provider!r} is not defined in config.providers")
        key = os.environ.get(p.get("api_key_env", ""), "") if p.get("api_key_env") else "none"
        if not key:
            raise LLMError(f"missing API key: add {p.get('api_key_env')}=... to the .env file "
                           f"in the project folder (or set it as an environment variable)")
        url = p["base_url"].rstrip("/") + "/chat/completions"
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body: dict[str, Any] = {"model": sc.model, "messages": msgs, "temperature": sc.temperature, "max_tokens": sc.max_tokens}
        if p.get("json_mode", True):
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        attempts = int(self.retry.get("max_attempts", 6))
        base = float(self.retry.get("base_seconds", 2.0))
        last_err = ""
        for a in range(attempts):
            self._pace(sc.provider, float(p.get("min_seconds_between_calls", 0)))
            t0 = time.time()
            try:
                r = requests.post(url, headers=headers, json=body, timeout=float(p.get("timeout_seconds", 120)))
            except Exception as e:  # network error -> retry
                last_err = str(e)
                if a + 1 < attempts:
                    wait = base * (2 ** a)
                    self._notify(sc.provider, stage, "network", wait, a + 1, attempts)
                    time.sleep(wait)
                continue
            if r.status_code == 200:
                j = r.json()
                usage = j.get("usage") or {}
                self.stats.add(stage, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), time.time() - t0)
                return (j["choices"][0]["message"].get("content") or "").strip()
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (429, 500, 502, 503, 504, 529):
                if a + 1 < attempts:   # no point sleeping after the last try
                    hdr = r.headers.get("retry-after")
                    wait = float(hdr) if hdr and hdr.replace(".", "", 1).isdigit() else base * (2 ** a)
                    self._notify(sc.provider, stage, wait_reason(r.status_code, r.text), wait, a + 1, attempts)
                    time.sleep(wait)
                continue
            if r.status_code == 400 and "response_format" in body:   # some models reject json mode
                body.pop("response_format"); continue
            break
        self.stats.errors += 1
        self._notify(sc.provider, stage, "gave_up", 0.0, attempts, attempts, detail=last_err[:200])
        raise LLMError(f"[{stage}] {sc.provider}:{sc.model} failed: {last_err}")

    def _notify(self, provider: str, stage: str, reason: str, seconds: float, attempt: int, attempts: int, detail: str = ""):
        """Tell the caller why we are waiting. reason: daily_limit | rate_limit | busy | network | gave_up."""
        if self.on_wait:
            try:
                self.on_wait({"provider": provider, "stage": stage, "reason": reason, "seconds": seconds,
                              "attempt": attempt, "attempts": attempts, "detail": detail})
            except Exception:
                pass  # a display problem must never break the check

    def _pace(self, provider: str, min_gap: float):
        if min_gap <= 0:
            return
        with self._lock:
            last = self._last_call.get(provider, 0.0)
            wait = last + min_gap - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last_call[provider] = time.time()


_DAILY = ("exceeded your current quota", "per day", "(rpd)", "(tpd)", "free_tier_requests", "daily")


def wait_reason(status: int, body: str) -> str:
    """Classify a 'please wait' reply: the free daily allowance is used up, a per-minute limit, or a busy server."""
    if status == 429:
        b = (body or "").lower()
        return "daily_limit" if any(s in b for s in _DAILY) else "rate_limit"
    return "busy"


def parse_json_loose(text: str) -> Optional[dict]:
    """Parse JSON even when wrapped in ```json fences or surrounded by prose."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
    for candidate in (t, _outer_braces(t)):
        if not candidate:
            continue
        try:
            v = json.loads(candidate)
            return v if isinstance(v, dict) else {"items": v}
        except Exception:
            pass
    return None


def _outer_braces(t: str) -> str:
    i, j = t.find("{"), t.rfind("}")
    return t[i:j + 1] if 0 <= i < j else ""
