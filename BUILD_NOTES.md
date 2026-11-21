# Build notes: what went wrong while building (and how it was caught)

The brief asks where AI tools got it wrong. These are real issues found during the build, each caught by
a check that was designed into the system. Use the ones you can explain in your own words.

| # | What went wrong | How it was caught | Fix |
|---|---|---|---|
| 1 | The first parser assumed one bracket per speaker label; the pack also has labels with two brackets, e.g. `**NAME (O.S.) (Emotion):**` | **Parse coverage check** showed 98.7%, not 100%, and listed the unparsed lines | regex accepts any number of brackets |
| 2 | Some episodes write `SCENE HEADING:` in capitals | coverage check again (99.96%) | case-insensitive headers, then 100.00% (3,727 blocks = 3,727 timestamps) |
| 3 | Name matching merged two different labels that both ended in the role word "WOMAN" | reading the merge list on the real pack | role words never auto-merge: a shared word must be used as a proper name in the script |
| 4 | A lord and a lady sharing a surname (`LORD X` / `LADY X`) were correctly kept apart, but **both got the display name "X"**, so downstream they collapsed into one character anyway (54 characters instead of 56) | a unit test (`test_same_scene_veto_and_possessive_label`) | display names are unique; collisions use the full label |
| 5 | A possessive label (`NAME'S MOTHER`) shares a token with `NAME` and could have been merged with her son | reviewing the design for the "same name, two people?" question | possessive labels are treated as one whole name |
| 6 | Regex baseline read "holds the ledger **up** to the candlelight" as an object called "up" | planted-error eval: 2 possession errors missed | wider stop-word lookahead |
| 7 | The first planted-error benchmark used regex-friendly sentences, so the **rules baseline scored 100%**, which proved the benchmark was too easy, not that rules work | eval result looked too good | several phrasings per error type; rules baseline dropped to 50% |
| 8 | The "possibly two people" merge check fired on a synthetic character (Arjun), whose merge was correct | synthetic show (known answer) | only counts contradictions that span episodes where *different* labels were used |
| 9 | The test's fake judge matched the word "letter" anywhere in the prompt (including the context lines) | a failing test | match on the fact line itself |
| 10 | The MCP server code targeted `mcp.server.fastmcp.FastMCP`; installed `mcp` 2.x renamed it to `MCPServer` | import error when testing | import shim that supports both versions |

Design checks that did their job: the coverage check (1, 2), unit tests (4, 9), synthetic answer keys (8),
and the rules-vs-LLM benchmark comparison (7).

_Examples here are anonymised because this repo is public; use the real label names in your private solution doc._
