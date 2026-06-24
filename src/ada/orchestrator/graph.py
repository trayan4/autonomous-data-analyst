"""
This is the LangGraph orchestration.

Flow:  START -> router -> { out_of_scope: refuse
                         | in_scope:     compliance -> { pii_refuse
                                                       | simple:     data_retrieval (cheap)
                                                       | diagnostic: analysis (strong) } } -> END

The router is an LLM classifier that decides whether a question is answerable from
the warehouse at all. Out-of-scope questions are refused up front - before any SQL
is written - which is the robust fix for confabulation (e.g. "which sales reps to
fire" when there is no employee data).

The compliance node is a deterministic entitlement guardrail (ADR-0001). If a
question asks for customer personal data (names, emails, phones) and the caller is
NOT entitled to PII, it declines BEFORE any SQL is generated and returns a clear
"not authorized" message - never "the data doesn't exist". If the caller IS entitled
(can_view_pii=True) it passes allow_pii downstream so the agent shows those values.
This is the guarantee that lives at the request, not the query: a self-censoring
model can no longer turn a restricted request into a misleading "we don't have that".
The analysis agent (4c) adds a third route off the compliance node.

    ada-ask "Why did sales drop last month?"
"""
from __future__ import annotations

import sys
from typing import Optional, TypedDict

from langgraph.graph import START, END, StateGraph

from ada.observability import tracing


class AgentState(TypedDict, total=False):
    question: str
    scope: str              # router classification: out_of_scope | simple | diagnostic
    route: str              # final handler that produced the answer
    answer: str
    sql: str
    columns: list
    rows: list
    error: Optional[str]
    seconds: float
    can_view_pii: bool      # caller entitlement (set by the caller / auth layer)
    allow_pii: bool         # passed downstream when an entitled caller may see PII
    _stream: bool           # internal: stream the final narration via the custom channel


# ---- scope classifier (router) -------------------------------------------------

SCOPE_RULES = """You classify an incoming analytics question into exactly ONE of three labels.

The warehouse contains: customers (segment, region, signup), products (category, price),
orders and order_items (sales, GMV/revenue, units, channel, region, dates, returns),
marketing_spend (by month/region/channel), and inventory_snapshots (stock levels, stockouts).

Labels:
- out_of_scope: answering needs data the warehouse does NOT have - employees, sales
  representatives, staff, salaries, suppliers, or competitors.
- diagnostic: asks WHY a metric changed, or to explain / root-cause a drop, rise, or
  anomaly. These need a decomposition across segments, not a single number. Access to
  restricted fields is handled downstream, so a request for contact details is still
  in scope here.
- simple: a direct look-up or aggregate answerable with one figure or a short list -
  totals, rankings, counts, filters - with no "why" behind it.

When the question is in scope but you are unsure between simple and diagnostic, choose simple.

Examples:
Q: Why did sales drop last month? -> diagnostic
Q: What caused the revenue decline in the South? -> diagnostic
Q: Explain the spike in returns in Q1. -> diagnostic
Q: What was total GMV in May 2026? -> simple
Q: Which region had the highest GMV in 2025? -> simple
Q: Who are our top customers by spend? -> simple
Q: List customers with their contact details. -> simple
Q: How much did we spend on marketing last quarter? -> simple
Q: Which sales reps should we fire? -> out_of_scope
Q: What are our competitors' prices? -> out_of_scope

Reply with exactly one word: out_of_scope, simple, or diagnostic."""

REFUSAL = ("That question is outside what this assistant can answer. The warehouse holds "
           "e-commerce sales, marketing, and inventory data - customers, products, orders, GMV, "
           "channels, regions, marketing spend, and stock levels - but no data on employees, "
           "sales representatives, or staff. I can help with questions about sales, products, "
           "regions, channels, marketing, or inventory.")


def _chat(messages, **kw):
    from ada.agent.model_client import chat
    return chat(messages, **kw)


def classify_scope(question: str, chat_fn=None) -> str:
    chat_fn = chat_fn or _chat
    out = (chat_fn([{"role": "system", "content": SCOPE_RULES},
                    {"role": "user", "content": question}],
                   tier="strong", max_tokens=10) or "").strip().lower()
    first = out.split()[0] if out.split() else ""
    if first.startswith("out"):
        return "out_of_scope"
    if first.startswith("diag"):
        return "diagnostic"
    return "simple"   # default in-scope to the cheap path


# ---- nodes ---------------------------------------------------------------------

def make_router(classify_fn=None):
    classify_fn = classify_fn or classify_scope

    def router(state: AgentState) -> dict:
        return {"scope": classify_fn(state["question"])}

    return router


def _scope_decider(state: AgentState) -> str:
    return "refuse" if state.get("scope") == "out_of_scope" else "compliance"


def refuse_node(state: AgentState) -> dict:
    return {"route": "refuse", "answer": REFUSAL}


