"""
test_synthesizer.py - verify the synthesizer builds the right grounded prompt,
without a live model. Mocks chat() and inspects the messages it would send.

Run:  python agent/test_synthesizer.py
"""
from __future__ import annotations

import sys

from ada.agent import synthesizer as S

captured = {}


def fake_chat(messages, **kw):
    captured["messages"] = messages
    return "stub answer"


S.chat = fake_chat

ok = True


def check(name, cond):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    ok = ok and cond


# with data
cols = ["region", "gmv"]
rows = [("North", 1372904.94), ("South", 511358.98)]
S.synthesize_answer("Why did sales drop last month?", cols, rows)
sys_text = captured["messages"][0]["content"].lower()
user_text = captured["messages"][1]["content"]

check("grounding rule present (only the table)", "only the result table" in sys_text)
check("describe-data-first rule present", "what the data does show" in sys_text)
check("anti-fabrication rule present", "do not invent" in sys_text)
check("anti-relabeling rule present", "actually correspond" in sys_text)
check("table header rendered", "region | gmv" in user_text)
check("actual data value present", "511358.98" in user_text)
check("question included", "Why did sales drop last month?" in user_text)

# empty result
S.synthesize_answer("Anything in July?", ["x"], [])
check("empty rows handled", "(no rows returned)" in captured["messages"][1]["content"])

# ---- streaming twins ----
stream_captured = {}


def fake_chat_stream(messages, **kw):
    stream_captured["messages"] = messages
    stream_captured["tier"] = kw.get("tier")
    for piece in ["South ", "Electronics ", "stockout."]:
        yield piece


S.chat_stream = fake_chat_stream

# answer stream: same prompt as the blocking call, yields deltas
S.synthesize_answer("Why did sales drop last month?", cols, rows)        # refresh blocking capture
blocking_msgs = captured["messages"]
deltas = list(S.synthesize_answer_stream("Why did sales drop last month?", cols, rows))
check("answer_stream yields deltas", len(deltas) == 3 and "".join(deltas) == "South Electronics stockout.")
check("answer_stream prompt identical to blocking", stream_captured["messages"] == blocking_msgs)
check("answer_stream uses cheap tier", stream_captured["tier"] == "cheap")

# diagnosis stream: same prompt as blocking synthesize_diagnosis, strong tier
S.synthesize_diagnosis("Why did sales drop last month?", cols, rows)     # refresh blocking capture
blocking_diag = captured["messages"]
ddeltas = list(S.synthesize_diagnosis_stream("Why did sales drop last month?", cols, rows))
check("diagnosis_stream yields deltas", "".join(ddeltas) == "South Electronics stockout.")
check("diagnosis_stream prompt identical to blocking", stream_captured["messages"] == blocking_diag)
check("diagnosis_stream uses strong tier", stream_captured["tier"] == "strong")

print("\nRESULT:", "PASS - synthesizer prompt correct" if ok else "FAIL")
sys.exit(0 if ok else 1)
