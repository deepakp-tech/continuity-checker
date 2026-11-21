# Continuity report

- Input: `river_witch` · genre profile: **fantasy** · mode: full
- Format: screenplay_v1 · parse coverage: **100.0%** · 6 episodes · 24 lines
- Characters: 8 speaker labels → 6 characters (2 auto-merged, 0 by a human, **0 waiting for a human**, 0 kept apart because they share a scene)
- Facts: 6 extracted, 6 verified (**100.0% grounded**); dropped: none
- Candidates checked: 3 · verdicts: {'contradiction': 1, 'unsure': 2}
- Notes check: skipped: the notes check needs an LLM (stage 'notes' is set to rules)
- Models: parse=rules (no LLM), alias=rules (no LLM), extract=rules (no LLM), entail=rules (no LLM), judge=rules (no LLM), notes=rules (no LLM)
- LLM calls: 0 (+0 from cache), tokens in/out: 0/0 · run time 0.0s

## Flags to review (3)

Ranked: contradictions first (high → low severity), then unsure. Every flag cites the exact lines.

### 1. [contradiction · high] Mohan / relationship / Kavya: 'brother' then 'cousin'

- **A** (Ep 1 E001-S01-L0002) Mohan | relationship | Kavya = brother (dialogue)
  > Mohan is my brother
- **B** (Ep 6 E006-S01-L0001) Mohan | relationship | Kavya = cousin (action)
  > Mohan is Kavya's cousin
- **Why:** relationship stated differently
- rule `value_change` · judged by rules · flag id 3

### 2. [unsure · medium] Raja is said to die in ep 3 but speaks in ep 5

- **A** (Ep 3 E003-S01-L0001) Raja | status | life = dead (action)
  > The Raja dies
- **B** (Ep 5 E005-S01-L0002 @L2) Raja speaks
  > Find the Tantrik. Tonight.
- **Why:** genre allows resurrection; needs a human check
- rule `speaks_after_death` · judged by rules · flag id 1

### 3. [unsure · medium] Mohan is said to die in ep 4 but speaks in ep 5

- **A** (Ep 4 E004-S01-L0001) Mohan | status | life = dead (action)
  > Mohan is killed
- **B** (Ep 5 E005-S02-L0004 @L4) Mohan speaks
  > Kavya... main zinda hoon?
- **Why:** genre allows resurrection; needs a human check
- rule `speaks_after_death` · judged by rules · flag id 2
