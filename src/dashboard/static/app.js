// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const fmt = (n, d=2) => (n==null || isNaN(n)) ? "—" : Number(n).toLocaleString("en-IN", {maximumFractionDigits:d});
const inr = (n) => (n==null || isNaN(n)) ? "—" : "₹" + Math.round(n).toLocaleString("en-IN");
const cls = (n) => n==null ? "" : (n >= 0 ? "pos" : "neg");
const toast = (msg) => { const t=$("toast"); t.textContent=msg; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"), 2200); };

// tabs
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");
  $("tab-"+b.dataset.tab).classList.add("active");
});

function table(rows, cols, opts={}) {
  if (!rows || !rows.length) return "<div style='color:var(--muted)'>No data.</div>";
  const head = cols.map(c => `<th>${c.title || c.key}</th>`).join("");
  const body = rows.map(r => "<tr>" + cols.map(c => {
    const v = r[c.key];
    if (c.fmt) return `<td class="${c.cls?c.cls(v):''}">${c.fmt(v, r)}</td>`;
    return `<td>${v ?? "—"}</td>`;
  }).join("") + "</tr>").join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

async function pollJob(jobId, onUpdate) {
  while (true) {
    const r = await fetch("/api/jobs/" + jobId).then(r => r.json());
    if (r.status === "done") return r.result;
    if (r.status === "error") throw new Error(r.error);
    onUpdate && onUpdate(r);
    await new Promise(res => setTimeout(res, 1200));
  }
}

function kpi(label, value, klass="") {
  return `<div class="kpi"><div class="label">${label}</div><div class="value ${klass}">${value}</div></div>`;
}

// ---------- portfolio ----------
let allocChart;
async function loadPortfolio() {
  toast("Loading portfolio …");
  const d = await fetch("/api/portfolio").then(r => r.json());
  if (d.error === "upstox_not_authenticated") {
    $("port-kpis").innerHTML =
      `<div class="kpi" style="flex:1"><div class="label">Upstox not authenticated</div>` +
      `<div class="value" style="font-size:14px;color:var(--muted)">${d.hint || "Run /upstox_login on Telegram, or python -m src.upstox.auth"}</div></div>`;
    $("port-holdings").innerHTML = ""; $("port-alloc").innerHTML = ""; $("risk-out").innerHTML = "";
    return;
  }
  const s = d.summary;
  $("port-kpis").innerHTML = [
    kpi("Invested", inr(s.holdings_invested)),
    kpi("Current value", inr(s.holdings_value)),
    kpi("Unrealised P&L", `${inr(s.holdings_pnl)} (${fmt(s.holdings_pnl_pct)}%)`, cls(s.holdings_pnl)),
    kpi("Day change", inr(s.day_change_value), cls(s.day_change_value)),
    kpi("Holdings", s.n_holdings),
    kpi("Positions", s.n_positions),
  ].join("");

  $("port-holdings").innerHTML = table(d.holdings, [
    {key:"tradingsymbol", title:"Symbol"},
    {key:"quantity", title:"Qty"},
    {key:"average_price", title:"Avg", fmt:v=>fmt(v)},
    {key:"last_price", title:"LTP", fmt:v=>fmt(v)},
    {key:"current_value", title:"Value", fmt:v=>inr(v)},
    {key:"pnl", title:"P&L", fmt:v=>inr(v), cls:cls},
    {key:"pnl_pct", title:"P&L %", fmt:v=>fmt(v)+"%", cls:cls},
  ]);

  // allocation chart
  const labels = d.allocation.map(r => Object.values(r)[0]);
  const data = d.allocation.map(r => r.current_value);
  if (allocChart) allocChart.destroy();
  allocChart = new Chart($("alloc-chart"), {
    type: "doughnut",
    data: {labels, datasets: [{data, backgroundColor: ["#2f81f7","#3fb950","#d29922","#f85149","#a371f7","#e3825c","#79c0ff","#56d364","#ff7b72","#bc8cff","#ffa657","#8ddb8c"]}]},
    options: {plugins: {legend: {position: "right", labels: {color: "#e6edf3", font: {size: 11}}}}}
  });
  $("port-alloc").innerHTML = table(d.allocation, [
    {key: Object.keys(d.allocation[0]||{})[0] || "x", title:"Bucket"},
    {key:"current_value", title:"Value", fmt:v=>inr(v)},
    {key:"pct", title:"%", fmt:v=>fmt(v)+"%"},
  ]);
}

async function deployCash() {
  const body = {
    cash: parseFloat($("deploy-cash").value),
    max_weight: parseFloat($("deploy-maxw").value),
    include_universe: $("deploy-universe").checked,
  };
  $("deploy-out").innerHTML = "<span class='spin'></span> optimising allocation …";
  const r = await fetch("/api/portfolio/deploy-cash", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  }).then(r => r.json());
  if (r.error) { $("deploy-out").innerHTML = `<span class='neg'>✗ ${r.error}</span>`; return; }

  const before = r.before, after = r.after, uplift = r.sharpe_uplift;
  const upColor = uplift > 0 ? "pos" : (uplift < 0 ? "neg" : "");
  const kpiHTML = `<div class="kpis" style="margin-bottom:10px">
    ${kpi("Cash deployed", inr(r.cash_to_deploy))}
    ${kpi("Sharpe before", fmt(before.sharpe, 3))}
    ${kpi("Sharpe after",  fmt(after.sharpe, 3), upColor)}
    ${kpi("Δ Sharpe",      (uplift>=0?"+":"") + fmt(uplift, 3), upColor)}
    ${kpi("E[R] after",    fmt(after.return_pct, 2) + "%", "pos")}
    ${kpi("Vol after",     fmt(after.vol_pct, 2) + "%")}
  </div>`;
  const buys = r.buys || [];
  const tbl = table(buys, [
    {key: "ticker", title: "Ticker"},
    {key: "buy_inr", title: "Buy (₹)", fmt: v => inr(v)},
    {key: "current_inr", title: "Currently held (₹)", fmt: v => v > 0 ? inr(v) : "—"},
    {key: "final_weight_pct", title: "Final weight", fmt: v => fmt(v, 2) + "%"},
    {key: "is_new_position", title: "New?",
     fmt: v => v ? `<span class="tag BUY">NEW</span>` : ""},
  ]);
  const note = r.universe_candidates_considered
    ? `<div style="color:var(--muted); font-size:12px; margin-top:6px">
         Considered ${buys.length} placements across your current holdings +
         ${r.universe_candidates_considered} STRONG_BUY candidates from the Universe Map.
       </div>` : "";
  $("deploy-out").innerHTML = kpiHTML + tbl + note;
}

async function loadRisk() {
  const d = await fetch("/api/portfolio/risk").then(r=>r.json());
  const conc = table(d.concentration, [
    {key:"tradingsymbol", title:"Symbol"},
    {key:"weight_pct", title:"Weight %", fmt:v=>fmt(v)+"%"},
    {key:"current_value", title:"Value", fmt:v=>inr(v)},
    {key:"pnl_pct", title:"P&L %", fmt:v=>fmt(v)+"%", cls:cls},
  ]);
  const losers = table(d.underperformers, [
    {key:"tradingsymbol", title:"Symbol"},
    {key:"average_price", title:"Avg", fmt:v=>fmt(v)},
    {key:"last_price", title:"LTP", fmt:v=>fmt(v)},
    {key:"pnl", title:"P&L", fmt:v=>inr(v), cls:cls},
    {key:"pnl_pct", title:"P&L %", fmt:v=>fmt(v)+"%", cls:cls},
  ]);
  $("risk-out").innerHTML = "<h4>Concentration</h4>" + conc + "<h4>Underperformers (&lt;-10%)</h4>" + losers;
}

