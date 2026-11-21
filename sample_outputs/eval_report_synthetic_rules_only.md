# Evaluation report

Targets were fixed before running. `n/a` = not applicable to this system or no labels yet.

Systems: **rules_only** = alias:rules, extract:rules, judge:rules

## contract_heiress

| Metric | Target | rules_only |
|---|---|---|
| Planted errors caught (verdict = contradiction) | >= 80% | 100% PASS |
| Planted errors caught or sent to a human (unsure) |  | 100% |
| Traps NOT flagged as contradictions (lies, disguises, recoveries, genre devices) | >= 80% | 50% FAIL |
| Contradiction flags that are real (planted) errors | >= 70% | 75% PASS |
| Contradictions on the clean control show (false alarms) | <= 1 | 2 FAIL |
| Extracted facts whose quote verified | >= 90% | 100% PASS |
| Name merges that are correct | >= 100% | 100% PASS |
| Wrong merges (two people as one) | <= 0 | 0 PASS |
| Same-person label pairs found | >= 80% | 100% PASS |

**Per item - rules_only**

- E1 [error, relationship] Riya is Meera's sister in ep 2, her cousin in ep 6 -> **caught** ['contradiction']
- E2 [error, status] Shankar dies in ep 3, speaks in ep 7 -> **caught** ['contradiction']
- E3 [error, possession] contract burned in ep 2, intact in ep 5 -> **caught** ['contradiction']
- E4 [error, identity] Arjun is 30 in ep 1, 34 in ep 6 (days later) -> **caught** ['contradiction']
- E5 [error, knowledge] Meera learns Arjun is the heir twice -> **caught** ['contradiction']
- E6 [error, relationship] Vikram calls Arjun his brother; Arjun calls Vikram his cousin -> **caught** ['contradiction']
- N1 [error, notes] ep 6 notes say Dadi was in a coma in ep 5; ep 5 shows her recovered -> **n/a (needs an LLM)** 
- T1 [trap, status] injured then recovered = story change -> **ok, not flagged** ['story_change']
- T2 [trap, possession] has the bangle, then loses it = story change -> **ok, not flagged** 
- T3 [trap, identity] driver disguise, then CEO = hidden identity trope -> **FLAGGED (wrong)** ['contradiction']
- T4 [trap, relationship] Nandini lies to reporters that Arjun is her husband -> **FLAGGED (wrong)** ['contradiction']
- false alarms on clean show: ["Arjun is Nandini's husband, but Nandini is Arjun's ex-fiance", "Arjun / identity / job: 'driver for' then 'chief executive'"]

## river_witch

| Metric | Target | rules_only |
|---|---|---|
| Planted errors caught (verdict = contradiction) | >= 80% | 50% FAIL |
| Planted errors caught or sent to a human (unsure) |  | 50% |
| Traps NOT flagged as contradictions (lies, disguises, recoveries, genre devices) | >= 80% | 100% PASS |
| Contradiction flags that are real (planted) errors | >= 70% | 100% PASS |
| Contradictions on the clean control show (false alarms) | <= 1 | 0 PASS |
| Extracted facts whose quote verified | >= 90% | 100% PASS |
| Name merges that are correct | >= 100% | 100% PASS |
| Wrong merges (two people as one) | <= 0 | 0 PASS |
| Same-person label pairs found | >= 80% | 100% PASS |

**Per item - rules_only**

- B1 [error, relationship] Mohan is Kavya's brother in ep 1, cousin in ep 6 -> **caught** ['contradiction']
- B2 [error, possession] mirror smashed in ep 2, used whole in ep 6 -> **MISSED** 
- B3 [error, relationship] Hinglish: Kavya is Dadi's granddaughter (poti) in ep 3, daughter (beti) in ep 6 -> **n/a (needs an LLM)** 
- T5 [trap, status] killed, then brought back by magic (fantasy genre) -> **ok, not flagged** ['unsure']
- T6 [trap, status] the title 'the Raja' passes from father to Rudra -> **ok, not flagged** ['unsure']