# ---- compliance guardrail (entitlement, deterministic) -------------------------

# Personal-contact terms that map to PII columns (customers.full_name/email/phone).
# Deterministic on purpose: the guarantee must not depend on what the model writes.
PII_TERMS = ("email", "phone", "full name", "customer name", "contact detail",
             "contact info", "home address", "mailing address", "personal detail",
             "personal information", "phone number", "email address")

# "email"/"phone" also name a *marketing channel* in this warehouse. A question
# about the channel is not a request for customer PII, so exclude that context
# unless it also names an unambiguous contact field.
_CHANNEL_CONTEXT = ("channel", "campaign", "marketing", "spend")
_UNAMBIGUOUS_PII = ("contact", "full name", "customer name", "address", "personal")


def requests_pii(question: str) -> bool:
    q = question.lower()
    if not any(t in q for t in PII_TERMS):
        return False
    if any(c in q for c in _CHANNEL_CONTEXT) and not any(t in q for t in _UNAMBIGUOUS_PII):
        return False
    return True


PII_REFUSAL = (
    "You are not authorized to view customer personal data such as names, email "
    "addresses, or phone numbers, so I can't run that query. This is an access "
    "restriction, not a gap in the data. I can answer with aggregated or "
    "non-identifying results instead - for example, customer counts by segment or "
    "region, or last order value summarized by group.")


def compliance_node(state: AgentState) -> dict:
    """Entitlement gate, then dispatch. Unauthorized PII request -> decline before any
    SQL is generated. Otherwise pass allow_pii downstream and route by complexity:
    diagnostic -> strong analysis agent, simple -> cheap data agent."""
    if requests_pii(state["question"]) and not state.get("can_view_pii", False):
        return {"route": "pii_refuse"}
    nxt = "analysis" if state.get("scope") == "diagnostic" else "data_retrieval"
    return {"route": nxt, "allow_pii": bool(state.get("can_view_pii", False))}


def _compliance_decider(state: AgentState) -> str:
    route = state.get("route")
    if route == "pii_refuse":
        return "pii_refuse"
    if route == "analysis":
        return "analysis"
    return "data_retrieval"


def pii_refuse_node(state: AgentState) -> dict:
    return {"route": "pii_refuse", "answer": PII_REFUSAL}


def _result_to_state(route: str, r) -> dict:
    return {"route": route, "answer": r.answer, "sql": r.sql, "columns": r.columns,
            "rows": r.rows, "error": r.error, "seconds": r.seconds}


def _emit_synth(stream_fn, writer):
    """Adapt a streaming synthesizer to the synthesize_fn(question, columns, rows) seam:
    push each delta out through the LangGraph custom-stream writer as it arrives, and
    return the full joined string so the node still records the complete answer in state
    (for eval, logging, and the final-state consumer). The retrieval steps upstream are
    unaffected - only this final narration streams."""
    def synth(question, columns, rows, max_rows: int = 50) -> str:
        parts: list[str] = []
        for delta in stream_fn(question, columns, rows, max_rows):
            parts.append(delta)
            if writer is not None:
                writer(delta)
        return "".join(parts)
    return synth


def _default_agent(question: str, allow_pii: bool = False, synthesize_fn=None):
    # simple look-ups run on the cheap tier
    from ada.agent.data_agent import answer_question
    return answer_question(question, allow_pii=allow_pii, tier="cheap", synthesize_fn=synthesize_fn)


def _default_analysis(question: str, allow_pii: bool = False, synthesize_fn=None):
    # diagnostic questions run on the strong tier with forced decomposition
    from ada.agent.analysis_agent import analyze_question
    return analyze_question(question, allow_pii=allow_pii, synthesize_fn=synthesize_fn)


def _stream_writer():
    """The LangGraph custom-stream writer for the current node, or None when the graph
    is being invoked (not streamed). Looked up lazily so non-streaming runs never touch
    the streaming machinery."""
    from langgraph.config import get_stream_writer
    return get_stream_writer()


def make_data_node(agent_fn=None):
    agent_fn = agent_fn or _default_agent

    def data_retrieval(state: AgentState) -> dict:
        if state.get("_stream"):
            from ada.agent.synthesizer import synthesize_answer_stream
            synth = _emit_synth(synthesize_answer_stream, _stream_writer())
            r = agent_fn(state["question"], state.get("allow_pii", False), synthesize_fn=synth)
        else:
            r = agent_fn(state["question"], state.get("allow_pii", False))
        return _result_to_state("data_retrieval", r)

    return data_retrieval


