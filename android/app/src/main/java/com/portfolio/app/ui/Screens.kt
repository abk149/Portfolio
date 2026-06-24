package com.portfolio.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.portfolio.app.net.Api
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

private val UNIVERSES = listOf("nifty50", "nifty100", "all_nse")

// Poll a backend job until it leaves "running". Returns the final job object.
private suspend fun pollJob(jobId: String, maxSecs: Int = 600): JSONObject {
    val deadline = System.currentTimeMillis() + maxSecs * 1000L
    while (System.currentTimeMillis() < deadline) {
        val r = Api.job(jobId)
        val o = r.objOrNull()
        val status = o?.optString("status")
        if (o != null && status != "running") return o
        delay(2000)
    }
    return JSONObject().put("status", "error").put("error", "timed out")
}

private fun arr(o: JSONObject?, key: String): JSONArray? =
    o?.optJSONArray(key) ?: o?.optJSONObject(key)?.optJSONArray("array")

// Allocation records → (label, value) pie slices. Label = first non-numeric
// field (sector/symbol); value = current_value (or weight) as fallback.
private fun allocationSlices(arr: JSONArray?): List<Pair<String, Float>> {
    if (arr == null) return emptyList()
    val out = ArrayList<Pair<String, Float>>()
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        var label = "—"
        val keys = o.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            if (o.opt(k) is String) { label = o.optString(k); break }
        }
        val v = (o.opt("current_value") as? Number)?.toFloat()
            ?: (o.opt("value") as? Number)?.toFloat()
            ?: (o.opt("weight_pct") as? Number)?.toFloat()
            ?: (o.opt("weight") as? Number)?.toFloat() ?: 0f
        if (v > 0f) out.add(label to v)
    }
    return out
}

// equity_curve [{date, portfolio_value, invested_capital}] → (values, invested).
private fun equitySeries(arr: JSONArray?): Pair<List<Float>, List<Float>> {
    val pv = ArrayList<Float>(); val inv = ArrayList<Float>()
    if (arr != null) for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        val p = (o.opt("portfolio_value") as? Number)?.toFloat() ?: continue
        pv.add(p)
        inv.add((o.opt("invested_capital") as? Number)?.toFloat()
            ?: (o.opt("invested") as? Number)?.toFloat() ?: Float.NaN)
    }
    return pv to inv
}