async function sendTelegram() {
  toast("Sending Excel to Telegram …");
  const r = await fetch("/api/telegram/send-report", {method:"POST"}).then(r=>r.json());
  toast(r.ok ? "Sent ✓" : "Failed");
}

// ---------- optimize ----------
let frontierChart;
async function runOptimize() {
  $("opt-kpis").innerHTML = "<span class='spin'></span> optimising …";
  const body = {
    mode: $("opt-mode").value,
    target: parseFloat($("opt-target").value),
    max_weight: parseFloat($("opt-mw").value),
  };
  const {job_id} = await fetch("/api/portfolio/optimize", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}).then(r=>r.json());
  const r = await pollJob(job_id);
  if (r.error) { $("opt-kpis").innerHTML = "<span class='neg'>"+r.error+"</span>"; return; }
  $("opt-kpis").innerHTML = [
    kpi("Mode", r.mode),
    kpi("E[Return] p.a.", fmt(r.expected_return_pct)+"%", "pos"),
    kpi("Volatility p.a.", fmt(r.volatility_pct)+"%"),
    kpi("Sharpe", fmt(r.sharpe, 3)),
  ].join("");
  $("opt-weights").innerHTML = table(r.weights, [
    {key:"ticker", title:"Ticker"},
    {key:"weight_pct", title:"Weight %", fmt:v=>fmt(v)+"%"},
  ]);
  $("opt-rebal").innerHTML = table(r.rebalance, [
    {key:"ticker", title:"Ticker"},
    {key:"current_pct", title:"Now %", fmt:v=>fmt(v)+"%"},
    {key:"target_pct", title:"Target %", fmt:v=>fmt(v)+"%"},
    {key:"delta_pct", title:"Δ %", fmt:v=>fmt(v)+"%", cls:cls},
    {key:"delta_inr", title:"Δ INR", fmt:v=>inr(v), cls:cls},
    {key:"action", title:"Action", fmt:v=>`<span class="tag ${v==='BUY'?'BUY':v==='SELL'?'SELL':'HOLD'}">${v}</span>`},
  ]);

  if (frontierChart) frontierChart.destroy();

  const datasets = [
    {
      label: "Efficient Frontier",
      data: (r.frontier||[]).map(p => ({x: p.vol*100, y: p.return*100})),
      borderColor: "#2f81f7", backgroundColor: "#2f81f7",
      showLine: true, pointRadius: 3, tension: 0.15,
    },
    {
      label: "Optimal (target)",
      data: [{x: r.volatility_pct, y: r.expected_return_pct}],
      borderColor: "#d29922", backgroundColor: "#d29922",
      pointRadius: 10, pointStyle: "star",
    },
  ];

  if (r.current_portfolio) {
    datasets.push({
      label: `Your portfolio (Sharpe ${r.current_portfolio.sharpe})`,
      data: [{x: r.current_portfolio.vol_pct, y: r.current_portfolio.return_pct}],
      borderColor: "#f85149", backgroundColor: "#f85149",
      pointRadius: 9, pointStyle: "rectRot",
    });
  }

  if (r.per_name && r.per_name.length) {
    datasets.push({
      label: "Individual holdings",
      data: r.per_name.map(p => ({x: p.vol*100, y: p.return*100, ticker: p.ticker, w: p.weight_pct})),
      backgroundColor: "rgba(139,148,158,.55)",
      borderColor: "rgba(139,148,158,.7)",
      pointRadius: 4,
    });
  }

  frontierChart = new Chart($("frontier-chart"), {
    type: "scatter",
    data: {datasets},
    options: {
      scales: {
        x: {title: {display: true, text:"Volatility (% p.a.)", color:"#8b949e"}, ticks: {color:"#8b949e"}, grid:{color:"#2a3140"}},
        y: {title: {display: true, text:"Expected Return (% p.a.)", color:"#8b949e"}, ticks: {color:"#8b949e"}, grid:{color:"#2a3140"}}
      },
      plugins: {
        legend: {labels: {color:"#e6edf3"}},
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const d = ctx.raw;
              const base = `${ctx.dataset.label}: vol ${d.x.toFixed(2)}%, ret ${d.y.toFixed(2)}%`;
              return d.ticker ? `${d.ticker} (${d.w}%) — ${base}` : base;
            }
          }
        }
      }
    }
  });

  // Caption: tell user where they sit vs frontier
  if (r.current_portfolio) {
    const gap_ret = (r.expected_return_pct - r.current_portfolio.return_pct).toFixed(2);
    const gap_vol = (r.current_portfolio.vol_pct - r.volatility_pct).toFixed(2);
    const note = document.createElement("div");
    note.style.cssText = "color:var(--muted); font-size:12px; margin-top:6px";
    note.innerHTML = `Your portfolio (◆ red) vs optimal (★ yellow): ` +
      `${gap_ret>0?"+":""}${gap_ret}% return  ·  ` +
      `${gap_vol>0?"-":"+"}${Math.abs(gap_vol)}% volatility achievable at the optimum.`;
    $("frontier-chart").parentNode.appendChild(note);
  }
}

// ---------- screener ----------
async function runScreener() {
  $("scr-status").innerHTML = "<span class='spin'></span> running funnel scan …";
  const body = {
    universe: $("scr-uni").value,
    tech_min: parseFloat($("scr-tech").value),
    fund_min: parseFloat($("scr-fund").value),
  };
  const {job_id} = await fetch("/api/screener/scan", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}).then(r=>r.json());
  const r = await pollJob(job_id);
  $("scr-status").textContent = `Technical survivors: ${r.technical_count} • Final picks: ${r.final_count}`;
  $("scr-out").innerHTML = table(r.results, [
    {key:"name", title:"Name"},
    {key:"symbol", title:"Symbol"},
    {key:"ltp", title:"LTP", fmt:v=>fmt(v)},
    {key:"combined", title:"Score", fmt:v=>fmt(v,1)},
    {key:"tech_score", title:"Tech", fmt:v=>fmt(v,1)},
    {key:"fund_score", title:"Fund", fmt:v=>fmt(v,1)},
    {key:"recommendation", title:"Reco", fmt:v=>`<span class="tag ${v}">${v}</span>`},
    {key:"rsi", title:"RSI", fmt:v=>fmt(v,1)},
    {key:"ret_3m_pct", title:"3m %", fmt:v=>fmt(v,2)+"%", cls:cls},
    {key:"PE", title:"P/E", fmt:v=>fmt(v,1)},
    {key:"ROE", title:"ROE", fmt:v=>v==null?"—":fmt(v*100,1)+"%"},
    {key:"sector", title:"Sector"},
  ]);
}

