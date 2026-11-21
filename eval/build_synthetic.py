"""Builds the two synthetic test shows and their answer keys.

Both shows are invented for this project (public, safe to publish). Each contains:
  * planted ERRORS  - real continuity mistakes the tool must flag (kind = "error")
  * TRAPS           - things that look like errors but are not (kind = "trap"): recoveries, lies,
                      disguises, resurrection in a fantasy genre, a title passing to an heir
  * NAME TRAPS      - relabelled characters (should merge), possessive labels and father/son sharing
                      a surname or title (must NOT merge)
  * a "clean_patch" - edits that remove every planted error, giving a clean control show on which
                      any 'contradiction' is a false alarm

Run:  python eval/build_synthetic.py      (writes eval/synthetic/<show>/ and eval/answer_keys/<show>.json)
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- Kuku-format writer
def kuku_episode(ep: int, logline: str, notes: str, scenes: list[tuple[str, list[tuple[str, str]]]]) -> str:
    out = [f"EPISODE NUMBER: Episode {ep}", f"EPISODE LOGLINE: {logline}", f"CONTINUITY NOTES: {notes}", "", "--- SCRIPT START ---", ""]
    for si, (heading, blocks) in enumerate(scenes, 1):
        t = 0.0
        body = []
        for label, text in blocks:
            dur = 2.0 + len(text) / 30
            s, e = _ts(t), _ts(t + dur)
            t += dur * 0.8
            if label in ("CAMERA", "ACTION", "SOUND FX"):
                body += [f"**{label}:**", f"Start: ({s}) | End: ({e})", text, ""]
            else:
                body += [f"**{label}:**", f"Start: ({s}) | End: ({e})", f"\"{text}\"", ""]
        out += [f"SCENE {si:02d}", f"Scene heading: {heading}", "Scene start: 00:00.00", f"Scene end: {_ts(t + 2)}", "",
                "Actions and dialogue:", ""] + body
    out.append("--- SCRIPT END ---")
    return "\n".join(out)


def _ts(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:05.2f}"


# --------------------------------------------------------------------------- Show A
SHOW_A = "contract_heiress"
A_EPISODES = [
    (1, "Meera, drowning in debt, meets the quiet new driver Arjun on the night her landlord threatens eviction.",
     "First episode of the series.",
     [("INT. SHARMA HOUSE – NIGHT", [
         ("CAMERA", "Slow push in on a cramped living room lit by a single bare bulb."),
         ("ACTION", "Meera wears the gold bangle her late father gave her, turning it nervously."),            # T2 has
         ("MEERA (Tired)", "Riya, did you pay the electricity bill?"),
         ("RIYA (Guilty)", "Not yet, Didi. The landlord came again today."),
         ("SOUND FX", "Tyres crunch on gravel outside."),
         ("ACTION", "Arjun works as a driver for the Mehra family, or so everyone believes."),               # T3 disguise a
         ("ACTION", "Arjun is thirty years old, broad-shouldered, with a thin scar along his jaw."),          # E4 age a
         ("SHANKAR (Gruff)", "Sir, Mr. Mehra is waiting. We should go."),
         ("ARJUN (Calm)", "Five minutes, Shankar. Miss Meera, your gate was left open."),
         ("CAMERA", "Close-up on Meera, wary and curious at once."),
     ])]),
    (2, "At a tense Mehra family dinner Meera signs a marriage contract to save her sister, then destroys it.",
     "Follows Episode 1, where Meera met Arjun the driver.",
     [("INT. MEHRA MANSION DINING HALL – NIGHT", [
         ("CAMERA", "Wide shot of a long table glittering with silver."),
         ("MR. MEHRA (Cold)", "A contract marriage, Miss Sharma. One year. Then you disappear."),
         ("ARJUN (Quiet)", "Father, she has not agreed to anything yet."),                                     # co-scene: MR. MEHRA + ARJUN
         ("MEERA (Defiant)", "Riya is my younger sister. I will not let her starve."),                        # E1 a
         ("ARJUN (Bitter)", "Nandini is my ex-fiancee. That ended long ago, whatever she tells the press."),  # T4 a
         ("ACTION", "Meera burns the contract in the fireplace as the family watches in silence."),          # E3 a destroyed
         ("SHANKAR (Low)", "Sir, the cars are ready whenever you are."),
     ])]),
    (3, "A crash on the highway kills the loyal bodyguard and leaves Dadi fighting for her life.",
     "Follows Episode 2, where Meera burned the contract.",
     [("EXT. HIGHWAY – NIGHT", [
         ("CAMERA", "Headlights flare; a truck swerves across the divider."),
         ("SOUND FX", "Metal screams against metal."),
         ("ACTION", "Shankar dies at the scene of the crash, still gripping the wheel."),                     # E2 a death
         ("ACTION", "Dadi is badly injured and rushed to the hospital."),                                     # T1 a injured
         ("VIKRAM (Grim)", "Arjun is my brother, and this accident was no accident."),                        # E6 a
         ("CAMERA", "Slow tilt up to Vikram's face lit by flashing police lights."),
     ])]),
    (4, "At the hospital Meera discovers who Arjun really is, while Nandini spins a lie for the cameras.",
     "Follows Episode 3, where Shankar died in the crash.",
     [("INT. CITY HOSPITAL CORRIDOR – DAY", [
         ("ACTION", "Meera learns that Arjun is the heir of Mehra Group."),                                    # E5 a
         ("NANDINI (Sweet, to reporters)", "Arjun is my husband. We married quietly in London."),             # T4 b lie
         ("ACTION", "Nandini smiles at the cameras, knowing the lie will spread by nightfall."),
         ("ACTION", "Meera loses the bangle in the crowd at the hospital gate."),                              # T2 lost
         ("NANDINI'S MOTHER (Smug)", "My daughter will be Mrs. Mehra by winter. Mark my words."),             # possessive label
         ("MEERA (Shaken)", "Why didn't you tell me who you were?"),
     ])]),
    (5, "Arjun takes his father's chair at Mehra Group; Dadi walks back in on her own.",
     "Follows Episode 4, where Meera learned the truth about Arjun.",
     [("INT. MEHRA GROUP BOARDROOM – DAY", [
         ("ACTION", "Arjun works as a chief executive now, the driver's uniform gone for good."),              # T3 disguise b
         ("ARJUN MEHRA (Firm)", "Vikram is my cousin, nothing more. He has no claim on this company."),        # E6 b (inverse)
         ("ACTION", "Dadi has recovered and walks in unaided, cane tapping."),                                # T1 b recovered
         ("ACTION", "Meera holds the contract, its pages crisp and unburnt."),                                # E3 b has
         ("MEERA (Stunned)", "How is this still here?"),
     ])]),
    (6, "Back home, Meera confronts Riya about the debts, while the papers celebrate Arjun's birthday.",
     "Follows Episode 5, where Dadi was still in a coma.",                                                   # N1 wrong note
     [("INT. SHARMA HOUSE – NIGHT", [
         ("ACTION", "Meera stares at the unpaid bills spread across the table."),
         ("MEERA SHARMA (Hurt)", "Even my cousin Riya never understood what I gave up."),                     # E1 b
         ("ACTION", "On the birthday cake in the newspaper photo, the caption is clear: Arjun is thirty-four years old."),  # E4 b
         ("RIYA (Tearful)", "I never asked you to sell yourself, Didi."),
     ])]),
    (7, "A familiar voice in the Mehra garage makes Meera relive the truth all over again.",
     "Follows Episode 6, where Meera and Riya argued.",
     [("INT. MEHRA GARAGE – DAY", [
         ("SHANKAR (Gruff)", "Sir, the car is ready. Where to today?"),                                       # E2 b speaks after death
         ("ACTION", "Meera learns that Arjun is the heir of Mehra Group, and the room spins."),                # E5 b learns twice
         ("ARJUN MEHRA (Soft)", "I wanted you to know me before you knew my name."),
     ])]),
    (8, "Father and son face off over Meera as the contract year begins.",
     "Follows Episode 7.",
     [("INT. MEHRA MANSION STUDY – NIGHT", [
         ("MR. MEHRA (Icy)", "You will end this marriage, or you will end your inheritance."),
         ("ARJUN MEHRA (Steady)", "Then keep the inheritance, Father."),                                       # co-scene again
         ("MEERA SHARMA (Quiet)", "Arjun, don't do this for me."),
         ("ACTION", "Meera steps between them, and for once Mr. Mehra has no answer."),
     ])]),
]

A_KEY = {
    "show": SHOW_A, "format": "kuku", "genre": "billionaire_romance",
    "alias_groups": [["MEERA", "MEERA SHARMA"], ["ARJUN", "ARJUN MEHRA"], ["MR. MEHRA"], ["RIYA"], ["SHANKAR"], ["VIKRAM"],
                     ["NANDINI"], ["NANDINI'S MOTHER"]],
    "expected": [
        {"id": "E1", "kind": "error", "type": "relationship", "subjects": ["Riya", "Meera"], "episodes": [2, 6], "desc": "Riya is Meera's sister in ep 2, her cousin in ep 6"},
        {"id": "E2", "kind": "error", "type": "status", "subjects": ["Shankar"], "episodes": [3, 7], "desc": "Shankar dies in ep 3, speaks in ep 7"},
        {"id": "E3", "kind": "error", "type": "possession", "subjects": ["Meera"], "episodes": [2, 5], "desc": "contract burned in ep 2, intact in ep 5"},
        {"id": "E4", "kind": "error", "type": "identity", "subjects": ["Arjun"], "episodes": [1, 6], "desc": "Arjun is 30 in ep 1, 34 in ep 6 (days later)"},
        {"id": "E5", "kind": "error", "type": "knowledge", "subjects": ["Meera"], "episodes": [4, 7], "desc": "Meera learns Arjun is the heir twice"},
        {"id": "E6", "kind": "error", "type": "relationship", "subjects": ["Arjun", "Vikram"], "episodes": [3, 5], "desc": "Vikram calls Arjun his brother; Arjun calls Vikram his cousin"},
        {"id": "N1", "kind": "error", "type": "notes", "subjects": [], "episodes": [5, 6], "llm_only": True, "desc": "ep 6 notes say Dadi was in a coma in ep 5; ep 5 shows her recovered"},
        {"id": "T1", "kind": "trap", "type": "status", "subjects": ["Dadi"], "episodes": [3, 5], "desc": "injured then recovered = story change"},
        {"id": "T2", "kind": "trap", "type": "possession", "subjects": ["Meera"], "episodes": [1, 4], "desc": "has the bangle, then loses it = story change"},
        {"id": "T3", "kind": "trap", "type": "identity", "subjects": ["Arjun"], "episodes": [1, 5], "desc": "driver disguise, then CEO = hidden identity trope"},
        {"id": "T4", "kind": "trap", "type": "relationship", "subjects": ["Arjun", "Nandini"], "episodes": [2, 4], "desc": "Nandini lies to reporters that Arjun is her husband"},
    ],
    "clean_patch": [
        {"episode": 6, "find": "Even my cousin Riya", "replace": "Even my sister Riya"},
        {"episode": 3, "find": "Shankar dies at the scene of the crash, still gripping the wheel.", "replace": "Shankar survives the crash with a broken arm."},
        {"episode": 2, "find": "Meera burns the contract in the fireplace as the family watches in silence.", "replace": "Meera reads the contract by the fireplace as the family watches in silence."},
        {"episode": 6, "find": "Arjun is thirty-four years old", "replace": "Arjun is thirty years old"},
        {"episode": 7, "find": "Meera learns that Arjun is the heir of Mehra Group, and the room spins.", "replace": "Meera remembers the day she learned the truth, and the room spins."},
        {"episode": 5, "find": "Vikram is my cousin, nothing more.", "replace": "Vikram is my brother, nothing more."},
        {"episode": 6, "find": "where Dadi was still in a coma", "replace": "where Dadi walked back in on her own"},
    ],
}


# --------------------------------------------------------------------------- Show B (screenplay format, Hinglish, fantasy)
SHOW_B = "river_witch"
B_EPISODES = {
    1: """EPISODE 1: The Curse