// ─────────────────────────────────────────────────────────────────────────────
// HOME · Portfolio
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun HomeScreen() {
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var risk by remember { mutableStateOf<JSONObject?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    fun load() {
        scope.launch {
            loading = true; error = null
            when (val r = Api.portfolio()) {
                is Api.Resp.Ok -> {
                    if (r.body.has("error")) error = r.body.optString("message", "Not authenticated")
                    else data = r.body
                }
                is Api.Resp.Err -> error = r.message
            }
            Api.portfolioRisk().objOrNull()?.let { risk = it }
            loading = false
        }
    }
    LaunchedEffect(Unit) { if (BackendBus.running) load() }

    ScreenScaffold(title = "Portfolio", loading = loading, onRefresh = ::load) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }
        error?.let {
            SectionCard("Not connected", Bear) {
                StatusBanner(it, Bear)
                Spacer(Modifier.height(8.dp))
                Text("Open Login (top-right 🔒) to authenticate your broker.",
                    color = Muted, fontSize = 12.sp)
            }
        }
        data?.let { d ->
            val s = d.optJSONObject("summary") ?: JSONObject()
            fun num(k: String) = (s.opt(k) as? Number)?.toDouble() ?: 0.0
            val pnl = num("holdings_pnl")
            val pct = num("holdings_pnl_pct")
            val day = num("day_change_value")
            SectionCard("Portfolio value", AccentHi) {
                Text("₹" + fmtNum(s.opt("holdings_value")), color = OnBg,
                    fontSize = 30.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    val up = pnl >= 0
                    Text((if (up) "▲ ₹" else "▼ ₹") + fmtNum(kotlin.math.abs(pnl)) +
                        "  (%.2f%%)".format(pct), color = if (up) Bull else Bear,
                        fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    Pill((if (day >= 0) "Day ▲ ₹" else "Day ▼ ₹") + fmtNum(kotlin.math.abs(day)),
                        if (day >= 0) Bull else Bear)
                }
                Spacer(Modifier.height(14.dp))
                KpiGrid(listOf(
                    Triple("Invested", "₹" + fmtCompact(s.opt("holdings_invested")), OnBg),
                    Triple("Holdings", fmtNum(s.opt("n_holdings")), OnBg),
                ))
            }
            SectionCard("Holdings", AccentHi) { DataTable(arr(d, "holdings")) }
            arr(d, "positions")?.takeIf { it.length() > 0 }?.let {
                SectionCard("Positions", AccentHi) { DataTable(it) }
            }
            SectionCard("Allocation", AccentHi) {
                DonutChart(allocationSlices(arr(d, "allocation")))
                Spacer(Modifier.height(10.dp))
                DataTable(arr(d, "allocation"))
            }
        }
        risk?.let { rk ->
            SectionCard("Concentration risk", Warn) { DataTable(arr(rk, "concentration"), 20) }
            SectionCard("Underperformers", Bear) { DataTable(arr(rk, "underperformers"), 20) }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// DR-QUANT funnel
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun QuantScreen() {
    var universe by remember { mutableStateOf("nifty50") }
    var running by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<JSONObject?>(null) }
    var status by remember { mutableStateOf<String?>(null) }
    var macro by remember { mutableStateOf<JSONObject?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) { if (BackendBus.running) macro = Api.macro().objOrNull() }

    ScreenScaffold(title = "DR-Quant", loading = running, onRefresh = null) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }
        SectionCard("Funnel", Bull) {
            UniversePicker(universe) { universe = it }
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = {
                    scope.launch {
                        running = true; result = null
                        status = "Submitting funnel run…"
                        val sub = Api.quantRun(universe).objOrNull()
                        val jobId = sub?.optString("job_id")
                        if (jobId.isNullOrBlank()) { status = "Failed to start: ${sub}"; running = false; return@launch }
                        status = "Running on-device · watch the Terminal for live progress…"
                        val fin = pollJob(jobId)
                        if (fin.optString("status") == "done") {
                            result = fin.optJSONObject("result"); status = null
                        } else status = "Run failed: ${fin.optString("error")}"
                        running = false
                    }
                },
                enabled = !running, modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Bull),
            ) { Text(if (running) "Running…" else "▶ Run funnel") }
            status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (running) Warn else Bear) }
        }
        macro?.let { m ->
            val kpis = listOf(
                Triple("VIX", fmtNum(m.opt("india_vix")), OnBg),
                Triple("NIFTY PCR", fmtNum(m.opt("nifty_pcr")), OnBg),
                Triple("USDINR", fmtNum(m.opt("usdinr")), OnBg),
                Triple("Regime", m.optString("regime", "—"), AccentHi),
            )
            SectionCard("Macro", Warn) { KpiGrid(kpis) }
        }
        result?.let { res ->
            val p = res.optJSONObject("portfolio")
            SectionCard("Result", Bull) {
                KpiGrid(listOf(
                    Triple("Candidates", fmtNum(res.opt("candidates")), OnBg),
                    Triple("Validated", fmtNum(arr(res, "validated")?.length() ?: 0), Bull),
                    Triple("Sharpe", fmtNum(p?.opt("sharpe")), AccentHi),
                ))
            }
            SectionCard("Validated", Bull) { DataTable(arr(res, "validated")) }
            arr(res, "intraday_alerts")?.takeIf { it.length() > 0 }?.let {
                SectionCard("Intraday alerts", Warn) { DataTable(it) }
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// UNIVERSE MAP
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun MapScreen() {
    var universe by remember { mutableStateOf("nifty50") }
    var building by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf<String?>(null) }
    var report by remember { mutableStateOf<JSONObject?>(null) }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    val scope = rememberCoroutineScope()

    fun loadCached() {
        scope.launch {
            report = Api.umapReport().objOrNull()
            data = Api.umapData().objOrNull()
        }
    }
    LaunchedEffect(Unit) { if (BackendBus.running) loadCached() }

    ScreenScaffold(title = "Universe Map", loading = building, onRefresh = ::loadCached) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }
        SectionCard("Build", AccentHi) {
            UniversePicker(universe) { universe = it }
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = {
                    scope.launch {
                        building = true
                        status = "Crawling universe — this is long; watch the Terminal…"
                        val sub = Api.umapBuild(universe).objOrNull()
                        val jobId = sub?.optString("job_id")
                        if (!jobId.isNullOrBlank()) {
                            val fin = pollJob(jobId, maxSecs = 1800)
                            status = if (fin.optString("status") == "done") "Build complete."
                                     else "Build failed: ${fin.optString("error")}"
                        } else status = "Started — refresh report when the Terminal shows done."
                        loadCached(); building = false
                    }
                },
                enabled = !building, modifier = Modifier.fillMaxWidth(),
            ) { Text(if (building) "Building…" else "▶ Build / refresh map") }
            status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, Warn) }
        }
        report?.let { rep ->
            SectionCard("Stats", AccentHi) {
                KpiGrid(listOf(
                    Triple("Stocks", fmtNum(rep.opt("count")), OnBg),
                    Triple("Tech scored", fmtNum(rep.opt("tech_total")), OnBg),
                    Triple("Fetched", fmtNum(rep.opt("fund_scanned")), AccentHi),
                    Triple("Reused", fmtNum(rep.opt("fund_reused")), Muted),
                ))
            }
        }
        arr(data, "stocks")?.takeIf { it.length() > 0 }?.let { stocks ->
            SectionCard("Map · technical vs fundamental", AccentHi) {
                ScatterChart(universePoints(stocks), xLabel = "Technical score", yLabel = "Fundamental score")
            }
            SectionCard("Universe", AccentHi) { DataTable(stocks, 80) }
        }
    }
}

