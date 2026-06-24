"""Offline tests for the compliance/entitlement guardrail (no model, no DB).

    python orchestrator/test_compliance.py
"""
import sys


from ada.orchestrator.graph import run, requests_pii


class FakeResult:
    def __init__(self):
        self.answer = "FAKE_ANSWER"
        self.sql = "SELECT 1"
        self.columns, self.rows, self.error, self.seconds = [], [], None, 0.0


def make_spy():
    calls = {}

    def agent_fn(question, allow_pii=False):
        calls["question"] = question
        calls["allow_pii"] = allow_pii
        return FakeResult()

    return agent_fn, calls


def simple(_q):
    return "simple"


def diagnostic(_q):
    return "diagnostic"


def out_of_scope(_q):
    return "out_of_scope"


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def main():
    ok = True

    # --- requests_pii unit behavior -------------------------------------------
    print("requests_pii detection:")
    ok &= check("ucC (email, phone) -> True",
                requests_pii("Give me the churned customers with their email, phone, and last order value."))
    ok &= check("contact details -> True", requests_pii("List customers with their contact details."))
    ok &= check("email *channel* -> False (marketing context)",
                not requests_pii("What was GMV by email channel last month?"))
    ok &= check("email spend -> False", not requests_pii("How much did we spend on email marketing?"))
    ok &= check("plain sales question -> False", not requests_pii("Why did sales drop last month?"))
    ok &= check("top region -> False", not requests_pii("Which region had the highest GMV in 2025?"))

    # --- routing: unauthorized PII request ------------------------------------
    print("\nunauthorized PII request:")
    agent_fn, calls = make_spy()
    out = run("Give me churned customers with their email and phone.",
              agent_fn=agent_fn, classify_fn=simple, can_view_pii=False)
    ok &= check("route == pii_refuse", out.get("route") == "pii_refuse")
    ok &= check("agent NOT called (no SQL generated)", "question" not in calls)
    ok &= check("answer says not authorized", "not authorized" in out.get("answer", "").lower())
    ok &= check("answer does NOT say data doesn't exist",
                "doesn't exist" not in out.get("answer", "").lower()
                and "does not contain" not in out.get("answer", "").lower())

    # --- routing: authorized PII request --------------------------------------
    print("\nauthorized PII request (entitled caller):")
    agent_fn, calls = make_spy()
    out = run("Give me churned customers with their email and phone.",
              agent_fn=agent_fn, classify_fn=simple, can_view_pii=True)
    ok &= check("route == data_retrieval", out.get("route") == "data_retrieval")
    ok &= check("agent called", calls.get("question") is not None)
    ok &= check("agent called with allow_pii=True", calls.get("allow_pii") is True)

    # --- routing: non-PII in-scope --------------------------------------------
    print("\nnon-PII in-scope question:")
    agent_fn, calls = make_spy()
    out = run("Why did sales drop last month?",
              agent_fn=agent_fn, classify_fn=simple, can_view_pii=False)
    ok &= check("route == data_retrieval", out.get("route") == "data_retrieval")
    ok &= check("agent called with allow_pii=False", calls.get("allow_pii") is False)

    # --- routing: out-of-scope still refused up front -------------------------
    print("\nout-of-scope question:")
    agent_fn, calls = make_spy()
    out = run("Which sales reps should we fire?",
              agent_fn=agent_fn, classify_fn=out_of_scope, can_view_pii=False)
    ok &= check("route == refuse", out.get("route") == "refuse")
    ok &= check("agent NOT called", "question" not in calls)

    print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
