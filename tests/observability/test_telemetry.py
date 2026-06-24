"""Offline tests for the telemetry core and model_client instrumentation.

No network, no Azure: the model client is exercised with a fake OpenAI client so we
can assert that a real chat() call records the right tier, deployment, tokens and cost.

    python observability/test_telemetry.py
"""
import sys
from types import SimpleNamespace


from ada.observability import telemetry

ok = True


def check(name, cond):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    ok = ok and cond


# --- 1. cost arithmetic ----------------------------------------------------------
print("cost arithmetic:")
# gpt-4.1 = $2/1M in, $8/1M out -> 1000 in + 500 out = 0.002 + 0.004 = 0.006
check("gpt-4.1 1000in+500out = $0.006",
      abs(telemetry.cost_usd("gpt-4.1", 1000, 500) - 0.006) < 1e-9)
# gpt-4o-mini = $0.15/1M in, $0.60/1M out -> 1000 in + 500 out = 0.00015 + 0.0003
check("gpt-4o-mini 1000in+500out = $0.00045",
      abs(telemetry.cost_usd("gpt-4o-mini", 1000, 500) - 0.00045) < 1e-9)
check("unknown deployment costs 0", telemetry.cost_usd("mystery", 1000, 500) == 0.0)

# --- 2. record + summarize -------------------------------------------------------
print("\nrecord + summarize:")
telemetry.start_run("case")
telemetry.record_call("strong", "gpt-4.1", 1000, 500, 1.2)
telemetry.record_call("cheap", "gpt-4o-mini", 2000, 400, 0.4)
telemetry.record_call("cheap", "gpt-4o-mini", 1000, 100, 0.3)
s = telemetry.summarize()
check("3 calls total", s["total"]["calls"] == 3)
check("two tiers tracked", set(s["by_tier"]) == {"strong", "cheap"})
check("strong has 1 call", s["by_tier"]["strong"]["calls"] == 1)
check("cheap has 2 calls", s["by_tier"]["cheap"]["calls"] == 2)
expected_total = 0.006 + telemetry.cost_usd("gpt-4o-mini", 3000, 500)
check("total cost adds up", abs(s["total"]["cost"] - expected_total) < 1e-9)
case1 = s
telemetry.end_run()
check("run cleared after end_run", telemetry.current_run() is None)
check("record outside a run is a no-op (no crash)",
      telemetry.record_call("cheap", "gpt-4o-mini", 10, 10, 0.1) is not None)

# --- 3. merge across cases -------------------------------------------------------
print("\nmerge across cases:")
telemetry.start_run("case2")
telemetry.record_call("strong", "gpt-4.1", 500, 200, 0.6)
case2 = telemetry.summarize()
telemetry.end_run()
merged = telemetry.merge_summaries([case1, case2])
check("merged call count", merged["total"]["calls"] == 4)
check("merged strong calls", merged["by_tier"]["strong"]["calls"] == 2)
check("merged cost = sum of cases",
      abs(merged["total"]["cost"] - (case1["total"]["cost"] + case2["total"]["cost"])) < 1e-9)

# --- 4. model_client records usage on a real chat() call (fake client) ----------
print("\nmodel_client instrumentation:")
from ada.agent import model_client  # noqa: E402

fake_settings = SimpleNamespace(deployment="gpt-4o-mini", deployment_strong="gpt-4.1",
                                base_url="http://x", api_key="k")


class FakeCompletions:
    def create(self, model, messages, temperature, max_tokens):
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=1234, completion_tokens=56),
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        )


fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
model_client._ensure = lambda: (fake_client, fake_settings)

telemetry.start_run("client")
out = model_client.chat([{"role": "user", "content": "hi"}], tier="strong")
rec = telemetry.current_run().calls[-1]
check("chat returned content", out == "ok")
check("recorded tier=strong", rec.tier == "strong")
check("recorded strong deployment", rec.deployment == "gpt-4.1")
check("recorded prompt tokens", rec.prompt_tokens == 1234)
check("recorded completion tokens", rec.completion_tokens == 56)
check("recorded cost matches gpt-4.1 price",
      abs(rec.cost_usd - telemetry.cost_usd("gpt-4.1", 1234, 56)) < 1e-12)
telemetry.end_run()

print("\nRESULT:", "PASS" if ok else "FAIL")
print("\n--- sample summary render ---")
print(telemetry.format_summary(merged, "demo total"))
sys.exit(0 if ok else 1)