// deploy-cash buys → clean table (symbol first so it's the sticky column).
private fun allocationBuys(arr: JSONArray?): JSONArray {
    val out = JSONArray()
    if (arr == null) return out
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        val sym = o.optString("ticker").removeSuffix(".NS").removeSuffix(".BO")
        out.put(JSONObject()
            .put("symbol", sym)
            .put("amount", o.opt("buy_inr") ?: JSONObject.NULL)
            .put("shares", o.opt("shares") ?: JSONObject.NULL)
            .put("price", o.opt("price") ?: JSONObject.NULL)
            .put("weight_pct", o.opt("final_weight_pct") ?: JSONObject.NULL))
    }
    return out
}

// Universe stocks → scatter points (x=tech, y=fundamental, color by recommendation).
private fun universePoints(arr: JSONArray): List<Triple<Float, Float, Color>> {
    val out = ArrayList<Triple<Float, Float, Color>>()
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        val x = (o.opt("tech_score") as? Number)?.toFloat()
            ?: (o.opt("combined") as? Number)?.toFloat() ?: continue
        val y = (o.opt("fund_score") as? Number)?.toFloat()
            ?: (o.opt("combined") as? Number)?.toFloat() ?: continue
        val reco = o.optString("recommendation", "")
        val c = when {
            reco.contains("STRONG_BUY") || reco == "BUY" || reco.contains("TECH_BUY") -> Bull
            reco.contains("HOLD") || reco.contains("WATCH") -> Warn
            reco.contains("AVOID") || reco.contains("SELL") -> Bear
            else -> Muted
        }
        out.add(Triple(x, y, c))
    }
    return out
}

