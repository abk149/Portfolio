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

// equity_curve [{date, portfolio_value, invested}] → (values, invested).
private fun equitySeries(arr: JSONArray?): Pair<List<Float>, List<Float>> {
    val pv = ArrayList<Float>(); val inv = ArrayList<Float>()
    if (arr != null) for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        pv.add((o.opt("portfolio_value") as? Number)?.toFloat() ?: continue)
        inv.add((o.opt("invested") as? Number)?.toFloat() ?: Float.NaN)
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
            val kpis = buildList {
                add(Triple("Invested", "₹" + fmtNum(s.opt("invested")), OnBg))
                add(Triple("Current", "₹" + fmtNum(s.opt("current_value")), OnBg))
                val pnl = (s.opt("pnl") as? Number)?.toDouble() ?: 0.0
                add(Triple("P&L", "₹" + fmtNum(s.opt("pnl")), if (pnl >= 0) Bull else Bear))
                val pct = (s.opt("pnl_pct") as? Number)?.toDouble() ?: 0.0
                add(Triple("Return", fmtNum(s.opt("pnl_pct")) + "%", if (pct >= 0) Bull else Bear))
            }
            SectionCard("Summary", AccentHi) { KpiGrid(kpis) }
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
        arr(data, "stocks")?.let { SectionCard("Universe", AccentHi) { DataTable(it, 80) } }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// ANALYSIS (Performance / Screener / Intraday / KB)
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun AnalysisScreen() {
    var sub by remember { mutableStateOf("Screener") }
    val tabs = listOf("Screener", "Intraday", "Performance", "KB")
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
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { if (BackendBus.running) res = Api.performanceCached().objOrNull()?.optJSONObject("data") }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }
        SectionCard("Performance", AccentHi) {
            Button(onClick = {
                scope.launch { loading = true; res = Api.performance().objOrNull(); loading = false }
            }, enabled = !loading, modifier = Modifier.fillMaxWidth()) {
                Text(if (loading) "Analyzing…" else "▶ Analyze performance")
            }
        }
        res?.let { r ->
            arr(r, "equity_curve")?.let { curve ->
                val (pv, inv) = equitySeries(curve)
                if (pv.size >= 2) SectionCard("Equity curve", Bull) {
                    LineChart(primary = pv, secondary = inv.takeIf { it.size == pv.size })
                }
            }
            arr(r, "winners")?.let { SectionCard("Winners", Bull) { DataTable(it) } }
            arr(r, "losers")?.let { SectionCard("Losers", Bear) { DataTable(it) } }
        }
    }
}

@Composable private fun KbTab() {
    var stats by remember { mutableStateOf<JSONObject?>(null) }
    var query by remember { mutableStateOf("") }
    var res by remember { mutableStateOf<JSONObject?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { if (BackendBus.running) stats = Api.kbStats().objOrNull() }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }
        stats?.let {
            SectionCard("Knowledge base", AccentHi) {
                KpiGrid(listOf(
                    Triple("Stocks", fmtNum(it.opt("stocks")), OnBg),
                    Triple("Documents", fmtNum(it.opt("documents")), OnBg),
                ))
            }
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