LOGLINE: Kavya refuses to abandon her brother Mohan on the night the river curse returns.
NOTES: First episode of the series.

INT. HAVELI COURTYARD - NIGHT

Lanterns flicker. Kavya waits by the iron gate, a shawl over her head.

KAVYA
(worried)
Mohan is my brother, Dadi. Main use akela nahi chhodungi.

DADI
Beta, nadi ki chudail ka shraap sach hai.

THE RAJA
No one leaves the haveli after dark. That is my order.

RUDRA
Father, the curse is a story for children.

The Raja slams his staff on the stone floor. Rudra falls silent.
""",
    2: """EPISODE 2: The Mirror
LOGLINE: A memory of the river, and a cursed gift on Kavya's doorstep.
NOTES: Follows Episode 1, where the Raja forbade anyone to leave.

EXT. GHAT - DAWN

Flashback. Young Kavya runs barefoot down the stone steps toward the water.

YOUNG KAVYA
Maa, the river is singing!

INT. KAVYA'S ROOM - MORNING

Kavya smashes the mirror the Tantrik left on her doorstep.

TANTRIK
(whispering, unseen)
Tumhari aankhon mein nadi hai, ladki.
""",
    3: """EPISODE 3: Poison
LOGLINE: The Raja is poisoned at his own table, and Dadi stands between Kavya and the Tantrik.
NOTES: Follows Episode 2, where Kavya smashed the Tantrik's mirror.

