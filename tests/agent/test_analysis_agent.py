"""Offline tests for the slim analysis agent (no model, no DB).

Verifies the three seams that make root-cause deliberate:
  - diagnose mode injects the decomposition directive into the SQL briefing
  - synthesize_diagnosis runs on the STRONG tier; synthesize_answer on CHEAP
  - analyze_question drives the shared chain with tier=strong, mode=diagnose,
    and the causal synthesizer, surfacing the driving region + category.

    python agent/test_analysis_agent.py
"""
import sys


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def main():
    ok = True

    # --- 1. diagnose mode injects the directive --------------------------------
    print("context diagnose mode:")
    from ada.agent.context_builder import build_context
    diag = build_context("why did sales drop", mode="diagnose")[0]["content"]
    ans = build_context("why did sales drop")[0]["content"]
    ok &= check("diagnose context carries DIAGNOSIS MODE", "DIAGNOSIS MODE" in diag)
    ok &= check("answer context does NOT", "DIAGNOSIS MODE" not in ans)
    ok &= check("requires region+category decomposition",
                "region AND" in diag and "category" in diag.lower())

    # --- 2. synth tier routing -------------------------------------------------
    print("\nsynthesizer tier routing:")
    from ada.agent import synthesizer
    seen = []
    synthesizer.chat = lambda messages, tier="cheap", **k: (seen.append(tier) or "ok")
    synthesizer.synthesize_diagnosis("q", ["a"], [(1,)])
    ok &= check("synthesize_diagnosis -> strong", seen[-1] == "strong")
    synthesizer.synthesize_answer("q", ["a"], [(1,)])
    ok &= check("synthesize_answer -> cheap", seen[-1] == "cheap")

    # --- 3. analyze_question wiring (mock generate + execute) ------------------
    print("\nanalyze_question wiring:")
    from ada.agent import data_agent
    from ada.agent import analysis_agent
    captured = {}

    class FakeGen:
        valid, pii_blocked, reasons = True, False, []
        sql = "SELECT region, category FROM ... -- decomposed"

    def fake_generate_sql(q, allow_pii=False, tier="strong", mode="answer", max_retries=1):
        captured["tier"], captured["mode"] = tier, mode
        return FakeGen()

    class FakeRes:
        ok = True
        columns = ["region", "category", "focal_gmv", "baseline_avg", "pct_change", "stockout"]
        rows = [("South", "Electronics", 5000.0, 200000.0, -0.975, 1),
                ("South", "Paid Search", 40000.0, 60000.0, -0.33, 0)]
        row_count = 2
        error = None

    def fake_run_sql(sql, max_rows=1000):
        return FakeRes()

    def fake_diag(question, columns, rows, max_rows=50):
        captured["synth"] = "diagnosis"
        return "Root cause: South Electronics fell ~97% during a stockout."

    data_agent.generate_sql = fake_generate_sql
    data_agent.run_sql = fake_run_sql
    analysis_agent.synthesize_diagnosis = fake_diag

    r = analysis_agent.analyze_question("Why did sales drop last month?")
    ok &= check("generate_sql called with tier=strong", captured.get("tier") == "strong")
    ok &= check("generate_sql called with mode=diagnose", captured.get("mode") == "diagnose")
    ok &= check("causal synthesizer used", captured.get("synth") == "diagnosis")
    ok &= check("answer names the region", "South" in r.answer)
    ok &= check("answer names the category", "Electronics" in r.answer)
    ok &= check("valid result returned", r.valid_sql and r.error is None)

    print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