// ---------- intraday ----------
async function runIntradayAnalyze() {
  $("intra-kpis").innerHTML = "<span class='spin'></span> analyzing …";
  const {job_id} = await fetch("/api/intraday/analyze", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({days:60})}).then(r=>r.json());
  const r = await pollJob(job_id);
  if (r.error) { $("intra-kpis").innerHTML = "<span class='neg'>"+r.error+"</span>"; return; }
  $("intra-kpis").innerHTML = [
    kpi("Trades", r.trades),
    kpi("Win-rate", fmt(r.win_rate_pct)+"%"),
    kpi("Profit factor", r.profit_factor ?? "—"),
    kpi("Expectancy", inr(r.expectancy), cls(r.expectancy)),
    kpi("Total P&L", inr(r.total_pnl), cls(r.total_pnl)),
  ].join("");
  $("intra-bw").innerHTML = "<h4>Best</h4>" + table(r.best, [
    {key:"date"}, {key:"symbol"}, {key:"pnl", fmt:v=>inr(v), cls:cls}, {key:"pnl_pct", fmt:v=>fmt(v)+"%", cls:cls},
  ]) + "<h4>Worst</h4>" + table(r.worst, [
    {key:"date"}, {key:"symbol"}, {key:"pnl", fmt:v=>inr(v), cls:cls}, {key:"pnl_pct", fmt:v=>fmt(v)+"%", cls:cls},
  ]);
  $("intra-sym").innerHTML = table(r.by_symbol, [
    {key:"symbol"}, {key:"count", title:"#"}, {key:"sum", title:"Σ P&L", fmt:v=>inr(v), cls:cls}, {key:"mean", title:"avg", fmt:v=>inr(v), cls:cls},
  ]);
  $("intra-mistakes").innerHTML = (r.mistakes||[]).map(m => `<div>• ${m}</div>`).join("") || "<div style='color:var(--muted)'>None flagged.</div>";
}

async function runIntradayScan() {
  $("intra-scan").innerHTML = "<span class='spin'></span> scanning …";
  const {job_id} = await fetch("/api/intraday/scan", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({universe:"nifty50", min_score:40})}).then(r=>r.json());
  const r = await pollJob(job_id);
  $("intra-scan").innerHTML = table(r.rows, [
    {key:"symbol"},
    {key:"direction", fmt:v=>`<span class="tag ${v}">${v}</span>`},
    {key:"score"},
    {key:"ltp", fmt:v=>fmt(v)},
    {key:"entry", fmt:v=>fmt(v)},
    {key:"stop", fmt:v=>fmt(v)},
    {key:"target_1R", title:"T1", fmt:v=>fmt(v)},
    {key:"target_2R", title:"T2", fmt:v=>fmt(v)},
    {key:"risk_per_share", title:"Risk/sh", fmt:v=>fmt(v)},
    {key:"signals", title:"Signals", fmt:v=>(v||[]).join(", ")},
  ]);
}

// ---------- D-R1-Quant ----------
async function killQuant() {
  const id = window._lastQuantJob;
  if (!id) { toast("No run to kill."); return; }
  const r = await fetch("/api/quant/kill/" + id, {method:"POST"}).then(r=>r.json());
  toast(r.ok ? "⏹ kill signal sent" : ("✗ " + r.error));
}

async function loadMacro() {
  $("macro-kpis").innerHTML = "<span class='spin'></span> fetching macro …";
  const m = await fetch("/api/quant/macro").then(r=>r.json());
  const klass = m.mode === "BULLISH" ? "pos" : (m.mode === "BEARISH" ? "neg" : "");
  $("macro-kpis").innerHTML = [
    kpi("Market Mode", m.mode, klass),
    kpi("India VIX", fmt(m.india_vix, 2)),
    kpi("Nifty PCR", fmt(m.nifty_pcr, 2)),
    kpi("USD/INR", fmt(m.usdinr, 2)),
    kpi("Nifty %", (m.nifty_change_pct>=0?"+":"")+fmt(m.nifty_change_pct,2)+"%", cls(m.nifty_change_pct)),
  ].join("") + "<div style='color:var(--muted);margin-top:8px'>" +
    (m.reasons||[]).map(r=>"• "+r).join("<br>") + "</div>";
}

function appendDebug(lines) {
  if (!lines || !lines.length) return;
  const box = $("quant-debug");
  if (box.dataset.fresh !== "1") { box.innerHTML = ""; box.dataset.fresh = "1"; }
  const levelColor = {DEBUG:"#6e7681", INFO:"#c9d1d9", WARNING:"#d29922", ERROR:"#f85149"};
  const frag = document.createDocumentFragment();
  for (const l of lines) {
    const div = document.createElement("div");
    const t = (l.ts||"").split("T")[1]?.replace("Z","") || "";
    div.innerHTML =
      `<span style="color:#6e7681">${t}</span> ` +
      `<span style="color:${levelColor[l.level]||'#c9d1d9'}">${l.level}</span> ` +
      `<span style="color:#79c0ff">${l.logger}</span> ` +
      `${escapeHtml(l.msg)}`;
    frag.appendChild(div);
  }
  box.appendChild(frag);
  if ($("quant-debug-autoscroll").checked) box.scrollTop = box.scrollHeight;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
}

// Global token so a new run cancels any in-flight streamer from a previous run.
let _activeStreamerToken = 0;

async function streamQuantLog(job_id) {
  const myToken = ++_activeStreamerToken;
  let cursor = 0;
  let unknownCount = 0;

  while (myToken === _activeStreamerToken) {
    // 1. Pull any new log lines
    const r = await fetch(`/api/quant/log/${job_id}?since=${cursor}`)
                .then(r => r.ok ? r.json() : null).catch(() => null);
    if (r) { cursor = r.next; appendDebug(r.lines); }

    // 2. Check job status — exit on done / error / unknown / network failure
    const resp = await fetch("/api/jobs/" + job_id);
    if (!resp.ok) {
      unknownCount++;
      if (unknownCount > 3) {
        appendDebug([{ts:"", level:"WARNING", logger:"streamer",
                      msg: `job ${job_id} no longer known by the server — stopping streamer. ` +
                           `(Probably from a previous dashboard restart — refresh the page.)`}]);
        return;
      }
    } else {
      unknownCount = 0;
      const j = await resp.json();
      if (j.status === "done" || j.status === "error") {
        const tail = await fetch(`/api/quant/log/${job_id}?since=${cursor}`)
                        .then(r => r.ok ? r.json() : null).catch(() => null);
        if (tail) appendDebug(tail.lines);
        appendDebug([{
          ts: "", level: j.status === "error" ? "ERROR" : "INFO", logger: "job",
          msg: j.status === "error" ? `Job failed: ${j.error || "(no message)"}` : "✓ Done.",
        }]);
        return;
      }
    }

    await new Promise(res => setTimeout(res, 800));
  }
  // superseded — silent exit (a newer streamer is now active)
}

