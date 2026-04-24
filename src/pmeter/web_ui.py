from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pmeter.runner import Environment


_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PMeter Live Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     background:#0f172a;color:#e2e8f0}
header{background:#1e293b;border-bottom:1px solid #334155;padding:1rem 2rem;
       display:flex;align-items:center;gap:1rem}
header h1{color:#38bdf8;font-size:1.25rem;font-weight:700}
.dot{width:10px;height:10px;border-radius:50%;background:#4ade80;
     animation:pulse 1.4s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
main{padding:1.5rem;max-width:1400px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
       gap:.75rem;margin-bottom:1.5rem}
.card{background:#1e293b;border:1px solid #334155;border-radius:.75rem;
      padding:1rem;text-align:center}
.card .label{color:#94a3b8;font-size:.7rem;text-transform:uppercase;
             letter-spacing:.05em;margin-bottom:.375rem}
.card .value{font-size:1.5rem;font-weight:700}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));
        gap:1rem;margin-bottom:1.5rem}
.section{background:#1e293b;border:1px solid #334155;border-radius:.75rem;padding:1.25rem}
.section h2{font-size:.875rem;font-weight:600;color:#cbd5e1;margin-bottom:.75rem;
            border-bottom:1px solid #334155;padding-bottom:.5rem}
canvas{max-height:220px}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th{color:#94a3b8;font-weight:500;text-align:left;padding:.5rem .625rem;
   border-bottom:1px solid #334155}
td{padding:.5rem .625rem;border-bottom:1px solid #1e293b}
tr:hover td{background:#263148}
.fail{color:#f87171}
</style>
</head>
<body>
<header>
  <div class="dot" id="dot"></div>
  <h1>PMeter Live Dashboard</h1>
  <span style="color:#94a3b8;font-size:.8rem;margin-left:auto" id="elapsed">--</span>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="label">Requests</div><div class="value" id="c-req" style="color:#60a5fa">0</div></div>
    <div class="card"><div class="label">Failures</div><div class="value" id="c-fail" style="color:#f87171">0</div></div>
    <div class="card"><div class="label">Fail Rate</div><div class="value" id="c-rate" style="color:#fbbf24">0%</div></div>
    <div class="card"><div class="label">RPS</div><div class="value" id="c-rps" style="color:#4ade80">0</div></div>
    <div class="card"><div class="label">P50 ms</div><div class="value" id="c-p50" style="color:#4ade80">0</div></div>
    <div class="card"><div class="label">P95 ms</div><div class="value" id="c-p95" style="color:#fbbf24">0</div></div>
    <div class="card"><div class="label">P99 ms</div><div class="value" id="c-p99" style="color:#f87171">0</div></div>
  </div>

  <div class="charts">
    <div class="section">
      <h2>RPS History (last 60s)</h2>
      <canvas id="rpsChart"></canvas>
    </div>
    <div class="section">
      <h2>P95 Latency History (ms)</h2>
      <canvas id="latChart"></canvas>
    </div>
  </div>

  <div class="section">
    <h2>Request Stats</h2>
    <table>
      <thead><tr>
        <th>Name</th><th>Count</th><th>Failures</th>
        <th>Avg ms</th><th>P50 ms</th><th>P95 ms</th><th>P99 ms</th>
      </tr></thead>
      <tbody id="req-table"></tbody>
    </table>
  </div>
</main>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const MAX = 60;
const labels = Array.from({length:MAX}, (_,i)=> i - MAX + 1 + "s");
const rpsHist = new Array(MAX).fill(0);
const latHist = new Array(MAX).fill(0);

const gridC = "rgba(255,255,255,.07)";
const tickC = "#94a3b8";
const mkChart = (id, label, color, data) => new Chart(document.getElementById(id), {
  type:"line",
  data:{labels, datasets:[{label, data, borderColor:color, backgroundColor:color+"22",
        borderWidth:1.5, pointRadius:0, tension:.3, fill:true}]},
  options:{animation:false, responsive:true,
    plugins:{legend:{labels:{color:tickC}}},
    scales:{
      x:{ticks:{color:tickC, maxTicksLimit:10}, grid:{color:gridC}},
      y:{ticks:{color:tickC}, grid:{color:gridC}, min:0}
    }}
});

const rpsChart = mkChart("rpsChart","RPS","#4ade80", rpsHist);
const latChart = mkChart("latChart","P95 ms","#fbbf24", latHist);

let prevReq = 0, prevTime = Date.now(), started = null;

async function poll() {
  try {
    const r = await fetch("/api/stats");
    const d = await r.json();
    const now = Date.now();

    if (!started) started = now;
    document.getElementById("elapsed").textContent =
      "Elapsed: " + ((now - started)/1000).toFixed(0) + "s";

    const t = d.totals;
    document.getElementById("c-req").textContent = t.requests;
    document.getElementById("c-fail").textContent = t.failures;
    document.getElementById("c-rate").textContent = (t.failure_rate*100).toFixed(1)+"%";
    document.getElementById("c-p50").textContent = t.p50_ms.toFixed(1);
    document.getElementById("c-p95").textContent = t.p95_ms.toFixed(1);
    document.getElementById("c-p99").textContent = t.p99_ms.toFixed(1);

    const dt = (now - prevTime) / 1000;
    const rps = dt > 0 ? ((t.requests - prevReq) / dt).toFixed(1) : 0;
    document.getElementById("c-rps").textContent = rps;
    prevReq = t.requests;
    prevTime = now;

    rpsHist.push(parseFloat(rps)); rpsHist.shift();
    latHist.push(parseFloat(t.p95_ms.toFixed(1))); latHist.shift();
    rpsChart.update(); latChart.update();

    // Table
    const tbody = document.getElementById("req-table");
    tbody.innerHTML = d.entries.map(e =>
      `<tr>
        <td>${e.name}</td><td>${e.requests}</td>
        <td class="${e.failures?"fail":""}">${e.failures}</td>
        <td>${e.avg_ms.toFixed(1)}</td><td>${e.p50_ms.toFixed(1)}</td>
        <td>${e.p95_ms.toFixed(1)}</td><td>${e.p99_ms.toFixed(1)}</td>
      </tr>`
    ).join("");

    if (d.done) {
      document.getElementById("dot").style.background = "#94a3b8";
      document.getElementById("dot").style.animation = "none";
    }
  } catch(e) {}
}

poll();
setInterval(poll, 1000);
</script>
</body>
</html>
"""


class WebUIServer:
    """Embedded HTTP server that serves a live dashboard during a test run."""

    def __init__(self, environment: "Environment", port: int = 8089) -> None:
        self._environment = environment
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        env = self._environment
        server = HTTPServer(("", self._port), _make_handler(env))
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        print(f"\nWeb UI: http://localhost:{self._port}  (live dashboard)\n")

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()


def _make_handler(environment: "Environment"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body = _DASHBOARD_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/stats":
                data = _build_stats_json(environment)
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_):
            pass  # suppress access log

    return Handler


def _build_stats_json(environment: "Environment") -> dict:
    stats = environment.stats
    totals = stats.totals()
    entries = []
    for e in sorted(stats.snapshot(), key=lambda x: x.name):
        entries.append({
            "name": e.name,
            "requests": e.requests,
            "failures": e.failures,
            "avg_ms": round(e.avg_ms, 2),
            "p50_ms": round(e.percentile(0.50), 2),
            "p95_ms": round(e.percentile(0.95), 2),
            "p99_ms": round(e.percentile(0.99), 2),
        })
    return {
        "totals": totals,
        "entries": entries,
        "done": environment.stop_event.is_set(),
    }