// ─────────────────────────────────────────────────────────────────────────────
// ANALYSIS (Performance / Screener / Intraday / KB)
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun AnalysisScreen() {
    var sub by remember { mutableStateOf("Optimize") }
    val tabs = listOf("Optimize", "Screener", "Intraday", "Performance", "KB")
    Column(Modifier.fillMaxSize()) {
        ScrollableTabRow(
            selectedTabIndex = tabs.indexOf(sub),
            containerColor = Bg, edgePadding = 12.dp,
        ) {
            tabs.forEach { t ->
                Tab(selected = sub == t, onClick = { sub = t },
                    text = { Text(t, fontSize = 13.sp) })
            }
        }
        Box(Modifier.weight(1f)) {
            when (sub) {
                "Optimize" -> OptimizeTab()
                "Screener" -> ScreenerTab()
                "Intraday" -> IntradayTab()
                "Performance" -> PerformanceTab()
                else -> KbTab()
            }
        }
    }
}

@Composable private fun ScreenerTab() {
    var universe by remember { mutableStateOf("nifty50") }
    var minScore by remember { mutableStateOf(60) }
    var res by remember { mutableStateOf<JSONObject?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }
        SectionCard("Screener funnel", AccentHi) {
            UniversePicker(universe) { universe = it }
            Spacer(Modifier.height(6.dp))
            Text("Min score: $minScore", color = Muted, fontSize = 12.sp)
            Slider(value = minScore.toFloat(), onValueChange = { minScore = it.toInt() },
                valueRange = 0f..100f)
            Button(onClick = {
                scope.launch { loading = true; res = Api.screener(universe, minScore).objOrNull(); loading = false }
            }, enabled = !loading, modifier = Modifier.fillMaxWidth()) {
                Text(if (loading) "Scanning…" else "▶ Run screener")
            }
        }
        arr(res, "results")?.let { SectionCard("Results", Bull) { DataTable(it) } }
    }
}

@Composable private fun IntradayTab() {
    var days by remember { mutableStateOf(90) }
    var res by remember { mutableStateOf<JSONObject?>(null) }
    var loading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }
        SectionCard("Trade analysis", AccentHi) {
            Text("Lookback: $days days", color = Muted, fontSize = 12.sp)
            Slider(value = days.toFloat(), onValueChange = { days = it.toInt() }, valueRange = 30f..365f)
            Button(onClick = {
                scope.launch { loading = true; res = Api.intradayAnalyze(days).objOrNull(); loading = false }
            }, enabled = !loading, modifier = Modifier.fillMaxWidth()) {
                Text(if (loading) "Analyzing…" else "▶ Analyze my trades")
            }
        }
        res?.let { r ->
            if (r.has("error")) { SectionCard("No data", Muted) { StatusBanner(r.optString("error"), Muted) } }
            else {
                SectionCard("Stats", AccentHi) {
                    KpiGrid(listOf(
                        Triple("Trades", fmtNum(r.opt("trades")), OnBg),
                        Triple("Win rate", fmtNum(r.opt("win_rate_pct")) + "%", AccentHi),
                        Triple("Expectancy", fmtNum(r.opt("expectancy")), OnBg),
                        Triple("Total P&L", "₹" + fmtNum(r.opt("total_pnl")),
                            if (((r.opt("total_pnl") as? Number)?.toDouble() ?: 0.0) >= 0) Bull else Bear),
                    ))
                }
                arr(r, "by_symbol")?.let { SectionCard("By symbol", AccentHi) { DataTable(it) } }
                val mistakes = r.optJSONArray("mistakes")
                if (mistakes != null && mistakes.length() > 0) SectionCard("Mistakes", Bear) {
                    for (i in 0 until mistakes.length())
                        Text("• ${mistakes.optString(i)}", color = OnBg, fontSize = 12.sp,
                            modifier = Modifier.padding(vertical = 2.dp))
                }
            }
        }
    }
}