async function runQuant() {
  _activeStreamerToken++;          // cancel any in-flight streamer from a prior run
  $("quant-validated").innerHTML = "<span class='spin'></span> running funnel — Stage 2 calls the local LLM per ticker, watch the debug pane …";
  $("quant-weights").innerHTML = "";
  $("quant-alerts").innerHTML = "";
  $("quant-rejected").innerHTML = "";
  $("quant-debug").innerHTML = "<span style='color:var(--muted)'>spinning up …</span>";
  $("quant-debug").dataset.fresh = "0";

  const {job_id, pid} = await fetch("/api/quant/run", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({universe:$("quant-uni").value})}).then(r=>r.json());
  window._lastQuantJob = job_id;
  $("quant-job-meta").textContent = `job ${job_id}${pid?` · pid ${pid}`:""}`;
  appendDebug([{ts:"", level:"INFO", logger:"job", msg:`started job ${job_id} (subprocess pid ${pid||"?"})`}]);
  streamQuantLog(job_id);   // log streamer runs in parallel
  let r;
  try {
    r = await pollJob(job_id);
  } catch (e) {
    $("quant-validated").innerHTML = `<span class="neg">Run failed: ${e.message}. See debug pane above.</span>`;
    return;
  }

  $("quant-validated").innerHTML = table(r.validated||[], [
    {key:"symbol"},
    {key:"verdict", fmt:v=>`<span class="tag ${v==='KEEP'?'BUY':'SELL'}">${v}</span>`},
    {key:"health_score", title:"Health", fmt:v=>fmt(v,1)},
    {key:"debt_to_equity", title:"D/E", fmt:v=>fmt(v,2)},
    {key:"free_cash_flow_cr", title:"FCF (cr)", fmt:v=>fmt(v,1)},
    {key:"promoter_pledging_pct", title:"Pledge %", fmt:v=>fmt(v,1)+"%"},
    {key:"thesis"},
  ]);

  const p = r.portfolio || {};
  if (p.weights_pct) {
    const rows = Object.entries(p.weights_pct).map(([k,v])=>({ticker:k, weight_pct:v}));
    $("quant-weights").innerHTML =
      `<div class="kpis">${kpi("E[R]", fmt(p.expected_return_pct)+"%", "pos")}${kpi("Vol", fmt(p.volatility_pct)+"%")}${kpi("Sharpe", fmt(p.sharpe,3))}</div>` +
      table(rows, [{key:"ticker"}, {key:"weight_pct", fmt:v=>fmt(v,2)+"%"}]);
  } else {
    $("quant-weights").innerHTML = "<div style='color:var(--muted)'>"+(p.error||"no portfolio")+"</div>";
  }

  $("quant-alerts").innerHTML = table(r.intraday_alerts||[], [
    {key:"symbol"}, {key:"direction", fmt:v=>`<span class="tag ${v}">${v}</span>`},
    {key:"entry", fmt:v=>fmt(v)}, {key:"stop", fmt:v=>fmt(v)},
    {key:"target_2R", title:"T2", fmt:v=>fmt(v)},
    {key:"signals", fmt:v=>(v||[]).join(", ")},
  ]);

  $("quant-rejected").textContent = "Rejected: " + ((r.rejected||[]).join(", ") || "none");
}

// ---------- Universe Map ----------
let umapChart;

function umapColor(reco) {
  return ({
    "STRONG_BUY": "#3fb950",
    "BUY": "#56d364",
    "TECH_BUY": "#79c0ff",
    "TECH_WATCH": "#d29922",
    "HOLD": "#8b949e",
    "AVOID": "#bc8cff",
    "SELL": "#f85149",
    "INSUFFICIENT_DATA": "rgba(139,148,158,.35)",
  })[reco] || "#8b949e";
}

function umapRender(stocks, meta) {
  // Center the axes on score=50 so the chart becomes a TRUE four-quadrant
  // map (origin in the middle, ±50 around it). Scores stay 0-100 in tooltips
  // for readability; the plot uses shifted coords.
  const CENTER = 50;
  const SHIFT = (v) => (v == null ? null : v - CENTER);

  const groups = {};
  for (const s of stocks) {
    if (s.tech_score == null) continue;
    const reco = s.recommendation || "HOLD";
    if (!groups[reco]) groups[reco] = [];
    // Stocks with no fund data: park at x = -CENTER - 10 (the far-left margin)
    // so they're visually segregated, not mixed into "low fundamentals".
    const fund_shifted = s.fund_score == null ? -(CENTER + 10) : SHIFT(s.fund_score);
    groups[reco].push({
      x: fund_shifted,
      y: SHIFT(s.tech_score),
      x_real: s.fund_score,    // original 0-100 values for tooltips
      y_real: s.tech_score,
      symbol: s.symbol, name: s.name, ltp: s.ltp,
      pe: s.PE, roe: s.ROE, sector: s.sector,
      combined: s.combined, reco,
    });
  }
  const datasets = Object.entries(groups).map(([reco, data]) => ({
    label: `${reco} (${data.length})`,
    data,
    backgroundColor: umapColor(reco),
    pointRadius: reco === "INSUFFICIENT_DATA" ? 2 : 4,
    pointHoverRadius: 7,
  }));

  // Quadrant background plugin — paints four tinted regions + heavy axis lines
  const quadrantBG = {
    id: "quadrantBG",
    beforeDraw(chart) {
      const {ctx, chartArea, scales} = chart;
      if (!chartArea) return;
      const x0 = scales.x.getPixelForValue(0);
      const y0 = scales.y.getPixelForValue(0);
      ctx.save();
      // Top-right (high fund + high tech) — green tint = BUY zone
      ctx.fillStyle = "rgba(63,185,80,0.05)";
      ctx.fillRect(x0, chartArea.top, chartArea.right - x0, y0 - chartArea.top);
      // Top-left (low fund + high tech) — amber = momentum
      ctx.fillStyle = "rgba(210,153,34,0.05)";
      ctx.fillRect(chartArea.left, chartArea.top, x0 - chartArea.left, y0 - chartArea.top);
      // Bottom-right (high fund + low tech) — blue = value
      ctx.fillStyle = "rgba(121,192,255,0.05)";
      ctx.fillRect(x0, y0, chartArea.right - x0, chartArea.bottom - y0);
      // Bottom-left (low + low) — red = avoid
      ctx.fillStyle = "rgba(248,81,73,0.05)";
      ctx.fillRect(chartArea.left, y0, x0 - chartArea.left, chartArea.bottom - y0);
      // axis lines through origin
      ctx.strokeStyle = "#6e7681";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(chartArea.left, y0); ctx.lineTo(chartArea.right, y0);
      ctx.moveTo(x0, chartArea.top);  ctx.lineTo(x0, chartArea.bottom);
      ctx.stroke();
      // Quadrant labels
      ctx.fillStyle = "rgba(255,255,255,0.18)";
      ctx.font = "bold 11px -apple-system, sans-serif";
      ctx.fillText("📈  BUY (high quality + momentum)",
                   x0 + 12, chartArea.top + 18);
      ctx.fillText("⚡  Momentum-only",
                   chartArea.left + 12, chartArea.top + 18);
      ctx.fillText("💰  Value / contrarian",
                   x0 + 12, chartArea.bottom - 10);
      ctx.fillText("🚫  Avoid",
                   chartArea.left + 12, chartArea.bottom - 10);
      ctx.restore();
    },
  };

  if (umapChart) umapChart.destroy();
  umapChart = new Chart($("umap-chart"), {
    type: "scatter",
    data: {datasets},
    plugins: [quadrantBG],
    options: {
      responsive: true,
      plugins: {
        legend: {labels: {color:"#e6edf3", font: {size: 11}}},
        tooltip: {
          callbacks: {
            label: ctx => {
              const d = ctx.raw;
              const fund = d.x_real == null ? "—" : d.x_real;
              return `${d.symbol} (${d.reco}) — tech ${d.y_real}, fund ${fund} · ` +
                     `PE ${d.pe??"—"} · ROE ${d.roe!=null?(d.roe*100).toFixed(1)+"%":"—"} · ${d.sector||"—"}`;
            },
          },
        },
      },
      scales: {
        x: {
          title: {display: true, text:"← weak fundamentals     |     strong fundamentals →",
                  color:"#8b949e"},
          min: -55, max: 55,
          ticks: {color:"#8b949e",
                  // show ACTUAL score in tick labels (add CENTER back)
                  callback: v => v < -(CENTER) ? "n/a" : (v + CENTER)},
          grid: {color:"#2a3140"},
        },
        y: {
          title: {display: true, text:"← weak technicals     |     strong technicals →",
                  color:"#8b949e"},
          min: -55, max: 55,
          ticks: {color:"#8b949e", callback: v => v + CENTER},
          grid: {color:"#2a3140"},
        },
      },
      onClick: (evt, items) => {
        if (!items.length) return;
        const e = items[0];
        const d = umapChart.data.datasets[e.datasetIndex].data[e.index];
        $("umap-detail").innerHTML = `
          <div class="card" style="margin:0">
            <h4 style="margin:0 0 6px">${d.symbol} <span style="color:var(--muted)">${d.name||""}</span></h4>
            <div style="font-size:13px; color:var(--muted)">
              Reco <span class="tag ${d.reco==='STRONG_BUY'||d.reco==='BUY'?'BUY':d.reco==='SELL'||d.reco==='AVOID'?'SELL':'HOLD'}">${d.reco}</span>
              · Combined ${d.combined ?? "—"} · Tech ${d.y_real} · Fund ${d.x_real ?? "—"}
            </div>
            <div style="font-size:13px; margin-top:4px">
              LTP ₹${d.ltp ?? "—"} · P/E ${d.pe ?? "—"} ·
              ROE ${d.roe!=null?(d.roe*100).toFixed(1)+"%":"—"} ·
              Sector ${d.sector || "—"}
            </div>
          </div>`;
      },
    },
  });

  // Quadrant tables
  const sortByCombined = (a, b) => (b.combined ?? -1) - (a.combined ?? -1);
  const sortByTech = (a, b) => b.tech_score - a.tech_score;
  const sortByFund = (a, b) => (b.fund_score ?? -1) - (a.fund_score ?? -1);

  const valid = stocks.filter(s => s.tech_score != null);
  const T = 50, F = 50;   // quadrant thresholds — match the chart's 4-quadrant split
  const best = valid.filter(s => s.tech_score >= T && (s.fund_score ?? 0) >= F).sort(sortByCombined).slice(0, 12);
  const momo = valid.filter(s => s.tech_score >= T && (s.fund_score ?? 0) < F).sort(sortByTech).slice(0, 12);
  const value = valid.filter(s => s.tech_score < T && (s.fund_score ?? 0) >= F).sort(sortByFund).slice(0, 12);
  const avoid = valid.filter(s => s.tech_score < T && (s.fund_score ?? 0) < F).sort((a,b) => a.tech_score - b.tech_score).slice(0, 12);

  const cols = [
    {key:"symbol"},
    {key:"tech_score", title:"Tech", fmt:v=>fmt(v,1)},
    {key:"fund_score", title:"Fund", fmt:v=>fmt(v,1)},
    {key:"PE", title:"PE", fmt:v=>fmt(v,1)},
    {key:"ROE", title:"ROE", fmt:v=>v==null?"—":fmt(v*100,1)+"%"},
    {key:"sector"},
  ];
  $("umap-q-best").innerHTML = table(best, cols);
  $("umap-q-momo").innerHTML = table(momo, cols);
  $("umap-q-value").innerHTML = table(value, cols);
  $("umap-q-avoid").innerHTML = table(avoid, cols);

  $("umap-meta").textContent =
    `${meta.count} stocks · ${meta.tech_total} technical · ${meta.fund_scanned||0} fund-scored · ` +
    `built ${(meta.built_at||"").slice(0, 19).replace("T", " ")} UTC`;
}

