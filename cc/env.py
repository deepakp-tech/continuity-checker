"""Load API keys from a `.env` file in the project folder.

Runs automatically when the `cc` package is imported, so the app, the CLI, the MCP server and the
evals all see the keys. Keys already set in the environment (for example with `export`) win over
the file. No extra package is needed.

.env format (one per line):
    GROQ_API_KEY=your-key
    GEMINI_API_KEY="your-key"
    # comments and blank lines are ignored
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = ENV_FILE) -> list[str]:
    """Set variables from `path` that aren't already set. Returns the names it set."""
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]          # strip matching quotes
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()   # strip trailing comment
        if name and value and not os.environ.get(name):
            os.environ[name] = value
            loaded.append(name)
    return loaded
