"""
test_app.py - the FastAPI surface streams tokens + a final state, offline.

stream_run is monkeypatched with a canned generator so the API can be exercised end to
end (SSE framing, token events, done event, refusal fallback) with no Azure.
"""
from __future__ import annotations

import json
import sys

from fastapi.testclient import TestClient

from ada.api import app as app_module

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def parse_sse(text):
    events = []
    for frame in text.split("\n\n"):
        if not frame.strip():
            continue
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        events.append((ev, json.loads(data) if data else None))
    return events


# --- 1. a streaming (diagnostic) answer -----------------------------------------
def fake_stream_ok(question, **kw):
    for t in ["South ", "Electronics ", "stockout."]:
        yield t
    return {"route": "analysis", "sql": "SELECT 1", "answer": "South Electronics stockout."}


app_module.stream_run = fake_stream_ok
client = TestClient(app_module.app)

check("health ok", client.get("/health").json() == {"status": "ok"})
check("index serves html", "<html" in client.get("/").text.lower())

evs = parse_sse(client.post("/ask", json={"question": "why did sales drop"}).text)
tokens = [d["text"] for e, d in evs if e == "token"]
done = [d for e, d in evs if e == "done"]
check("three token events streamed", tokens == ["South ", "Electronics ", "stockout."])
check("exactly one done event", len(done) == 1)
check("done carries route", done and done[0]["route"] == "analysis")
check("done carries sql", done and done[0]["sql"] == "SELECT 1")

# --- 2. refusal path: no tokens, answer in the done event -----------------------
def fake_stream_refuse(question, **kw):
    return {"route": "refuse", "sql": None, "answer": "That question is outside scope."}
    yield  # make it a generator


app_module.stream_run = fake_stream_refuse
evs = parse_sse(client.post("/ask", json={"question": "who to fire"}).text)
tokens = [d for e, d in evs if e == "token"]
done = [d for e, d in evs if e == "done"]
check("refusal streams no tokens", tokens == [])
check("refusal answer in done event", done and "outside scope" in done[0]["answer"])
check("refusal route is refuse", done and done[0]["route"] == "refuse")

# --- 3. tracing survives the single-thread streaming bridge (no detach error) ---
from ada.observability import tracing

exporter = tracing.configure("memory")


def fake_stream_traced(question, **kw):
    with tracing.span("graph.run", **{"ada.route": "data_retrieval"}):
        for t in ["a", "b", "c"]:
            yield t
    return {"route": "data_retrieval", "sql": "SELECT 1", "answer": "abc"}


app_module.stream_run = fake_stream_traced
evs = parse_sse(client.post("/ask", json={"question": "q"}).text)
toks = "".join(d["text"] for e, d in evs if e == "token")
check("tracing path still streams tokens", toks == "abc")
if exporter is not None:
    spans = [s for s in exporter.get_finished_spans() if s.name == "graph.run"]
    check("graph.run span recorded through the bridge", len(spans) >= 1)
else:
    print("  [skip] OpenTelemetry not installed; span assertion skipped")

print("\nRESULT:", "PASS - API streams answers and final state" if ok else "FAIL")
sys.exit(0 if ok else 1)