async function umapLoad() {
  $("umap-meta").innerHTML = "<span class='spin'></span> loading cached…";
  const universe = $("umap-uni").value;
  const r = await fetch(`/api/universe-map/data?universe=${universe}`).then(r=>r.json());
  if (!r.ok) {
    $("umap-meta").innerHTML = `<span class="neg">${r.error}</span>`;
    return;
  }
  if (r.error) {
    // Build completed but emitted an error (e.g. Upstox auth)
    $("umap-meta").innerHTML = `<span class="neg">⚠ ${escapeHtml(r.error.split('\n')[0])}</span>`;
    $("umap-detail").innerHTML =
      `<div class="card" style="margin:0; border-color:var(--neg)">
         <h4 style="margin:0 0 8px; color:var(--neg)">Build aborted</h4>
         <pre style="white-space:pre-wrap; font-size:12px; color:var(--muted); margin:0">${escapeHtml(r.error)}</pre>
       </div>`;
    return;
  }
  umapRender(r.stocks, r);
}

function umapDownload(fmt) {
  const universe = $("umap-uni").value;
  window.open(`/api/universe-map/report?universe=${universe}&fmt=${fmt}`, "_blank");
}

async function umapReset() {
  if (!confirm("Wipe instrument cache, blacklist, daily candles, screener.in cache, and universe-map cache?\n\nThe next build will redownload everything fresh.")) return;
  toast("🧹 clearing caches …");
  const r = await fetch("/api/universe-map/reset", {method:"POST"}).then(r=>r.json());
  toast(r.ok ? `✓ cleared: ${r.cleared.join(", ")}` : "✗ failed");
  $("umap-meta").textContent = "Cache cleared. Click 🛠 Build map to repopulate.";
  if (umapChart) { umapChart.destroy(); umapChart = null; }
  $("umap-q-best").innerHTML = ""; $("umap-q-momo").innerHTML = "";
  $("umap-q-value").innerHTML = ""; $("umap-q-avoid").innerHTML = "";
  $("umap-detail").innerHTML = "";
}

// Generation token — a new build cancels any in-flight streamer from before.
let _umapStreamToken = 0;

async function umapBuild() {
  const body = {universe: $("umap-uni").value, max_age_days: parseFloat($("umap-maxage").value)};
  $("umap-log").innerHTML = "<span class='spin'></span> spawning build subprocess …";
  const r = await fetch("/api/universe-map/build", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}).then(r=>r.json());
  if (!r.ok) { toast("✗ " + r.error); return; }
  toast(`🛠 building (pid ${r.pid}) — watch the log below`);

  const myToken = ++_umapStreamToken;   // supersede any older streamer
  let cursor = 0;
  let unknownCount = 0;
  $("umap-log").innerHTML = "";

  while (myToken === _umapStreamToken) {
    // 1. tail the log
    const lgResp = await fetch(`/api/universe-map/log/${r.job_id}?since=${cursor}`).catch(()=>null);
    if (lgResp && lgResp.ok) {
      const lg = await lgResp.json().catch(()=>null);
      if (lg) {
        cursor = lg.next;
        if (lg.lines && lg.lines.length) {
          const box = $("umap-log");
          for (const ln of lg.lines) {
            const div = document.createElement("div");
            div.textContent = ln.msg;
            box.appendChild(div);
          }
          box.scrollTop = box.scrollHeight;
        }
      }
    }
    // 2. poll job state — exit on done / error / unknown(404) / network fail
    const jResp = await fetch(`/api/universe-map/job/${r.job_id}`).catch(()=>null);
    if (!jResp || !jResp.ok) {
      // 404 = job no longer known (dashboard restarted, or old job). Bail out
      // after a few misses instead of polling a dead id forever.
      if (++unknownCount > 3) {
        $("umap-log").appendChild(Object.assign(document.createElement("div"),
          {textContent: `[streamer] job ${r.job_id} no longer known — stopping. ` +
                        `(refresh the page if you restarted the dashboard)`}));
        return;
      }
    } else {
      unknownCount = 0;
      const j = await jResp.json().catch(()=>({}));
      if (j.status === "done") { toast("✓ map built"); umapLoad(); return; }
      if (j.status === "error") { toast("✗ build failed — see log"); return; }
    }
    await new Promise(res => setTimeout(res, 1500));
  }
  // superseded by a newer build — exit quietly
}

