# Continuity Checker for AI-written microdramas

A script editor uploads a show's episode scripts and gets back a ranked list of **continuity errors**
(a sister who becomes a cousin, a dead character who speaks, a burned letter that reappears, a secret
learned twice). Every flag shows the two clashing lines with episode and timestamp, so it can be
verified in seconds.

Built for Kuku's AI Generalist assignment, Problem 3.

---

## Quick start

```bash
pip install -r requirements.txt

# 1) Offline, no API key: rules-only baseline on a public demo show
python -m cc run --input eval/synthetic/contract_heiress --out outputs/demo --genre billionaire_romance --rules-only
#    -> outputs/demo/report.md, flags.csv, facts.csv, characters.csv, bible.sqlite

# 2) With LLMs (free tiers): put your keys in .env, then run on any show folder or .zip
cp .env.example .env           # then fill in GROQ_API_KEY (https://console.groq.com, free, no card)
                               # (GEMINI_API_KEY is optional: the second-model fact check now runs on Groq's
                               #  Qwen model, because Gemini's free tier allows only ~20 requests a day)
                               # .env is loaded automatically by the app, CLI, MCP server and evals;
                               # keys set with `export` take priority over the file
python -m cc run --input path/to/show_folder --out outputs/my_show --genre period_drama

# 3) Editor app
streamlit run app.py

# 4) Evals (see "How we know it works")
python eval/run_evals.py --suite synthetic --out eval_out --compare-rules

# 5) Tests (offline; the LLM path is tested against a local fake API)
pytest -q
```

Models are set per stage in `config.yaml`. Switching provider or model is a config edit, not a code change:
every provider (Groq, Gemini, OpenRouter, local Ollama, or a paid API) is called through the same
OpenAI-style endpoint. Free model IDs change often, so check each console for current names.

**Confidential scripts:** keep them outside this repo (or in `data/private/`, which is git-ignored), run locally,
and prefer Groq or Ollama for them: Google's free Gemini tier may use inputs to improve its products.
Never upload private scripts to a publicly hosted copy of the app.

---

## How it works

```
upload -> detect format -> coverage check -> genre profile -> resolve names -> extract facts -> verify facts
       -> story bible -> find conflicts (+ notes check) -> LLM judge -> editor review -> feedback log
```

| Stage | Who | What | Safeguard |
|---|---|---|---|
| Parse (`cc/parsing.py`) | code | Kuku block format, standard screenplay, or **LLM fallback** for unknown layouts, converted to one internal line format | **Coverage check**: % of source text understood; below 95% it warns or falls back. LLM-parsed lines must exist in the source. |
| Resolve names (`cc/aliases.py`) | code + LLM + human | `LADY ANNA` = `ANNA`? | Labels that **speak in the same scene are never merged**. Role words ("woman", "guard") never auto-merge. Possessive labels (`TOM'S MOTHER`) are a different person. Unclear pairs stay separate until a human decides; decisions can be **limited to an episode range** (a title passing to an heir). |
| Extract facts (`cc/extract.py`) | LLM | relationship, status, identity, possession, knowledge, time per scene, each with a **quote and line id** | pydantic schema; camera lines skipped (48% of the text in the pack, so roughly half the tokens) |
| Verify facts | code + 2nd model | the quote must fuzzy-match the cited line (else the fact is dropped and logged), then a **different model** checks that the quote really states the fact | dropped facts are kept with a reason, which gives the grounding rate |
| Story bible (`cc/bible.py`) | code | SQLite: lines, characters, aliases, facts, flags, editor decisions | |
| Find conflicts (`cc/conflicts.py`) | code | change points per (character, type, attribute), relationship stated from both sides, **speaks after death**, secret learned twice | linear cost: only change points go to the judge |
| Judge (`cc/judge.py`) | LLM | contradiction / story_change / intentional / extraction_error / **unsure**, plus severity | genre profile says which devices are normal; any text the judge quotes must exist in its input, otherwise the verdict is **downgraded to unsure** |
| Notes check | LLM | each episode's CONTINUITY NOTES are checked against the episode they describe | a "contradicted" claim must cite real line ids |
| Merge check | code | many contradictions straddling two merged labels mean a possible wrong merge, which is flagged | |
| Review (`app.py`) | human | accept / dismiss / mark intentional | decisions go to `feedback_log.jsonl` and feed regression tests |

**Rules-only mode** (`--rules-only`) replaces every LLM stage with regex rules (`cc/rules.py`). It runs offline,
and it is the "no LLM" baseline in the ablation.

**Two modes:** full audit, or `--incremental` (only new episodes are extracted and checked against the existing bible,
which is how it would sit in a weekly writing workflow).

**MCP server (optional, read-only):** `python -m cc mcp --out outputs/my_show` exposes `get_character`, `get_facts`,
`list_open_flags` and `check_scene`. An AI writing tool can check a **draft** scene against the bible *before* the
episode is written. It works with mcp 1.x and 2.x.

---

## How we know it works (`eval/`)

* **Synthetic shows** (`eval/build_synthetic.py`, public): a billionaire romance in Kuku's format and a fantasy show
  with Hinglish dialogue in screenplay format. They contain planted errors, **traps** (a lie to reporters, a driver
  disguise, a recovery, a resurrection in a fantasy genre, a title passing from father to son), name traps, and a
  **clean control** version where any contradiction is a false alarm.
* **Planted errors in the real pack** (`eval/plant_errors.py`): errors and traps inserted into a copy, with several
  phrasings per type so regex matching alone can't score well.
* **Hand labels** (`eval/labels/`): name groups, 50 extracted facts, judge pairs. These give merge precision and
  recall, extraction precision, judge accuracy and the twist false-alarm rate.
* **Stability:** the judge is re-asked with the two facts swapped. **Regression:** earlier editor decisions must still hold.
* `--compare-rules` adds the rules-only column (the ablation).

```bash
python eval/run_evals.py --suite synthetic --out eval_out --compare-rules
python eval/run_evals.py --suite pack --input <pack folder> --work ../private_runs --out ../private_runs/eval --compare-rules
```

Targets are fixed in `run_evals.py` (`TARGETS`) before running, and the report marks each metric PASS or FAIL.

---

## Cost and scale (free tier)

* The pack is 50 episodes (~75k words). After dropping camera lines, extraction is roughly 65-70k input tokens:
  one model's daily free quota on Groq, about 20-30 min because of the tokens-per-minute cap.
* The judge only sees change points (usually tens to a few hundred calls per show), and **everything is cached**,
  so re-runs and incremental runs cost almost nothing.
* Free-tier limits are per model, so `config.yaml` uses different models per stage.
* Shows are independent and run in parallel. The real bottleneck is editor time, which is why flags are ranked.

## Known limits

* Text only: visual continuity in AI video (faces, costumes) is out of scope.
* Facts that are only implied, never stated, are often missed (watch extraction recall).
* Planted errors are easier than organic ones, so precision on the **unmodified** pack must come from hand labels.
* Rules-only mode is a baseline, not a product. It can't read Hinglish or paraphrase.
* Tested on one real show plus two synthetic ones. New formats get the coverage check and the LLM fallback parser, but a new genre may need a profile.

## Layout

```
cc/            parsing, aliases, extract, conflicts, judge, bible, llm, prompts, rules, pipeline, report, CLI
profiles/      genre profiles (period_drama, billionaire_romance, fantasy, generic)
app.py         Streamlit editor app
mcp_server.py  read-only MCP server
eval/          synthetic shows + answer keys, error planter, eval runner, label templates
tests/         offline tests (fake OpenAI-compatible server for the LLM path)
config.yaml    models per stage, thresholds
```