@Composable private fun PerformanceTab() {
    var res by remember { mutableStateOf<JSONObject?>(null) }
    var loading by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) {
        if (BackendBus.running) res = Api.performanceCached().objOrNull()?.optJSONObject("data")
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }
        SectionCard("Performance over time", AccentHi) {
            Text("Reconstructs your portfolio value day-by-day from your executed " +
                "orders, vs the capital you put in — plus money-weighted return (XIRR), " +
                "winners and losers.", color = Muted, fontSize = 12.sp)
            Spacer(Modifier.height(10.dp))
            Button(onClick = {
                scope.launch {
                    loading = true; status = "Analysing — fetching trade history & building the curve…"
                    val sub = Api.performance().objOrNull()       // job-based
                    val jobId = sub?.optString("job_id")
                    if (jobId.isNullOrBlank()) { status = "Failed to start."; loading = false; return@launch }
                    val fin = pollJob(jobId)
                    if (fin.optString("status") == "done") { res = fin.optJSONObject("result"); status = null }
                    else status = "Failed: ${fin.optString("error")}"
                    loading = false
                }
            }, enabled = !loading, modifier = Modifier.fillMaxWidth()) {
                Text(if (loading) "Analysing…" else "▶ Analyse performance")
            }
            status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (loading) Warn else Bear) }
        }
        res?.let { r ->
            val s = r.optJSONObject("summary") ?: JSONObject()
            fun n(k: String) = (s.opt(k) as? Number)?.toDouble() ?: 0.0
            SectionCard("Returns", AccentHi) {
                KpiGrid(listOf(
                    Triple("Invested", "₹" + fmtCompact(s.opt("total_invested")), OnBg),
                    Triple("Current", "₹" + fmtCompact(s.opt("current_value")), OnBg),
                    Triple("Total P&L", "₹" + fmtCompact(s.opt("total_pnl")),
                        if (n("total_pnl") >= 0) Bull else Bear),
                    Triple("Return", "%.2f%%".format(n("total_pnl_pct")),
                        if (n("total_pnl_pct") >= 0) Bull else Bear),
                    Triple("XIRR", if (r.opt("xirr") is Number) "%.2f%%".format(n("xirr").let { if (it < 1) it * 100 else it }) else "—", AccentHi),
                    Triple("Trades", fmtNum(s.opt("total_trades")), OnBg),
                ))
            }
            val curve = arr(r, "equity_curve")
            val (pv, inv) = equitySeries(curve)
            SectionCard("Portfolio value vs invested", Bull) {
                if (pv.size >= 2) LineChart(primary = pv, secondary = inv.takeIf { it.size == pv.size })
                else StatusBanner("No time series yet. This needs your executed order " +
                    "history — on Groww with limited API access it may be unavailable; " +
                    "Upstox provides full history.", Warn)
            }
            arr(r, "winners")?.takeIf { it.length() > 0 }?.let { SectionCard("Winners", Bull) { DataTable(it) } }
            arr(r, "losers")?.takeIf { it.length() > 0 }?.let { SectionCard("Losers", Bear) { DataTable(it) } }
        }
    }
}

// (vol%, ret%) from an optimizer record, tolerant of decimal vs _pct keys.
private fun xyPct(o: JSONObject?): Pair<Float, Float>? {
    if (o == null) return null
    fun f(vararg keys: String): Float? {
        for (k in keys) { val v = o.opt(k); if (v is Number) return v.toFloat() }
        return null
    }
    val volPct = f("vol_pct", "volatility_pct") ?: f("vol", "volatility")?.let { it * 100 }
    val retPct = f("return_pct") ?: f("return", "ret", "expected_return")?.let { it * 100 }
    return if (volPct != null && retPct != null) volPct to retPct else null
}