document.querySelector('[data-tab="umap"]')?.addEventListener("click", () => {
  if (!window._umapInited) { window._umapInited = true; umapLoad(); }
});

// ---------- agent ----------
async function runAgent() {
  const out = $("agent-out");
  out.innerHTML = "<span class='spin'></span> thinking …";
  const body = {agent: $("agent-pick").value, question: $("agent-q").value};
  const {job_id} = await fetch("/api/agent", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}).then(r=>r.json());
  const r = await pollJob(job_id);
  out.textContent = r.answer || JSON.stringify(r, null, 2);
}

// ---------- Upstox in-browser auth ----------
async function openUpstoxLogin() {
  const r = await fetch("/api/upstox/auth-url").then(r=>r.json());
  if (!r.ok) {
    $("upstox-auth-state").innerHTML = `<span class="neg">${r.error}</span>`;
    return;
  }
  $("upstox-auth-state").innerHTML = `Redirect URI: <code>${r.redirect_uri}</code> — paste the URL Upstox sends you back to.`;
  window.open(r.url, "_blank", "noopener");
}

async function submitUpstoxCode() {
  const val = $("upstox-code").value.trim();
  if (!val) { toast("Paste the redirected URL first."); return; }
  $("upstox-auth-result").innerHTML = "<span class='spin'></span> exchanging code …";
  const r = await fetch("/api/upstox/exchange-code", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({code_or_url: val}),
  }).then(r=>r.json());
  if (r.ok) {
    $("upstox-auth-result").innerHTML = `<span class="pos">✅ Authenticated as <b>${r.user}</b>. Token cached.</span>`;
    $("upstox-code").value = "";
    toast("Upstox authenticated ✓");
    loadStatus(); loadPortfolio();
  } else {
    $("upstox-auth-result").innerHTML = `<span class="neg">❌ ${r.error}</span>`;
  }
}

// ---------- knowledge base ----------
async function kbStats() {
  const r = await fetch("/api/kb/stats").then(r=>r.json());
  if (!r.ok) {
    $("kb-kpis").innerHTML = `<div class="kpi"><div class="label">KB</div><div class="value neg" style="font-size:14px">${r.error}</div></div>`;
    return;
  }
  $("kb-kpis").innerHTML = [
    kpi("Books / documents", r.documents),
    kpi("Doc chunks", r.chunks),
    kpi("Agent decisions captured", r.decisions || 0),
    kpi("Universe stocks ingested", r.universe_stocks || 0),
    kpi("Search mode", r.search_mode || "—",
        r.semantic_search ? "pos" : ""),
  ].join("");
}

async function kbSearchDecisions() {
  const q = $("kb-dec-q").value.trim();
  if (!q) return;
  $("kb-dec-out").innerHTML = "<span class='spin'></span> searching …";
  const r = await fetch("/api/kb/search-decisions", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({query:q, k:5})}).then(r=>r.json());
  const rows = r.results || [];
  if (!rows.length) {
    $("kb-dec-out").innerHTML = "<div style='color:var(--muted)'>No decisions captured yet — run the D-R1-Quant funnel first.</div>";
    return;
  }
  $("kb-dec-out").innerHTML = rows.map(x => `
    <div style="border-left:3px solid var(--accent); padding:6px 12px; margin-bottom:8px; background:var(--panel-2); border-radius:4px">
      <div style="font-size:11px"><b>${x.symbol||"?"}</b> · <span class="tag ${x.verdict==='KEEP'?'BUY':'SELL'}">${x.verdict||"?"}</span> · macro=${x.macro_mode||"?"} · health=${x.health_score||"?"}</div>
      <div style="margin-top:4px; font-size:12px; color:var(--muted); white-space:pre-wrap">${escapeHtml((x.text||"").slice(0, 700))}${(x.text||"").length>700?"…":""}</div>
    </div>
  `).join("");
}

async function kbRefresh() {
  kbStats();
  const r = await fetch("/api/kb/documents").then(r=>r.json());
  const docs = r.documents || [];
  $("kb-docs").innerHTML = docs.length
    ? table(docs, [
        {key:"title", title:"Title"},
        {key:"source", title:"Source"},
        {key:"chunks", title:"# chunks"},
        {key:"doc_id", title:"", fmt: id => `<button onclick="kbDelete('${id}')" style="font-size:11px">delete</button>`},
      ])
    : "<div style='color:var(--muted)'>Nothing indexed yet — upload a PDF above.</div>";
}

async function kbUpload() {
  const file = $("kb-file").files[0];
  if (!file) { toast("Pick a file first."); return; }
  $("kb-upload-state").innerHTML = `<span class='spin'></span> uploading ${file.name} …`;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", $("kb-title").value);
  const r = await fetch("/api/kb/upload", {method:"POST", body:fd}).then(r=>r.json());
  if (r.ok) {
    $("kb-upload-state").innerHTML = `<span class="pos">✓ indexed ${r.chunks_indexed} chunks from "${r.title}"</span>`;
    $("kb-file").value = ""; $("kb-title").value = "";
    kbRefresh();
  } else {
    $("kb-upload-state").innerHTML = `<span class="neg">✗ ${r.error}</span>`;
  }
}

async function kbIngestText() {
  const body = {
    title: $("kb-snippet-title").value || "untitled",
    text: $("kb-snippet-text").value,
  };
  if (!body.text.trim()) { toast("Paste some text first."); return; }
  const r = await fetch("/api/kb/ingest-text", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}).then(r=>r.json());
  if (r.ok) {
    toast(`✓ indexed ${r.chunks_indexed} chunks`);
    $("kb-snippet-title").value = ""; $("kb-snippet-text").value = "";
    kbRefresh();
  } else { toast("✗ " + r.error); }
}

async function kbSearch() {
  const q = $("kb-search-q").value.trim();
  if (!q) return;
  $("kb-search-out").innerHTML = "<span class='spin'></span> searching …";
  const r = await fetch("/api/kb/search", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({query:q, k:5})}).then(r=>r.json());
  const rows = r.results || [];
  if (!rows.length) { $("kb-search-out").innerHTML = "<div style='color:var(--muted)'>No matches.</div>"; return; }
  $("kb-search-out").innerHTML = rows.map(x => `
    <div style="border-left:3px solid var(--accent); padding:6px 12px; margin-bottom:8px; background:var(--panel-2); border-radius:4px">
      <div style="color:var(--muted); font-size:11px">${x.title} · chunk ${x.chunk_idx} · dist ${(x.distance ?? 0).toFixed(3)}</div>
      <div style="margin-top:4px; font-size:13px">${escapeHtml((x.text||"").slice(0, 600))}${(x.text||"").length>600?"…":""}</div>
    </div>
  `).join("");
}

async function kbDelete(doc_id) {
  if (!confirm("Delete this document and all its chunks from the KB?")) return;
  await fetch("/api/kb/document/" + doc_id, {method:"DELETE"});
  toast("✓ deleted");
  kbRefresh();
}

async function kbExport() {
  toast("Building fine-tuning corpus …");
  const r = await fetch("/api/kb/export-finetune", {method:"POST"}).then(r=>r.json());
  if (r.ok) {
    toast(`✓ docs ${r.docs_size_kb}KB · ${r.decisions} decisions (${r.decisions_size_kb}KB) → .cache/`);
  } else { toast("✗ failed"); }
}

// Initialise KB tab when first visited
document.querySelector('[data-tab="kb"]')?.addEventListener("click", () => {
  if (!window._kbInited) { window._kbInited = true; kbRefresh(); }
});

// ---------- settings / status ----------
function dot(ok) { return `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${ok?'var(--pos)':'var(--neg)'};margin-right:6px"></span>`; }

