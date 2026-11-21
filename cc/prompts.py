"""All LLM prompts in one place. Examples are invented and show-neutral on purpose:
nothing here mentions characters or plot from any real show, so the prompts don't
overfit to the pack they were developed on."""

SYSTEM = ("You are a meticulous script continuity analyst. You only report what the given text "
          "states. You never invent facts, names, quotes or line ids. You always answer with a single JSON object.")

EXTRACT_PROMPT = """Extract story FACTS from this scene of a serialized drama.

Genre profile: {genre}
Episode: {episode}
Known characters (use these exact names for subjects; aliases in brackets):
{characters}

SCENE LINES (format: [line_id] SPEAKER: dialogue  or  [line_id] ACTION: description):
{lines}

What counts as a fact (only things the lines state explicitly - no guessing, no inference):
- relationship: attribute = the OTHER person's name, value = what the subject is TO them
  (sister, brother, mother, father, son, daughter, husband, wife, fiance, ex-fiance, cousin, employer, employee, friend, enemy)
- status: attribute = "life" with value alive|dead|injured|missing|recovered, or attribute = "marital" with value married|engaged|single|divorced|widowed
- identity: attribute = age|job|title|real_name|origin|wealth, value = what is stated
- possession: attribute = the object (short noun), value = has|lost|destroyed|stolen|gave_away
- knowledge: attribute = the secret in at most 6 words, value = learns|knows|does_not_know
- time: attribute = the event, value = the time statement as written ("in three days", "five years ago")
- location: ONLY lasting facts such as where someone lives or works (attribute = "residence" or "workplace")
Do NOT extract emotions, gestures, camera moves or momentary positions.

Rules:
- "quote" must be copied word-for-word from the line (at most 25 words). "line_id" must be the id of that line.
- For dialogue, source = "dialogue" and speaker = the speaker label; "I"/"my" refers to the speaker.
  Dialogue facts are CLAIMS by that character (they may lie) - still extract them.
- For ACTION lines, source = "action".
- If nothing qualifies, return {{"facts": []}}.

Example (invented scene):
[E004-S02-L0007] MEERA: "Arjun is my younger brother, and he is twenty-two."
[E004-S02-L0008] ACTION: Arjun tears the old letter into pieces.
-> {{"facts": [
 {{"subject": "Meera", "type": "relationship", "attribute": "Arjun", "value": "sister", "source": "dialogue", "speaker": "MEERA", "line_id": "E004-S02-L0007", "quote": "Arjun is my younger brother"}},
 {{"subject": "Arjun", "type": "identity", "attribute": "age", "value": "22", "source": "dialogue", "speaker": "MEERA", "line_id": "E004-S02-L0007", "quote": "he is twenty-two"}},
 {{"subject": "Arjun", "type": "possession", "attribute": "letter", "value": "destroyed", "source": "action", "speaker": "", "line_id": "E004-S02-L0008", "quote": "Arjun tears the old letter into pieces"}}]}}

Return JSON: {{"facts": [ ... ]}}"""

ENTAIL_PROMPT = """For each item, decide whether the QUOTE actually states the FACT.
Answer "yes" only if a careful reader would agree the quote says it. Figurative speech
("she is like a sister to me"), guesses and hypotheticals are "no". Use "unsure" if it depends on context.

Items:
{items}

Return JSON: {{"results": [{{"id": <id>, "answer": "yes"|"no"|"unsure"}}, ...]}}"""

ALIAS_PROMPT = """Two speaker labels appear in the same show. Decide whether they refer to the SAME person.

Label A: {a}   (speaks in episodes {eps_a})
Label B: {b}   (speaks in episodes {eps_b})
They never speak in the same scene.

Episode loglines that mention either name:
{loglines}

Evidence lines (how other characters address them, and action lines in their scenes):
{evidence}

Rules: similar names are NOT enough. A title can pass from one person to another, and two
different people can share a first name. Say "same" only if the evidence lines support it.
Cite only line ids shown above.

Return JSON: {{"verdict": "same"|"different"|"unsure", "confidence": 0.0-1.0,
 "evidence_line_ids": ["..."], "reason": "one sentence"}}"""

JUDGE_PROMPT = """Two facts from the same show may conflict. Classify the pair.

Genre profile: {genre}
Story devices that are NORMAL in this genre (not errors): {allowed}
Extra guidance: {notes}

Why the pair was flagged: {rule}

FACT A (episode {ep_a}, {ts_a}, {src_a}{spk_a}): {subj_a} | {type_a} | {attr_a} = {val_a}
Quote A: "{quote_a}"
Context A:
{ctx_a}

FACT B (episode {ep_b}, {ts_b}, {src_b}{spk_b}): {subj_b} | {type_b} | {attr_b} = {val_b}
Quote B: "{quote_b}"
Context B:
{ctx_b}

Verdicts:
- contradiction: both cannot be true in the story and nothing in the context explains it (a continuity error)
- story_change: the story legitimately changed between A and B (a marriage, a recovery, an object bought back)
- intentional: a lie, disguise, misdirection, flashback, dream, or a genre device listed above
- extraction_error: one of the facts misreads its quote
- unsure: the context shown is not enough to decide
Dialogue is a character's claim and may be a lie; action lines are true in the story.

Severity (for contradiction): high = breaks the plot; medium = an attentive viewer would notice; low = cosmetic.
In "reason", quote ONLY text that appears above, in double quotes. Do not mention events not shown.

Return JSON: {{"verdict": "...", "severity": "high"|"medium"|"low", "reason": "one or two sentences"}}"""

NOTES_PROMPT = """The writer's CONTINUITY NOTES for episode {episode} make claims about earlier events.
Check each claim against the script of episode {ref_episode} below.

CONTINUITY NOTES: {notes}

SCRIPT OF EPISODE {ref_episode} (camera directions removed):
{lines}

For every concrete claim about episode {ref_episode} (events, reveals, who did what):
- supported: the script shows it (cite line ids)
- contradicted: the script shows something incompatible (cite line ids)
- not_found: the script does not show it
Ignore claims about what happens in episode {episode} itself.

Return JSON: {{"claims": [{{"claim": "...", "status": "supported"|"contradicted"|"not_found",
 "evidence_line_ids": ["..."], "reason": "one sentence"}}]}}"""

PARSE_PROMPT = """Convert this script excerpt into structured lines. Copy the text of each line EXACTLY
as written - do not summarize, translate or add anything. If no episode number is visible use {default_episode}.

Types: "action" (description), "dialogue" (a character speaking; put the name in speaker),
"sfx" (sound), "camera" (shot directions).

EXCERPT:
{chunk}

Return JSON: {{"lines": [{{"episode": 1, "scene": 1, "scene_heading": "", "type": "dialogue",
 "speaker": "NAME", "emotion": "", "text": "exact words"}}]}}"""