@Composable private fun OptimizeTab() {
    var mode by remember { mutableStateOf("max_sharpe") }
    var maxW by remember { mutableStateOf(25) }
    var running by remember { mutableStateOf(false) }
    var res by remember { mutableStateOf<JSONObject?>(null) }
    var status by remember { mutableStateOf<String?>(null) }
    // Deploy-cash (reallocation by amount)
    var cash by remember { mutableStateOf("15000") }
    var deployBusy by remember { mutableStateOf(false) }
    var deploy by remember { mutableStateOf<JSONObject?>(null) }
    var deployMsg by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }

        SectionCard("Invest new cash → allocation", Bull) {
            Text("Enter an amount; I'll suggest how to deploy it (₹ + whole shares) " +
                "to best improve your portfolio's risk/return.", color = Muted, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(cash, { cash = it.filter { ch -> ch.isDigit() } },
                    label = { Text("Amount (₹)") }, singleLine = true,
                    modifier = Modifier.weight(1f))
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = {
                        val amt = cash.toDoubleOrNull() ?: 0.0
                        if (amt <= 0) { deployMsg = "Enter an amount."; return@Button }
                        scope.launch {
                            deployBusy = true; deploy = null; deployMsg = "Computing allocation…"
                            val r = Api.deployCash(amt).objOrNull()
                            if (r == null) deployMsg = "Backend error."
                            else if (r.has("error")) deployMsg = r.optString("error")
                            else { deploy = r; deployMsg = null }
                            deployBusy = false
                        }
                    },
                    enabled = !deployBusy,
                ) { Text(if (deployBusy) "…" else "Suggest") }
            }
            deployMsg?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (deployBusy) Warn else Bear) }
            deploy?.let { d ->
                val before = d.optJSONObject("before"); val after = d.optJSONObject("after")
                Spacer(Modifier.height(10.dp))
                KpiGrid(listOf(
                    Triple("Sharpe now", fmtNum(before?.opt("sharpe")), OnBg),
                    Triple("Sharpe after", fmtNum(after?.opt("sharpe")), Bull),
                ))
                Spacer(Modifier.height(10.dp))
                Text("Buy", color = Muted, fontSize = 11.sp)
                Spacer(Modifier.height(4.dp))
                DataTable(allocationBuys(arr(d, "buys")))
            }
        }

        SectionCard("MPT optimizer", AccentHi) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("max_sharpe" to "Max Sharpe", "min_variance" to "Min Var").forEach { (k, lbl) ->
                    FilterChip(selected = mode == k, onClick = { mode = k }, label = { Text(lbl) })
                }
            }
            Spacer(Modifier.height(6.dp))
            Text("Max weight per name: $maxW%", color = Muted, fontSize = 12.sp)
            Slider(value = maxW.toFloat(), onValueChange = { maxW = it.toInt() }, valueRange = 5f..100f)
            Button(
                onClick = {
                    scope.launch {
                        running = true; res = null; status = "Optimising… (prices via broker or Yahoo fallback)"
                        val sub = Api.optimize(mode, maxW / 100.0).objOrNull()
                        val jobId = sub?.optString("job_id")
                        if (jobId.isNullOrBlank()) { status = "Failed: $sub"; running = false; return@launch }
                        val fin = pollJob(jobId)
                        if (fin.optString("status") == "done") {
                            val r = fin.optJSONObject("result")
                            if (r != null && r.has("error")) status = r.optString("error")
                            else { res = r; status = null }
                        } else status = "Failed: ${fin.optString("error")}"
                        running = false
                    }
                },
                enabled = !running, modifier = Modifier.fillMaxWidth(),
            ) { Text(if (running) "Optimising…" else "▶ Optimise portfolio") }
            status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (running) Warn else Bear) }
        }
        res?.let { r ->
            SectionCard("Optimal", Bull) {
                KpiGrid(listOf(
                    Triple("Exp. return", fmtNum(r.opt("expected_return_pct")) + "%", Bull),
                    Triple("Volatility", fmtNum(r.opt("volatility_pct")) + "%", OnBg),
                    Triple("Sharpe", fmtNum(r.opt("sharpe")), AccentHi),
                ))
            }
            SectionCard("Efficient frontier", AccentHi) {
                val frontier = buildList {
                    arr(r, "frontier")?.let { for (i in 0 until it.length()) xyPct(it.optJSONObject(i))?.let(::add) }
                }
                val holdings = buildList {
                    arr(r, "per_name")?.let { for (i in 0 until it.length()) xyPct(it.optJSONObject(i))?.let(::add) }
                }
                val current = xyPct(r.optJSONObject("current_portfolio"))
                val optimal = xyPct(JSONObject()
                    .put("vol_pct", r.opt("volatility_pct")).put("return_pct", r.opt("expected_return_pct")))
                FrontierChart(frontier, holdings, current, optimal)
            }
            SectionCard("Target weights", AccentHi) { DataTable(arr(r, "weights")) }
            SectionCard("Rebalance actions", Warn) { DataTable(arr(r, "rebalance")) }
        }
    }
}

