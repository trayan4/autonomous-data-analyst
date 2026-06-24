"""Offline tests for the Azure Monitor export path (no real network).

Covers the wiring and the degrade-don't-crash behavior:
  - missing connection string -> no processor, a stderr note, no exception
  - valid (dummy) connection string -> a BatchSpanProcessor is built
  - configure('azure') + flush + shutdown runs cleanly with no spans queued

Real export to Application Insights can only be verified live (needs a connection
string and network); this asserts everything up to the network boundary.

    python observability/test_azure_export.py
"""
import os
import sys


from ada.observability import tracing

ok = True


def check(name, cond):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    ok = ok and cond


if not tracing._OTEL:
    print("OpenTelemetry not installed - skipping (PASS).")
    sys.exit(0)

DUMMY = ("InstrumentationKey=00000000-0000-0000-0000-000000000000;"
         "IngestionEndpoint=https://example.in.applicationinsights.azure.com/")

print("azure export wiring:")

# 1. no connection string -> graceful None
os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)
check("missing conn string -> no processor (no crash)", tracing._azure_processor() is None)

# 2. dummy connection string -> processor built (construction, no network)
os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = DUMMY
try:
    proc = tracing._azure_processor()
    built = proc is not None and type(proc).__name__ == "BatchSpanProcessor"
except Exception as e:  # pragma: no cover
    built = False
    print("    construction error:", e)
check("dummy conn string -> BatchSpanProcessor built", built)

# 3. configure('azure') + flush + shutdown is clean with nothing queued
mem = tracing.configure("azure", force=True)
check("configure('azure') returns None (not a memory exporter)", mem is None)
tracing.flush()
tracing.shutdown()
check("flush + shutdown ran without error", True)

os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
