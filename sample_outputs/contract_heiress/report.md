# Continuity report

- Input: `contract_heiress` · genre profile: **billionaire_romance** · mode: full
- Format: kuku_timestamped_v1 · parse coverage: **100.0%** · 8 episodes · 45 lines
- Characters: 10 speaker labels → 8 characters (2 auto-merged, 0 by a human, **0 waiting for a human**, 1 kept apart because they share a scene)
- Facts: 18 extracted, 18 verified (**100.0% grounded**); dropped: none
- Candidates checked: 9 · verdicts: {'contradiction': 8, 'story_change': 1}
- Notes check: skipped: the notes check needs an LLM (stage 'notes' is set to rules)
- Models: parse=rules (no LLM), alias=rules (no LLM), extract=rules (no LLM), entail=rules (no LLM), judge=rules (no LLM), notes=rules (no LLM)
- LLM calls: 0 (+0 from cache), tokens in/out: 0/0 · run time 0.0s

## Flags to review (8)

Ranked: contradictions first (high → low severity), then unsure. Every flag cites the exact lines.

### 1. [contradiction · high] Shankar is said to die in ep 3 but speaks in ep 7

- **A** (Ep 3 E003-S01-L0003) Shankar | status | life = dead (action)
  > Shankar dies
- **B** (Ep 7 E007-S01-L0001 @00:00.00) Shankar speaks
  > Sir, the car is ready. Where to today?
- **Why:** character speaks after an action line says they died
- rule `speaks_after_death` · judged by rules · flag id 9

### 2. [contradiction · high] Riya / relationship / Meera: 'sister' then 'cousin'

- **A** (Ep 2 E002-S01-L0004) Riya | relationship | Meera = sister (dialogue)
  > Riya is my younger sister
- **B** (Ep 6 E006-S01-L0002) Riya | relationship | Meera = cousin (dialogue)
  > my cousin Riya
- **Why:** relationship stated differently
- rule `value_change` · judged by rules · flag id 7

### 3. [contradiction · high] Arjun is Vikram's brother, but Vikram is Arjun's cousin

- **A** (Ep 3 E003-S01-L0005) Arjun | relationship | Vikram = brother (dialogue)
  > Arjun is my brother
- **B** (Ep 5 E005-S01-L0002) Vikram | relationship | Arjun = cousin (dialogue)
  > Vikram is my cousin
- **Why:** relationship stated differently
- rule `relationship_inverse` · judged by rules · flag id 2

### 4. [contradiction · high] Arjun is Nandini's husband, but Nandini is Arjun's ex-fiance

- **A** (Ep 4 E004-S01-L0002) Arjun | relationship | Nandini = husband (dialogue)
  > Arjun is my husband
- **B** (Ep 2 E002-S01-L0005) Nandini | relationship | Arjun = ex-fiance (dialogue)
  > Nandini is my ex-fiancee
- **Why:** relationship stated differently
- rule `relationship_inverse` · judged by rules · flag id 1

### 5. [contradiction · medium] Meera learns 'arjun is the heir of mehra' in ep 4 and again in ep 7

- **A** (Ep 4 E004-S01-L0001) Meera | knowledge | arjun is the heir of mehra = learns (action)
  > Meera learns that Arjun is the heir of Mehra Group
- **B** (Ep 7 E007-S01-L0002) Meera | knowledge | arjun is the heir of mehra = learns (action)
  > Meera learns that Arjun is the heir of Mehra Group
- **Why:** the same secret is learned twice
- rule `learns_twice` · judged by rules · flag id 8

### 6. [contradiction · medium] Arjun / identity / age: '30' then '34'

- **A** (Ep 1 E001-S01-L0007) Arjun | identity | age = 30 (action)
  > Arjun is thirty years old
- **B** (Ep 6 E006-S01-L0003) Arjun | identity | age = 34 (action)
  > Arjun is thirty-four years old
- **Why:** identity stated differently
- rule `value_change` · judged by rules · flag id 6

### 7. [contradiction · medium] Arjun / identity / job: 'driver for' then 'chief executive'

- **A** (Ep 1 E001-S01-L0006) Arjun | identity | job = driver for (action)
  > Arjun works as a driver for
- **B** (Ep 5 E005-S01-L0001) Arjun | identity | job = chief executive (action)
  > Arjun works as a chief executive
- **Why:** identity stated differently
- rule `value_change` · judged by rules · flag id 3

### 8. [contradiction · medium] Meera / possession / contract: 'destroyed' then 'has'

- **A** (Ep 2 E002-S01-L0006) Meera | possession | contract = destroyed (action)
  > Meera burns the contract
- **B** (Ep 5 E005-S01-L0004) Meera | possession | contract = has (action)
  > Meera holds the contract
- **Why:** object reappears after being destroyed or lost
- rule `value_change` · judged by rules · flag id 4

## Pairs judged NOT to be errors (1)

Shown for transparency (story changes, intentional devices, extraction errors).

- [story_change] Dadi / status / life: 'injured' then 'recovered' — status changed over time
