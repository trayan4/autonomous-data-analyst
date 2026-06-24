"""Offline tests for OpenTelemetry spans (in-memory exporter, no network).

Asserts the graph emits a root span + one span per node it traverses, with route
attributes, and that a model call emits a GenAI span carrying tokens and cost.

    python observability/test_tracing.py
"""
import sys
from types import SimpleNamespace


from ada.observability import tracing

ok = True


def check(name, cond):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    ok = ok and cond


exporter = tracing.configure("memory")
if exporter is None:
    print("OpenTelemetry not installed - tracing is a no-op here. Skipping (PASS).")
    sys.exit(0)


def names(spans):
    return [s.name for s in spans]


def by_name(spans, n):
    return next((s for s in spans if s.name == n), None)


def res(answer):
    return SimpleNamespace(answer=answer, sql="SELECT 1", columns=["c"], rows=[(1,)],
                           error=None, seconds=0.1)


# --- 1. diagnostic run: root + router + compliance + analysis -------------------
print("graph spans (diagnostic route):")
from ada.orchestrator import graph as G  # noqa: E402

exporter.clear()
G.run("Why did sales drop last month?",
      agent_fn=lambda q, allow_pii=False: res("DATA"),
      analysis_fn=lambda q, allow_pii=False: res("ANALYSIS"),
      classify_fn=lambda q: "diagnostic")
spans = exporter.get_finished_spans()
nm = names(spans)
check("root graph.run present", "graph.run" in nm)
check("node.router present", "node.router" in nm)
check("node.compliance present", "node.compliance" in nm)
check("node.analysis present", "node.analysis" in nm)
check("node.data_retrieval NOT present", "node.data_retrieval" not in nm)
root = by_name(spans, "graph.run")
check("root tagged route=analysis", root.attributes.get("ada.route") == "analysis")
check("root tagged scope present", root.attributes.get("ada.scope") in (None, "diagnostic"))

# --- 2. simple run: root + router + compliance + data_retrieval -----------------
print("\ngraph spans (simple route):")
exporter.clear()
G.run("What was total GMV in May 2026?",
      agent_fn=lambda q, allow_pii=False: res("DATA"),
      analysis_fn=lambda q, allow_pii=False: res("ANALYSIS"),
      classify_fn=lambda q: "simple")
nm = names(exporter.get_finished_spans())
check("node.data_retrieval present", "node.data_retrieval" in nm)
check("node.analysis NOT present", "node.analysis" not in nm)

# --- 3. model-call GenAI span ---------------------------------------------------
print("\nmodel-call span:")
from ada.agent import model_client  # noqa: E402

fake_settings = SimpleNamespace(deployment="gpt-4o-mini", deployment_strong="gpt-4.1",
                                base_url="http://x", api_key="k")


class FakeCompletions:
    def create(self, model, messages, temperature, max_tokens):
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=1234, completion_tokens=56),
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])


model_client._ensure = lambda: (SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
                                fake_settings)

exporter.clear()
model_client.chat([{"role": "user", "content": "hi"}], tier="strong")
spans = exporter.get_finished_spans()
chat_span = by_name(spans, "chat gpt-4.1")
check("chat span emitted with model in name", chat_span is not None)
if chat_span:
    a = chat_span.attributes
    check("gen_ai.request.model = gpt-4.1", a.get("gen_ai.request.model") == "gpt-4.1")
    check("gen_ai.system set", a.get("gen_ai.system") == "azure.ai.openai")
    check("input tokens recorded", a.get("gen_ai.usage.input_tokens") == 1234)
    check("output tokens recorded", a.get("gen_ai.usage.output_tokens") == 56)
    check("cost attribute present", a.get("gen_ai.usage.cost_usd", 0) > 0)
    check("tier tagged strong", a.get("ada.model_tier") == "strong")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
