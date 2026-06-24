"""
this is a thin FastAPI surface over the agent graph.

Endpoints:
  GET  /         a minimal single-page chat UI
  POST /ask      streams the answer as Server-Sent Events (token + done)
  GET  /health   liveness probe

The route handler wraps the existing graph.stream_run(), so the SQL/exec/diagnosis
logic is unchanged - this layer only does HTTP + streaming. It is also the trusted
boundary where authentication/entitlement will live: can_view_pii must be resolved
here from a validated identity, never taken from the client (see the note in /ask).

Run:  ada-serve   (or: uvicorn ada.api.app:app)
"""
from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ada.observability import tracing
from ada.orchestrator.graph import stream_run


@asynccontextmanager
async def lifespan(app: FastAPI):
    # configure the span exporter once (ADA_TRACE=none|console|azure), flush on shutdown
    tracing.configure_from_env()
    yield
    tracing.shutdown()


app = FastAPI(title="Autonomous Data Analyst", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str


def _sse(event: str, obj: dict) -> str:
    # JSON-encode the payload so multi-line answer/SQL never breaks SSE framing
    return f"event: {event}\ndata: {json.dumps(obj)}\n\n"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/ask")
async def ask(req: AskRequest) -> StreamingResponse:
    return StreamingResponse(_ask_events(req.question), media_type="text/event-stream")


async def _ask_events(question: str):
    """Bridge the synchronous graph stream to an async SSE response.

    The whole of stream_run() is run in ONE worker thread and its tokens are pushed
    back over a queue. Keeping it on a single thread keeps the OpenTelemetry span
    context consistent: Starlette's default per-item threadpool iteration of a sync
    generator can attach a span's context on one thread and detach it on another,
    raising "Failed to detach context". One thread per request avoids that entirely.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()

    def produce():
        # SECURITY: entitlement must be resolved by a trusted auth layer from a validated
        # identity, NOT read from the request body. Default False so PII is refused.
        can_view_pii = False
        try:
            gen = stream_run(question, can_view_pii=can_view_pii)
            final: dict = {}
            while True:
                try:
                    token = next(gen)
                except StopIteration as stop:        # generator return value = final state
                    final = stop.value or {}
                    break
                loop.call_soon_threadsafe(queue.put_nowait, ("token", {"text": token}))
            loop.call_soon_threadsafe(queue.put_nowait, ("done", {
                "route": final.get("route"),
                "sql": final.get("sql"),
                "answer": final.get("answer"),       # refusal paths stream no tokens
            }))
        except Exception as exc:                     # surface errors instead of hanging
            loop.call_soon_threadsafe(queue.put_nowait, ("error", {"message": str(exc)}))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, DONE)

    threading.Thread(target=produce, name="ada-stream", daemon=True).start()

    while True:
        item = await queue.get()
        if item is DONE:
            break
        event, data = item
        yield _sse(event, data)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


def main():
    """Console entry point: ada-serve."""
    import uvicorn
    uvicorn.run("ada.api.app:app", host="127.0.0.1", port=8000, reload=False)


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Autonomous Data Analyst</title>
<style>
  :root { --fg:#1a1a1a; --muted:#6b7280; --line:#e5e7eb; --accent:#2563eb; --bg:#f9fafb; }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         color:var(--fg); background:var(--bg); }
  .wrap { max-width: 760px; margin: 0 auto; padding: 32px 20px 80px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: var(--muted); margin: 0 0 24px; font-size: 13px; }
  form { display:flex; gap:8px; }
  input[type=text] { flex:1; padding:12px 14px; border:1px solid var(--line); border-radius:10px;
                     font-size:15px; background:#fff; }
  input[type=text]:focus { outline:none; border-color:var(--accent); }
  button { padding:12px 18px; border:0; border-radius:10px; background:var(--accent); color:#fff;
           font-size:15px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .examples { margin:12px 0 0; display:flex; flex-wrap:wrap; gap:8px; }
  .examples button { background:#eef2ff; color:var(--accent); padding:6px 10px; font-size:13px; border-radius:8px; }
  .card { margin-top:24px; background:#fff; border:1px solid var(--line); border-radius:12px;
          padding:18px 20px; display:none; }
  .answer { white-space:pre-wrap; min-height:1.5em; }
  .meta { margin-top:14px; padding-top:12px; border-top:1px solid var(--line); color:var(--muted);
          font-size:13px; display:flex; align-items:center; gap:8px; }
  .pill { background:var(--bg); border:1px solid var(--line); border-radius:999px; padding:2px 10px;
          color:var(--fg); font-size:12px; }
  details { margin-top:10px; }
  summary { cursor:pointer; color:var(--accent); font-size:13px; }
  pre { background:#0f172a; color:#e2e8f0; padding:14px; border-radius:8px; overflow:auto; font-size:12.5px; }
  .cursor::after { content:"▋"; color:var(--muted); animation:blink 1s steps(1) infinite; }
  @keyframes blink { 50% { opacity:0; } }
</style>
</head>
<body>
<div class="wrap">
  <h1>Autonomous Data Analyst</h1>
  <p class="sub">Ask a question about the e-commerce warehouse. The answer streams as it's written.</p>

  <form id="f">
    <input id="q" type="text" autocomplete="off"
           placeholder="Why did sales drop last month?" />
    <button id="go" type="submit">Ask</button>
  </form>
  <div class="examples" id="ex">
    <button data-q="Why did sales drop last month?">Why did sales drop?</button>
    <button data-q="What was total GMV in May 2026?">Total GMV in May</button>
    <button data-q="Which region had the highest GMV in 2025?">Top region 2025</button>
  </div>

  <div class="card" id="card">
    <div class="answer cursor" id="answer"></div>
    <div class="meta" id="meta" style="display:none">
      <span>route</span><span class="pill" id="route"></span>
    </div>
    <details id="sqlBox" style="display:none">
      <summary>SQL</summary>
      <pre id="sql"></pre>
    </details>
  </div>
</div>

<script>
const f = document.getElementById('f'), q = document.getElementById('q'), go = document.getElementById('go');
const card = document.getElementById('card'), answer = document.getElementById('answer');
const meta = document.getElementById('meta'), routeEl = document.getElementById('route');
const sqlBox = document.getElementById('sqlBox'), sqlEl = document.getElementById('sql');

document.getElementById('ex').addEventListener('click', e => {
  if (e.target.dataset.q) { q.value = e.target.dataset.q; f.requestSubmit(); }
});

f.addEventListener('submit', async e => {
  e.preventDefault();
  const question = q.value.trim();
  if (!question) return;

  go.disabled = true;
  card.style.display = 'block';
  answer.textContent = '';
  answer.classList.add('cursor');
  meta.style.display = 'none';
  sqlBox.style.display = 'none';

  try {
    const res = await fetch('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question })
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let streamed = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\\n\\n')) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        let ev = 'message', data = '';
        for (const line of frame.split('\\n')) {
          if (line.startsWith('event:')) ev = line.slice(6).trim();
          else if (line.startsWith('data:')) data += line.slice(5).trim();
        }
        if (!data) continue;
        const obj = JSON.parse(data);
        if (ev === 'token') { answer.textContent += obj.text; streamed = true; }
        else if (ev === 'error') { answer.textContent = 'Error: ' + obj.message; }
        else if (ev === 'done') {
          if (!streamed && obj.answer) answer.textContent = obj.answer;
          if (obj.route) { routeEl.textContent = obj.route; meta.style.display = 'flex'; }
          if (obj.sql) { sqlEl.textContent = obj.sql; sqlBox.style.display = 'block'; }
        }
      }
    }
  } catch (err) {
    answer.textContent = 'Request failed: ' + err;
  } finally {
    answer.classList.remove('cursor');
    go.disabled = false;
  }
});
</script>
</body>
</html>"""
