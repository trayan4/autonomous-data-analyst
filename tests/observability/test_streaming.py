"""
test_streaming.py - chat_stream yields text deltas AND still records usage/cost.

Streaming hides token counts unless stream_options.include_usage is set; this test
fakes the OpenAI streaming protocol (content chunks + a final usage-only chunk) and
asserts (a) the deltas concatenate to the full reply, and (b) telemetry/tracing
capture the same cost + gen_ai.* attributes as the non-streaming path.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from ada.agent import model_client
from ada.observability import telemetry, tracing

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def _delta_chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None)


def _usage_chunk(pt, ct):
    return SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct))


def fake_stream():
    yield _delta_chunk("South ")
    yield _delta_chunk("Electronics ")
    yield _delta_chunk("stockout.")
    yield _usage_chunk(120, 18)          # final usage-only chunk


class FakeCompletions:
    def create(self, **kwargs):
        # streaming path must opt into usage reporting
        assert kwargs.get("stream") is True, "stream not requested"
        assert kwargs.get("stream_options", {}).get("include_usage") is True, "usage not requested"
        return fake_stream()


class FakeClient:
    chat = SimpleNamespace(completions=FakeCompletions())


fake_settings = SimpleNamespace(deployment="gpt-4o-mini", deployment_strong="gpt-4.1",
                                base_url="http://x/", api_key="k")

# inject the fake client/settings (bypass real Azure)
model_client._client = FakeClient()
model_client._settings = fake_settings

exporter = tracing.configure("memory")
telemetry.start_run()

deltas = list(model_client.chat_stream([{"role": "user", "content": "why did sales drop"}], tier="strong"))
full = "".join(deltas)

check("yields multiple deltas", len(deltas) == 3)
check("deltas reconstruct full reply", full == "South Electronics stockout.")

summary = telemetry.summarize()["total"]
check("usage recorded from final chunk", summary["prompt"] == 120 and summary["completion"] == 18)
check("strong-tier cost computed (>0)", summary["cost"] > 0)

if exporter is not None:
    spans = exporter.get_finished_spans()
    chat_spans = [s for s in spans if s.name.startswith("chat ")]
    check("one chat span emitted", len(chat_spans) == 1)
    attrs = dict(chat_spans[0].attributes) if chat_spans else {}
    check("span marked streaming", attrs.get("gen_ai.request.streaming") is True)
    check("span carries output tokens", attrs.get("gen_ai.usage.output_tokens") == 18)
    check("span carries cost", float(attrs.get("gen_ai.usage.cost_usd", 0)) > 0)
else:
    print("  [skip] OpenTelemetry not installed; span assertions skipped")

print("\nRESULT:", "PASS - streaming yields tokens and records usage" if ok else "FAIL")
sys.exit(0 if ok else 1)