INT. DARBAR - NIGHT

The Raja dies, poisoned at his own table.

DADI
Kavya meri poti hai, Tantrik. Use haath mat lagana.

RUDRA
(numb)
Who did this? Who poisoned my father?

TANTRIK
Shraap ka pehla daam, Rajkumar.
""",
    4: """EPISODE 4: The Price
LOGLINE: Mohan pays for his sister's defiance.
NOTES: Follows Episode 3, where the Raja was poisoned.

EXT. RIVERBANK - NIGHT

Mohan is killed by the Tantrik's curse, sinking beneath the black water.

KAVYA
(screaming)
Mohan! Nahi!
""",
    5: """EPISODE 5: The Crown
LOGLINE: Rudra takes the throne, and the river witch gives Kavya a miracle.
NOTES: Follows Episode 4, where Mohan died at the river.

INT. DARBAR - DAY

Rudra is crowned; from now on the whole court calls him the Raja.

THE RAJA
Find the Tantrik. Tonight.

EXT. RIVERBANK - NIGHT

The river witch's spell brings Mohan back to life, gasping on the sand.

MOHAN
Kavya... main zinda hoon?
""",
    6: """EPISODE 6: Moonlight
LOGLINE: Kavya uses the Tantrik's mirror to find his hiding place.
NOTES: Follows Episode 5, where Mohan was brought back to life.