@Composable private fun KbTab() {
    var stats by remember { mutableStateOf<JSONObject?>(null) }
    var docs by remember { mutableStateOf<JSONObject?>(null) }
    var query by remember { mutableStateOf("") }
    var res by remember { mutableStateOf<JSONObject?>(null) }
    val scope = rememberCoroutineScope()
    fun reload() {
        scope.launch {
            stats = Api.kbStats().objOrNull()
            docs = Api.get("/api/kb/documents").objOrNull()
        }
    }
    LaunchedEffect(Unit) { if (BackendBus.running) reload() }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }
        stats?.let {
            SectionCard("Knowledge base", AccentHi, trailing = {
                TextButton(onClick = { reload() }) { Text("Refresh") }
            }) {
                KpiGrid(listOf(
                    Triple("Universe stocks", fmtNum(it.opt("universe_stocks")), OnBg),
                    Triple("Documents", fmtNum(it.opt("documents")), OnBg),
                    Triple("Doc chunks", fmtNum(it.opt("chunks")), OnBg),
                    Triple("Decisions", fmtNum(it.opt("decisions")), OnBg),
                ))
                Spacer(Modifier.height(8.dp))
                val active = (it.opt("universe_stocks") as? Number)?.toInt() ?: 0
                StatusBanner(
                    (if (active > 0) "● Storage active" else "○ Empty — run Universe Map to populate") +
                    "\nMode: ${it.optString("search_mode", "—")}" +
                    "\nDB: ${it.optString("path", "—")}",
                    if (active > 0) Bull else Muted)
            }
        }
        arr(docs, "documents")?.takeIf { it.length() > 0 }?.let {
            SectionCard("Stored documents", AccentHi) { DataTable(it, 40) }
        }
        SectionCard("Search", AccentHi) {
            OutlinedTextField(query, { query = it }, label = { Text("Query") },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(8.dp))
            Button(onClick = { scope.launch { res = Api.kbSearch(query).objOrNull() } },
                enabled = query.isNotBlank(), modifier = Modifier.fillMaxWidth()) { Text("Search") }
        }
        arr(res, "results")?.let { SectionCard("Hits", Bull) { DataTable(it) } }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// TERMINAL
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun TerminalScreen() {
    val listState = rememberLazyListState()
    LaunchedEffect(BackendBus.logs.size) {
        if (BackendBus.logs.isNotEmpty()) listState.animateScrollToItem(BackendBus.logs.size - 1)
    }
    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("System Terminal", color = OnBg, fontSize = 16.sp, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            val (lbl, col) = when (BackendBus.state.value) {
                BackendBus.State.RUNNING -> "● RUNNING" to Bull
                BackendBus.State.STARTING -> "● STARTING" to Warn
                BackendBus.State.ERROR -> "● ERROR" to Bear
                else -> "● STOPPED" to Muted
            }
            Pill(lbl, col)
        }
        Spacer(Modifier.height(8.dp))
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth()
                .background(Color(0xFF010409)).padding(8.dp),
        ) {
            items(BackendBus.logs.size) { i ->
                Text(BackendBus.logs[i], color = AccentHi, fontFamily = FontFamily.Monospace, fontSize = 11.sp)
            }
        }
        Spacer(Modifier.height(8.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { BackendBus.onStart() },
                enabled = BackendBus.state.value != BackendBus.State.RUNNING,
                colors = ButtonDefaults.buttonColors(containerColor = Bull),
                modifier = Modifier.weight(1f)) { Text("▶ Start backend") }
            Button(onClick = { BackendBus.onStop() },
                enabled = BackendBus.state.value == BackendBus.State.RUNNING,
                colors = ButtonDefaults.buttonColors(containerColor = Bear),
                modifier = Modifier.weight(1f)) { Text("⏹ Stop") }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// AI CHAT — grounded in the user's loaded data (portfolio, DR-Quant, U-Map)
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun ChatScreen() {
    val msgs = remember { mutableStateListOf<Pair<Boolean, String>>() }   // isUser, text
    var input by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    LaunchedEffect(msgs.size) { if (msgs.isNotEmpty()) listState.animateScrollToItem(msgs.size - 1) }

    fun send() {
        val q = input.trim()
        if (q.isEmpty()) return
        msgs.add(true to q); input = ""; busy = true
        scope.launch {
            val reply = when (val r = Api.chat(q)) {
                is Api.Resp.Ok ->
                    if (r.body.optBoolean("ok", false)) r.body.optString("reply")
                    else "⚠ ${r.body.optString("error", "no answer")}"
                is Api.Resp.Err -> "⚠ Backend: ${r.message}"
            }
            msgs.add(false to reply); busy = false
        }
    }

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Text("AI Assistant", color = OnBg, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(4.dp))
        Text("Knows your portfolio, latest DR-Quant run, and the Universe Map.",
            color = Muted, fontSize = 11.sp)
        Spacer(Modifier.height(8.dp))
        if (!BackendBus.running) StatusBanner("Start the backend first (Terminal ▶).", Warn)
        LazyColumn(state = listState, modifier = Modifier.weight(1f).fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(msgs.size) { i ->
                val (isUser, text) = msgs[i]
                Row(Modifier.fillMaxWidth(),
                    horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start) {
                    Box(
                        Modifier.widthIn(max = 300.dp)
                            .background(if (isUser) Accent else Panel2,
                                androidx.compose.foundation.shape.RoundedCornerShape(12.dp))
                            .padding(10.dp)
                    ) { Text(text, color = OnBg, fontSize = 13.sp) }
                }
            }
        }
        if (busy) Row(Modifier.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp, color = AccentHi)
            Spacer(Modifier.width(8.dp)); Text("Thinking…", color = Muted, fontSize = 12.sp)
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(input, { input = it }, modifier = Modifier.weight(1f),
                placeholder = { Text("Ask about your data…") }, maxLines = 3)
            Spacer(Modifier.width(8.dp))
            Button(onClick = { send() }, enabled = !busy && input.isNotBlank() && BackendBus.running) {
                Text("Send")
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// shared bits
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun ScreenScaffold(
    title: String,
    loading: Boolean,
    onRefresh: (() -> Unit)?,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, top = 12.dp, end = 12.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text(title, color = OnBg, fontSize = 20.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.weight(1f))
            if (loading) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = AccentHi)
            else if (onRefresh != null) TextButton(onClick = onRefresh) { Text("Refresh") }
        }
        content()
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
fun ColumnScope.BackendOfflineHint() {
    SectionCard("Backend offline", Warn) {
        Text("The on-device engine isn't running yet.", color = OnBg, fontSize = 13.sp)
        Spacer(Modifier.height(10.dp))
        Button(onClick = { BackendBus.onStart() }, modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(containerColor = Bull)) { Text("▶ Start backend") }
        Spacer(Modifier.height(6.dp))
        Text("Watch progress in the Terminal tab.", color = Muted, fontSize = 11.sp)
    }
}

@Composable
fun UniversePicker(selected: String, onSelect: (String) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        UNIVERSES.forEach { u ->
            FilterChip(selected = u == selected, onClick = { onSelect(u) }, label = { Text(u) })
        }
    }
}