def make_analysis_node(analysis_fn=None):
    analysis_fn = analysis_fn or _default_analysis

    def analysis(state: AgentState) -> dict:
        if state.get("_stream"):
            from ada.agent.synthesizer import synthesize_diagnosis_stream
            synth = _emit_synth(synthesize_diagnosis_stream, _stream_writer())
            r = analysis_fn(state["question"], state.get("allow_pii", False), synthesize_fn=synth)
        else:
            r = analysis_fn(state["question"], state.get("allow_pii", False))
        return _result_to_state("analysis", r)

    return analysis


# ---- graph ---------------------------------------------------------------------

def _traced(name: str, fn):
    """Wrap a node so each invocation is a span tagged with the route it produced."""
    if tracing is None:
        return fn

    def wrapped(state: AgentState) -> dict:
        with tracing.span(name) as sp:
            out = fn(state)
            if isinstance(out, dict):
                tracing.set_attrs(sp, **{"ada.scope": out.get("scope"),
                                         "ada.route": out.get("route")})
            return out

    return wrapped


def build_graph(agent_fn=None, analysis_fn=None, classify_fn=None):
    g = StateGraph(AgentState)
    g.add_node("router", _traced("node.router", make_router(classify_fn)))
    g.add_node("compliance", _traced("node.compliance", compliance_node))
    g.add_node("data_retrieval", _traced("node.data_retrieval", make_data_node(agent_fn)))
    g.add_node("analysis", _traced("node.analysis", make_analysis_node(analysis_fn)))
    g.add_node("refuse", _traced("node.refuse", refuse_node))
    g.add_node("pii_refuse", _traced("node.pii_refuse", pii_refuse_node))
    g.add_edge(START, "router")
    g.add_conditional_edges("router", _scope_decider,
                            {"compliance": "compliance", "refuse": "refuse"})
    g.add_conditional_edges("compliance", _compliance_decider,
                            {"data_retrieval": "data_retrieval", "analysis": "analysis",
                             "pii_refuse": "pii_refuse"})
    g.add_edge("data_retrieval", END)
    g.add_edge("analysis", END)
    g.add_edge("refuse", END)
    g.add_edge("pii_refuse", END)
    return g.compile()


def run(question: str, agent_fn=None, analysis_fn=None, classify_fn=None,
        can_view_pii: bool = False) -> AgentState:
    graph = build_graph(agent_fn, analysis_fn, classify_fn)
    if tracing is None:
        return graph.invoke({"question": question, "can_view_pii": can_view_pii})
    with tracing.span("graph.run", **{"ada.question": question,
                                      "ada.can_view_pii": can_view_pii}) as root:
        result = graph.invoke({"question": question, "can_view_pii": can_view_pii})
        tracing.set_attrs(root, **{"ada.scope": result.get("scope"),
                                   "ada.route": result.get("route")})
        return result


def stream_run(question: str, agent_fn=None, analysis_fn=None, classify_fn=None,
               can_view_pii: bool = False):
    """Stream the final user-facing narration token-by-token.

    Yields answer-text deltas as the synthesis model produces them, then returns the
    final AgentState (route, sql, rows, full answer) via the generator's return value -
    capture it with the iterator protocol (StopIteration.value). Only the narration
    streams; routing, compliance, SQL generation and execution run silently first. The
    refusal paths emit no deltas (nothing to synthesize), so for those the generator
    simply yields nothing and the final state carries the static answer.

    Implemented with stream_mode=["custom", "values"]: "custom" carries the per-token
    deltas emitted by _emit_synth's writer; "values" carries the full state snapshots,
    the last of which is the final result.
    """
    graph = build_graph(agent_fn, analysis_fn, classify_fn)
    inp = {"question": question, "can_view_pii": can_view_pii, "_stream": True}
    final: AgentState = {}
    with tracing.span("graph.run", **{"ada.question": question,
                                      "ada.can_view_pii": can_view_pii}) as root:
        for mode, chunk in graph.stream(inp, stream_mode=["custom", "values"]):
            if mode == "custom":
                yield chunk                      # a narration token delta
            else:                                # "values": full state snapshot
                final = chunk
        tracing.set_attrs(root, **{"ada.scope": final.get("scope"),
                                   "ada.route": final.get("route")})
    return final


def main():
    """Console entry point: ada-ask "<question>" - streams the answer live."""
    tracing.configure_from_env()
    q = sys.argv[1] if len(sys.argv) > 1 else "Why did sales drop last month?"
    try:
        gen = stream_run(q)
        final: AgentState = {}
        streamed = False
        while True:
            try:
                token = next(gen)
            except StopIteration as stop:
                final = stop.value or {}
                break
            sys.stdout.write(token)
            sys.stdout.flush()
            streamed = True
        if not streamed:                          # refusal paths: nothing was streamed
            print(final.get("answer", ""))
        else:
            print()                               # newline after the streamed answer
        print("\nroute :", final.get("route"))
        if final.get("sql"):
            print("sql:\n" + final["sql"])
    finally:
        tracing.shutdown()


if __name__ == "__main__":
    main()