async function loadStatus() {
  $("status-cards").innerHTML = "<span class='spin'></span> checking subsystems …";
  const s = await fetch("/api/status").then(r=>r.json());

  $("status-cards").innerHTML = [
    `<div class="kpi"><div class="label">Upstox</div><div class="value" style="font-size:14px">${dot(s.upstox.ok)}${s.upstox.ok ? s.upstox.user : "not authed"}</div></div>`,
    `<div class="kpi"><div class="label">LLM (${s.llm.provider})</div><div class="value" style="font-size:14px">${dot(s.llm.ok)}${s.llm.model || "—"}</div></div>`,
    `<div class="kpi"><div class="label">Telegram</div><div class="value" style="font-size:14px">${dot(s.telegram.configured)}${s.telegram.configured ? s.telegram.authorized_chats.length + " chats" : "not set"}</div></div>`,
    `<div class="kpi"><div class="label">TimescaleDB</div><div class="value" style="font-size:14px">${dot(s.db.timescale)}${s.db.timescale ? "connected" : (s.db.dsn_set ? "DSN set, no conn" : "JSONL fallback")}</div></div>`,
    `<div class="kpi"><div class="label">Scheduler</div><div class="value" style="font-size:14px">${dot(s.scheduler.running)}${s.scheduler.running ? s.scheduler.jobs.length + " jobs" : "stopped"}</div></div>`,
  ].join("");

  // scheduler section
  $("sched-state").textContent = s.scheduler.running
    ? `Running. ${s.scheduler.jobs.length} jobs scheduled.`
    : "Not running. Click ▶ Start to enable cron jobs in-process.";
  $("sched-jobs").innerHTML = s.scheduler.jobs.length
    ? table(s.scheduler.jobs, [{key:"id"}, {key:"name"}, {key:"next", title:"Next run"}])
    : "";

  // config
  $("config-table").innerHTML = table(
    Object.entries(s.config).map(([k,v]) => ({key:k, value:String(v)})),
    [{key:"key"}, {key:"value"}]
  );

  if (s.llm.provider === "ollama" && !s.llm.ok) {
    toast("⚠ Ollama model not pulled. Run: ollama pull " + s.llm.model);
  }
}

async function schedStart() {
  await fetch("/api/scheduler/start", {method:"POST"});
  toast("Scheduler started");
  loadStatus();
}
async function schedStop() {
  await fetch("/api/scheduler/stop", {method:"POST"});
  toast("Scheduler stopped");
  loadStatus();
}
async function runNow(job) {
  toast(`Triggering ${job} …`);
  const {job_id} = await fetch("/api/scheduler/run-now/" + job, {method:"POST"}).then(r=>r.json());
  try {
    await pollJob(job_id);
    toast(`✓ ${job} done`);
  } catch (e) { toast(`✗ ${job}: ` + e.message); }
}
async function startTgBot() {
  const r = await fetch("/api/telegram/bot/start", {method:"POST"}).then(r=>r.json());
  toast(r.already_running ? `Bot already running (pid ${r.pid})` : (r.ok ? `🤖 Bot started (pid ${r.pid}) — send /upstox_login on Telegram` : "✗ " + (r.error||"failed")));
}
async function stopTgBot() {
  const r = await fetch("/api/telegram/bot/stop", {method:"POST"}).then(r=>r.json());
  toast(r.already_stopped ? "Bot wasn't running" : (r.ok ? "⏹ Bot stopped" : "✗ failed"));
}
async function tgBotLog() {
  const r = await fetch("/api/telegram/bot/log?tail=80").then(r=>r.json());
  $("tg-bot-log").textContent = (r.lines||[]).join("\n") || "(empty)";
}
async function testTelegram() {
  toast("Testing Telegram …");
  const r = await fetch("/api/telegram/test", {method:"POST"}).then(r=>r.json());
  if (r.ok) {
    toast(`✓ Telegram OK — sent to ${r.sends.length} chat(s) as @${r.bot}`);
  } else {
    const detail = r.sends ? r.sends.map(s=>`${s.chat_id}: ${s.error||"ok"}`).join(" · ") : (r.error||"failed");
    toast(`✗ [${r.stage||"send"}] ${detail}`);
    console.error("telegram test", r);
  }
}

// ---------- Performance ----------
let perfChart;
window.fullEquityCurve = [];

async function uploadTrades(input) {
  if (!input.files || input.files.length === 0) return;
  const file = input.files[0];
  const formData = new FormData();
  formData.append("file", file);

  const overlay = document.getElementById("upload-overlay");
  const status = document.getElementById("perf-status");
  overlay.style.display = "flex";
  
  try {
    const res = await fetch("/api/portfolio/upload_trades", {
      method: "POST",
      body: formData
    });
    
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Upload failed");
    }
    
    const data = await res.json();
    alert(`Success: ${data.message}\nFound ${data.trades_found} trades.`);
    
    // Automatically trigger analysis
    loadPerformance();
  } catch (e) {
    console.error(e);
    alert(`Error uploading trades: ${e.message}`);
    status.innerText = "Upload failed";
  } finally {
    overlay.style.display = "none";
    input.value = ""; // reset input so same file can be uploaded again if needed
  }
}

async function loadPerformance() {
  $("perf-status").innerHTML = "<span class='spin'></span> Analyzing — fetching trade history & building equity curve …";
  $("perf-kpis").innerHTML = "";
  try {
    const {job_id} = await fetch("/api/portfolio/performance", {method:"POST"}).then(r=>r.json());
    const data = await pollJob(job_id, (r) => {
      $("perf-status").innerHTML = "<span class='spin'></span> Running … (this may take 15-30 seconds on first run)";
    });
    $("perf-status").textContent = "";
    renderPerformance(data);
  } catch (e) {
    $("perf-status").innerHTML = `<span class="neg">✗ ${e.message}</span>`;
  }
}