EXT. GHAT - NIGHT

Mohan is Kavya's cousin, raised in the same courtyard.

Kavya holds the mirror up to the moonlight.

DADI MAA
Meri beti Kavya ko kuch nahi hoga.

Dadi lights a diya and watches the water.
""",
}
B_KEY = {
    "show": SHOW_B, "format": "screenplay", "genre": "fantasy",
    "alias_groups": [["KAVYA", "YOUNG KAVYA"], ["DADI", "DADI MAA"], ["RUDRA"], ["THE RAJA"], ["MOHAN"], ["TANTRIK"]],
    "expected": [
        {"id": "B1", "kind": "error", "type": "relationship", "subjects": ["Mohan", "Kavya"], "episodes": [1, 6], "desc": "Mohan is Kavya's brother in ep 1, cousin in ep 6"},
        {"id": "B2", "kind": "error", "type": "possession", "subjects": ["Kavya"], "episodes": [2, 6], "desc": "mirror smashed in ep 2, used whole in ep 6"},
        {"id": "B3", "kind": "error", "type": "relationship", "subjects": ["Kavya", "Dadi"], "episodes": [3, 6], "llm_only": True, "desc": "Hinglish: Kavya is Dadi's granddaughter (poti) in ep 3, daughter (beti) in ep 6"},
        {"id": "T5", "kind": "trap", "type": "status", "subjects": ["Mohan"], "episodes": [4, 5], "desc": "killed, then brought back by magic (fantasy genre)"},
        {"id": "T6", "kind": "trap", "type": "status", "subjects": ["Raja", "The Raja"], "episodes": [3, 5], "desc": "the title 'the Raja' passes from father to Rudra"},
    ],
    "clean_patch": [
        {"episode": 6, "find": "Mohan is Kavya's cousin", "replace": "Mohan is Kavya's brother"},
        {"episode": 2, "find": "Kavya smashes the mirror", "replace": "Kavya hides the mirror"},
        {"episode": 6, "find": "Meri beti Kavya", "replace": "Meri poti Kavya"},
    ],
}


def write_show(name: str, files: dict[str, str], key: dict):
    d = HERE / "synthetic" / name
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.txt"):
        old.unlink()
    for fn, txt in files.items():
        (d / fn).write_text(txt, encoding="utf-8")
    k = HERE / "answer_keys"
    k.mkdir(exist_ok=True)
    (k / f"{name}.json").write_text(json.dumps(key, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(files)} episodes -> {d}")


def main():
    write_show(SHOW_A, {f"episode-{ep:03d}.txt": kuku_episode(ep, lg, nt, sc) for ep, lg, nt, sc in A_EPISODES}, A_KEY)
    write_show(SHOW_B, {f"episode-{ep:03d}.txt": txt for ep, txt in B_EPISODES.items()}, B_KEY)


if __name__ == "__main__":
    main()