function renderPerformance(data) {
  if (!data) return;
  const s = data.summary || {};
  const ret = data.returns || {};

  // ---- KPIs ----
  const xirrVal = data.xirr != null ? `${data.xirr >= 0 ? "+" : ""}${fmt(data.xirr)}%` : "—";
  const xirrCls = data.xirr != null ? (data.xirr >= 0 ? "pos" : "neg") : "";

  const retKpi = (label, r) => {
    if (!r) return kpi(label, "—");
    return kpi(label, `${r.cagr_pct >= 0 ? "+" : ""}${fmt(r.cagr_pct)}% CAGR`, r.cagr_pct >= 0 ? "pos" : "neg");
  };

  $("perf-kpis").innerHTML = [
    kpi("XIRR", xirrVal, xirrCls),
    retKpi("1 Year", ret["1Y"]),
    retKpi("3 Year", ret["3Y"]),
    retKpi("5 Year", ret["5Y"]),
    kpi("Since Inception", ret.inception ? `${ret.inception.cagr_pct >= 0 ? "+" : ""}${fmt(ret.inception.cagr_pct)}% (${ret.inception.years}y)` : "—",
        ret.inception ? (ret.inception.cagr_pct >= 0 ? "pos" : "neg") : ""),
    kpi("Total P&L", `${inr(s.total_pnl)} (${fmt(s.total_pnl_pct)}%)`, cls(s.total_pnl)),
    kpi("Trades", s.total_trades || 0),
    kpi("Holdings", s.n_holdings || 0),
  ].join("");

  // ---- Equity Curve ----
  window.fullEquityCurve = data.equity_curve || [];
  renderFilteredCurve("all");

  // ---- All Stocks Traded ----
  const allStocksBody = document.querySelector("#all-stocks-table tbody");
  if (allStocksBody) {
    if (!data.all_stocks || !data.all_stocks.length) {
      allStocksBody.innerHTML = "<tr><td colspan='8' class='muted center'>No trades found.</td></tr>";
    } else {
      allStocksBody.innerHTML = data.all_stocks.map(s => `
        <tr>
          <td><strong>${s.symbol}</strong></td>
          <td class="right">${s.total_bought_qty}</td>
          <td class="right">${s.total_sold_qty}</td>
          <td class="right">${s.current_qty}</td>
          <td class="right">${inr(s.current_value)}</td>
          <td class="right ${cls(s.realized_pnl)}">${inr(s.realized_pnl)}</td>
          <td class="right ${cls(s.unrealized_pnl)}">${inr(s.unrealized_pnl)}</td>
          <td class="right ${cls(s.total_pnl)}"><strong>${inr(s.total_pnl)}</strong></td>
        </tr>
      `).join("");
    }
  }

  // ---- Winners ----
  $("perf-winners").innerHTML = table(data.winners || [], [
    {key:"symbol", title:"Symbol"},
    {key:"quantity", title:"Qty"},
    {key:"avg_price", title:"Avg", fmt:v=>fmt(v)},
    {key:"ltp", title:"LTP", fmt:v=>fmt(v)},
    {key:"invested", title:"Invested", fmt:v=>inr(v)},
    {key:"current_value", title:"Value", fmt:v=>inr(v)},
    {key:"pnl", title:"P&L", fmt:v=>inr(v), cls:cls},
    {key:"pnl_pct", title:"Return", fmt:v=>`${v>=0?"+":""}${fmt(v)}%`, cls:cls},
  ]);

  // ---- Losers ----
  $("perf-losers").innerHTML = table(data.losers || [], [
    {key:"symbol", title:"Symbol"},
    {key:"quantity", title:"Qty"},
    {key:"avg_price", title:"Avg", fmt:v=>fmt(v)},
    {key:"ltp", title:"LTP", fmt:v=>fmt(v)},
    {key:"invested", title:"Invested", fmt:v=>inr(v)},
    {key:"current_value", title:"Value", fmt:v=>inr(v)},
    {key:"pnl", title:"P&L", fmt:v=>inr(v), cls:cls},
    {key:"pnl_pct", title:"Return", fmt:v=>`${v>=0?"+":""}${fmt(v)}%`, cls:cls},
  ]);

  // ---- Opportunity Misses ----
  $("perf-misses").innerHTML = table(data.opportunity_misses || [], [
    {key:"symbol", title:"Symbol"},
    {key:"qty_sold", title:"Qty Sold"},
    {key:"avg_sell_price", title:"Sold At", fmt:v=>fmt(v)},
    {key:"current_price", title:"Now", fmt:v=>fmt(v)},
    {key:"missed_gain_pct", title:"Missed %", fmt:v=>`+${fmt(v)}%`, cls:()=>"neg"},
    {key:"missed_value", title:"Missed ₹", fmt:v=>inr(v), cls:()=>"neg"},
    {key:"last_sell_date", title:"Sold On"},
  ]);
}

function renderFilteredCurve(days) {
  let curve = window.fullEquityCurve;
  if (!curve || !curve.length) {
    renderEquityCurve([]);
    return;
  }
  
  if (days !== "all") {
    let cutoff = new Date();
    if (days === "ytd") {
      cutoff = new Date(cutoff.getFullYear(), 0, 1);
    } else {
      cutoff.setDate(cutoff.getDate() - parseInt(days));
    }
    const cutoffStr = cutoff.toISOString().split("T")[0];
    curve = curve.filter(p => p.date >= cutoffStr);
  }
  
  renderEquityCurve(curve);
}

// Attach filter listeners
document.addEventListener("click", e => {
  if (e.target.matches("#chart-filters .filter-btn")) {
    document.querySelectorAll("#chart-filters .filter-btn").forEach(b => b.classList.remove("active"));
    e.target.classList.add("active");
    renderFilteredCurve(e.target.dataset.days);
  }
});

function renderEquityCurve(curve) {
  if (!curve || !curve.length) {
    $("perf-equity-chart").parentElement.innerHTML =
      "<div style='color:var(--muted);text-align:center;padding:60px'>No equity curve data. Click Analyze to generate.</div>";
    return;
  }

  const labels = curve.map(p => p.date);
  const values = curve.map(p => p.portfolio_value);
  const invested = curve.map(p => p.invested_capital);

  if (perfChart) perfChart.destroy();

  const ctx = $("perf-equity-chart").getContext("2d");

  // Gradient fill for portfolio value line
  const gradient = ctx.createLinearGradient(0, 0, 0, 340);
  gradient.addColorStop(0, "rgba(63, 185, 80, 0.25)");
  gradient.addColorStop(0.5, "rgba(63, 185, 80, 0.08)");
  gradient.addColorStop(1, "rgba(63, 185, 80, 0.0)");

  const investedGradient = ctx.createLinearGradient(0, 0, 0, 340);
  investedGradient.addColorStop(0, "rgba(47, 129, 247, 0.12)");
  investedGradient.addColorStop(1, "rgba(47, 129, 247, 0.0)");

  perfChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Portfolio Value",
          data: values,
          borderColor: "#3fb950",
          backgroundColor: gradient,
          borderWidth: 2.5,
          fill: true,
          tension: 0.25,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: "#3fb950",
        },
        {
          label: "Invested Capital",
          data: invested,
          borderColor: "#2f81f7",
          backgroundColor: investedGradient,
          borderWidth: 1.5,
          borderDash: [6, 3],
          fill: true,
          tension: 0.25,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBackgroundColor: "#2f81f7",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#e6edf3", font: { size: 12 } } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const val = ctx.raw;
              if (ctx.datasetIndex === 0) {
                const inv = invested[ctx.dataIndex] || 0;
                const pnl = val - inv;
                const pnlPct = inv > 0 ? ((pnl / inv) * 100).toFixed(1) : "0";
                return `Portfolio: ₹${Math.round(val).toLocaleString("en-IN")}  (P&L: ₹${Math.round(pnl).toLocaleString("en-IN")} / ${pnlPct}%)`;
              }
              return `Invested: ₹${Math.round(val).toLocaleString("en-IN")}`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: "#8b949e", maxTicksLimit: 12, maxRotation: 0 },
          grid: { color: "#2a3140" },
        },
        y: {
          ticks: {
            color: "#8b949e",
            callback: v => "₹" + (v >= 100000 ? (v / 100000).toFixed(1) + "L" : Math.round(v).toLocaleString("en-IN")),
          },
          grid: { color: "#2a3140" },
        },
      },
    },
  });
}

// Auto-load cached data when Performance tab is first visited
document.querySelector('[data-tab="performance"]')?.addEventListener("click", () => {
  if (!window._perfInited) {
    window._perfInited = true;
    // Try cached data first
    fetch("/api/portfolio/performance/cached")
      .then(r => r.json())
      .then(r => {
        if (r.ok && r.data) {
          renderPerformance(r.data);
          $("perf-status").textContent = "(showing cached data — click Analyze to refresh)";
        }
      })
      .catch(() => {});
  }
});

// initial load
loadPortfolio().catch(e => $("port-kpis").innerHTML = `<span class='neg'>Error: ${e.message}. Make sure you've run <code>python -m src.upstox.auth</code>.</span>`);
