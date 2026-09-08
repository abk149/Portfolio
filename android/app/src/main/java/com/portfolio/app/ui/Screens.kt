package com.portfolio.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
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

// equity_curve [{date, portfolio_value, invested_capital, hold_value}]
// → (portfolio, invested, if-never-sold).
private class EquityCurve(
    val portfolio: List<Float>,
    val invested: List<Float>,
    val hold: List<Float>,
    val proceeds: List<Float>,
)

private fun equitySeries(arr: JSONArray?): EquityCurve {
    val pv = ArrayList<Float>(); val inv = ArrayList<Float>()
    val hold = ArrayList<Float>(); val proc = ArrayList<Float>()
    if (arr != null) for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        val p = (o.opt("portfolio_value") as? Number)?.toFloat() ?: continue
        pv.add(p)
        inv.add((o.opt("invested_capital") as? Number)?.toFloat()
            ?: (o.opt("invested") as? Number)?.toFloat() ?: Float.NaN)
        hold.add((o.opt("hold_value") as? Number)?.toFloat() ?: Float.NaN)
        proc.add((o.opt("proceeds") as? Number)?.toFloat() ?: 0f)
    }
    return EquityCurve(pv, inv, hold, proc)
}

// benchmark.series [{date, portfolio, index}] → two rebased (base-100) lines.
private fun rebasedSeries(arr: JSONArray?): Pair<List<Float>, List<Float>> {
    val p = ArrayList<Float>(); val i = ArrayList<Float>()
    if (arr != null) for (k in 0 until arr.length()) {
        val o = arr.optJSONObject(k) ?: continue
        p.add((o.opt("portfolio") as? Number)?.toFloat() ?: Float.NaN)
        i.add((o.opt("index") as? Number)?.toFloat() ?: Float.NaN)
    }
    return p to i
}

// benchmark.rolling / .monthly → (labels, portfolio%, index%)
private fun pairedPct(arr: JSONArray?, labelKey: String)
        : Triple<List<String>, List<Float>, List<Float>> {
    val l = ArrayList<String>(); val a = ArrayList<Float>(); val b = ArrayList<Float>()
    if (arr != null) for (k in 0 until arr.length()) {
        val o = arr.optJSONObject(k) ?: continue
        l.add(o.optString(labelKey))
        a.add((o.opt("portfolio_pct") as? Number)?.toFloat() ?: Float.NaN)
        b.add((o.opt("index_pct") as? Number)?.toFloat() ?: Float.NaN)
    }
    return Triple(l, a, b)
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
        if (data != null) {
            AiInsightCard(
                jobKey = "ai_risk_review",
                title = "AI risk review",
                blurb = "A pre-mortem on this book: your biggest concentration, which " +
                    "holdings would fall together and on what shared driver, and which " +
                    "upcoming events would hit several at once.",
                cta = "✨ Review my risk",
                accent = Warn,
            ) { Api.aiRiskReview() }
            Spacer(Modifier.height(24.dp))
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// DR-QUANT funnel
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun QuantScreen() {
    var universe by remember { mutableStateOf("nifty50") }
    val job = JobBus.state("quant")
    var macro by remember { mutableStateOf<JSONObject?>(null) }
    var deepSym by remember { mutableStateOf<String?>(null) }
    var symInput by remember { mutableStateOf("") }

    LaunchedEffect(Unit) { if (BackendBus.running) macro = Api.macro().objOrNull() }
    deepSym?.let { DeepDiveDialog(it) { deepSym = null } }

    ScreenScaffold(title = "DR-Quant", loading = job.running, onRefresh = null) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }
        SectionCard("Deep dive a stock", AccentHi) {
            Text("Drill into last-2-quarter results, valuation issues, and a quant " +
                "entry price for any symbol (or a funnel-validated one).",
                color = Muted, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(symInput, { symInput = it.uppercase() },
                    label = { Text("Symbol (e.g. RELIANCE)") }, singleLine = true,
                    modifier = Modifier.weight(1f))
                Spacer(Modifier.width(8.dp))
                Button(onClick = { if (symInput.isNotBlank()) deepSym = symInput.trim() },
                    enabled = symInput.isNotBlank()) { Text("🔍 Dive") }
            }
        }
        SectionCard("Funnel", Bull) {
            UniversePicker(universe) { universe = it }
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = {
                    JobBus.run("quant",
                        "Running on-device · watch the Terminal for live progress…") {
                        Api.quantRun(universe)
                    }
                },
                enabled = !job.running, modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Bull),
            ) { Text(if (job.running) "Running…" else "▶ Run funnel") }
            job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }
        }
        macro?.let { m -> MacroCard(m) }
        job.result?.let { res ->
            val p = res.optJSONObject("portfolio")
            val validated = arr(res, "validated")
            SectionCard("Result", Bull) {
                KpiGrid(listOf(
                    Triple("Candidates", fmtNum(res.opt("candidates")), OnBg),
                    Triple("Validated", fmtNum(validated?.length() ?: 0), Bull),
                    Triple("Sharpe", fmtNum(p?.opt("sharpe")), AccentHi),
                ))
                arr(res, "rejected")?.takeIf { it.length() > 0 }?.let { rej ->
                    Spacer(Modifier.height(10.dp))
                    val names = (0 until rej.length()).joinToString(", ") { rej.optString(it) }
                    Text("Rejected ${rej.length()}: $names", color = Muted,
                        fontSize = 11.sp, lineHeight = 16.sp)
                }
            }
            if (validated != null && validated.length() > 0) {
                SectionCard("Validated picks", Bull) {
                    Text("The names that survived the funnel, with why each one passed.",
                        color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(4.dp))
                    for (i in 0 until validated.length()) {
                        validated.optJSONObject(i)?.let { v ->
                            ValidatedCard(v) { deepSym = v.optString("symbol") }
                        }
                    }
                }
            }
            arr(res, "intraday_alerts")?.takeIf { it.length() > 0 }?.let {
                SectionCard("Intraday alerts", Warn) { DataTable(it) }
            }
        }
    }
}

/**
 * The DR-Quant macro tiles.
 *
 * Values that genuinely can't be fetched show "—" with the backend's own
 * explanation underneath, rather than a silent blank — a blank tile reads as a
 * broken app, and gives no way to tell a calm market from a dead feed.
 */
@Composable
private fun MacroCard(m: JSONObject) {
    // The backend calls it "mode"; this used to read "regime" and so always
    // showed the placeholder. Accept either.
    val regime = m.optString("mode").ifBlank { m.optString("regime") }.ifBlank { "—" }
    val regimeColor = when (regime) {
        "BULLISH" -> Bull
        "BEARISH" -> Bear
        else -> Warn
    }
    fun tile(label: String, key: String, suffix: String = ""): Triple<String, String, Color> {
        val v = m.opt(key)
        val txt = if (v == null || v == JSONObject.NULL) "—" else fmtNum(v) + suffix
        return Triple(label, txt, if (txt == "—") Muted else OnBg)
    }
    SectionCard("Market backdrop", Warn) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Regime", color = Muted, fontSize = 11.sp)
            Spacer(Modifier.width(10.dp))
            Pill(regime, regimeColor)
            Spacer(Modifier.weight(1f))
            m.optString("as_of").takeIf { it.isNotBlank() }?.let {
                Text(it.replace("T", " "), color = Muted, fontSize = 10.sp)
            }
        }
        Spacer(Modifier.height(12.dp))
        KpiGrid(listOf(
            tile("India VIX", "india_vix"),
            tile("Nifty today", "nifty_change_pct", "%"),
            tile("USD/INR", "usdinr"),
            tile("Nifty PCR", "nifty_pcr"),
        ))
        arr(m, "reasons")?.takeIf { it.length() > 0 }?.let { rs ->
            Spacer(Modifier.height(12.dp))
            for (i in 0 until rs.length())
                Text("• " + rs.optString(i), color = Muted, fontSize = 11.sp,
                    lineHeight = 16.sp, modifier = Modifier.padding(vertical = 1.dp))
        }
        arr(m, "notes")?.takeIf { it.length() > 0 }?.let { ns ->
            Spacer(Modifier.height(10.dp))
            for (i in 0 until ns.length()) StatusBanner(ns.optString(i), Muted)
        }
    }
}

/**
 * One validated pick, as a readable card.
 *
 * This replaced a raw DataTable. The dossier has thirteen fields of mixed type
 * — a prose thesis, an array of risks, an opaque instrument key — and rendering
 * those as thirteen columns on a phone made the most valuable screen in the app
 * unreadable. An investor wants: what is it, how strongly did it pass, why, on
 * what numbers, and what could go wrong.
 */
@Composable
private fun ValidatedCard(v: JSONObject, onDeepDive: () -> Unit) {
    val score = (v.opt("health_score") as? Number)?.toDouble()
    val scoreColor = when {
        score == null -> Muted
        score >= 70 -> Bull
        score >= 50 -> Warn
        else -> Bear
    }
    Column(
        Modifier.fillMaxWidth().padding(vertical = 6.dp)
            .clip(androidx.compose.foundation.shape.RoundedCornerShape(12.dp))
            .background(Panel2)
            .border(1.dp, BorderCol.copy(alpha = 0.7f),
                androidx.compose.foundation.shape.RoundedCornerShape(12.dp))
            .padding(14.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(v.optString("symbol", "—"), color = OnBg,
                    fontSize = 16.sp, fontWeight = FontWeight.Bold)
                v.optString("sector").takeIf { it.isNotBlank() && it != "null" }?.let {
                    Text(it, color = Muted, fontSize = 11.sp)
                }
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(score?.let { "%.0f".format(it) } ?: "—",
                    color = scoreColor, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Text("HEALTH", color = Muted, fontSize = 9.sp, letterSpacing = 0.6.sp)
            }
        }

        v.optString("thesis").takeIf { it.isNotBlank() && it != "null" }?.let {
            Spacer(Modifier.height(10.dp))
            Text(it, color = OnBg.copy(alpha = 0.9f), fontSize = 12.5.sp, lineHeight = 18.sp)
        }

        // Only show metrics the model actually returned — a row of "null"s is
        // noise, and worse, reads as a real value of zero.
        val metrics = listOfNotNull(
            metric(v, "pe", "P/E"),
            metric(v, "roe_pct", "ROE", "%"),
            metric(v, "debt_to_equity", "D/E"),
            metric(v, "sales_growth_pct", "Sales", "%", signed = true),
            metric(v, "profit_growth_pct", "Profit", "%", signed = true),
        )
        if (metrics.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                metrics.forEach { (label, value) ->
                    Column {
                        Text(label, color = Muted, fontSize = 9.5.sp, letterSpacing = 0.5.sp)
                        Spacer(Modifier.height(2.dp))
                        Text(value, color = OnBg, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }

        arr(v, "key_risks")?.takeIf { it.length() > 0 }?.let { risks ->
            Spacer(Modifier.height(12.dp))
            Text("KEY RISKS", color = Muted, fontSize = 9.5.sp, letterSpacing = 0.6.sp)
            Spacer(Modifier.height(4.dp))
            for (i in 0 until risks.length()) {
                val r = risks.optString(i)
                if (r.isNotBlank() && r != "null")
                    Text("• $r", color = Warn.copy(alpha = 0.92f), fontSize = 11.5.sp,
                        lineHeight = 16.sp, modifier = Modifier.padding(vertical = 1.dp))
            }
        }

        Spacer(Modifier.height(12.dp))
        TextButton(onClick = onDeepDive, contentPadding = PaddingValues(0.dp)) {
            Text("🔍 Deep dive ${v.optString("symbol")}", color = AccentHi, fontSize = 12.sp)
        }
    }
}

/** (label, formatted value) for a metric, or null when the model returned nothing. */
private fun metric(v: JSONObject, key: String, label: String,
                   suffix: String = "", signed: Boolean = false): Pair<String, String>? {
    val n = (v.opt(key) as? Number)?.toDouble() ?: return null
    val body = if (signed) "%+.1f".format(n) else "%.1f".format(n)
    return label to (body + suffix)
}

// ─────────────────────────────────────────────────────────────────────────────
// IDEAS — macro-infused themes → stocks → allocation toward the frontier
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun ThemesScreen() {
    var days by remember { mutableStateOf(14) }
    val job = JobBus.state("themes")
    var cash by remember { mutableStateOf("25000") }
    val allocJob = JobBus.state("alloc_themes")
    var deepSym by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    deepSym?.let { DeepDiveDialog(it) { deepSym = null } }

    ScreenScaffold(title = "Macro Ideas", loading = job.running, onRefresh = null) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }
        SectionCard("Generate ideas", AccentHi) {
            Text("Pulls recent macro, news (domestic + global), and Reddit chatter, " +
                "maps them to sectors, and picks stocks from your Universe Map — " +
                "then allocates an amount toward the efficient frontier.",
                color = Muted, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            Text("Freshness window", color = Muted, fontSize = 11.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(7, 14, 30).forEach { d ->
                    FilterChip(selected = days == d, onClick = { days = d }, label = { Text("${d}d") })
                }
            }
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = {
                    JobBus.clear("alloc_themes")
                    JobBus.run("themes",
                        "Ingesting macro/news/Reddit + reasoning over your universe…",
                        maxSecs = 300) { Api.themes(days) }
                },
                enabled = !job.running, modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Bull),
            ) { Text(if (job.running) "Thinking…" else "✨ Generate macro ideas") }
            job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }
        }

        job.result?.let { r ->
            val macro = r.optJSONObject("macro")
            val counts = r.optJSONObject("counts")
            SectionCard("As of ${r.optString("as_of")} · last ${r.optInt("window_days")}d", AccentHi) {
                StatusBanner("Market mode: ${macro?.optString("mode", "—")}  ·  " +
                    "VIX ${macro?.opt("india_vix")} · PCR ${macro?.opt("nifty_pcr")} · " +
                    "USDINR ${macro?.opt("usdinr")}\nSignals: ${counts?.optInt("news") ?: 0} news, " +
                    "${counts?.optInt("reddit") ?: 0} reddit, ${counts?.optInt("universe") ?: 0} universe stocks",
                    AccentHi)
            }
            r.optString("macro_view").takeIf { it.isNotBlank() }?.let { view ->
                SectionCard("Holistic macro view", AccentHi) {
                    Text(view, color = OnBg, fontSize = 13.sp, lineHeight = 19.sp)
                }
            }
            val picks = r.optJSONArray("picks")
            if (picks == null || picks.length() == 0) {
                SectionCard("No picks", Muted) { Text("No high-conviction picks from recent " +
                    "signals. Build/refresh the Universe Map for more coverage.",
                    color = Muted, fontSize = 12.sp) }
            } else {
                SectionCard("Top picks (${picks.length()})", Bull) {
                    Text("All factors weighed together. Tap a stock for the full deep dive.",
                        color = Muted, fontSize = 11.sp)
                }
                for (i in 0 until picks.length()) {
                    val p = picks.optJSONObject(i) ?: continue
                    val sym = p.optString("symbol")
                    val conv = p.optString("conviction", "MEDIUM")
                    val col = when (conv) { "HIGH" -> Bull; "LOW" -> Muted; else -> AccentHi }
                    val e = p.optJSONObject("entry")
                    SectionCard(sym, col, trailing = { Pill(conv, col) }) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(p.optString("sector", ""), color = Muted, fontSize = 11.sp,
                                modifier = Modifier.weight(1f))
                            Text("🔍 deep dive", color = AccentHi, fontSize = 11.sp,
                                modifier = Modifier.clickable { deepSym = sym })
                        }
                        if (e != null) {
                            Spacer(Modifier.height(8.dp))
                            StatusBanner("Entry ₹${fmtNum(e.opt("suggested_entry"))}  " +
                                "(zone ₹${fmtNum(e.opt("entry_low"))}–${fmtNum(e.opt("entry_high"))})  ·  " +
                                "CMP ₹${fmtNum(e.opt("current"))}  ·  50-DMA ₹${fmtNum(e.opt("dma50"))}  ·  " +
                                "RSI ${fmtNum(e.opt("rsi"))}", Bull)
                        }
                        Spacer(Modifier.height(8.dp))
                        Text(p.optString("thesis", ""), color = OnBg.copy(alpha = 0.9f),
                            fontSize = 13.sp, lineHeight = 18.sp)
                    }
                }

                // Allocate the idea stocks toward the efficient frontier.
                val tickers = buildList {
                    r.optJSONArray("tickers")?.let { for (k in 0 until it.length()) add(it.optString(k)) }
                }
                SectionCard("Allocate toward the frontier", Bull) {
                    Text("Deploy an amount across these ${tickers.size} idea stocks to best " +
                        "improve your portfolio's risk/return (₹ + whole shares).",
                        color = Muted, fontSize = 12.sp)
                    Spacer(Modifier.height(8.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(cash, { cash = it.filter { c -> c.isDigit() } },
                            label = { Text("Amount (₹)") }, singleLine = true, modifier = Modifier.weight(1f))
                        Spacer(Modifier.width(8.dp))
                        Button(onClick = {
                            val amt = cash.toDoubleOrNull() ?: 0.0
                            if (amt > 0 && tickers.isNotEmpty()) {
                                JobBus.runSync("alloc_themes", "Optimising allocation…") {
                                    Api.deployCashTickers(amt, tickers)
                                }
                            }
                        }, enabled = !allocJob.running) { Text(if (allocJob.running) "…" else "Allocate") }
                    }
                    allocJob.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (allocJob.running) Warn else Bear) }
                    allocJob.result?.let { a ->
                        val before = a.optJSONObject("before"); val after = a.optJSONObject("after")
                        Spacer(Modifier.height(10.dp))
                        KpiGrid(listOf(
                            Triple("Sharpe now", fmtNum(before?.opt("sharpe")), OnBg),
                            Triple("Sharpe after", fmtNum(after?.opt("sharpe")), Bull),
                        ))
                        Spacer(Modifier.height(10.dp))
                        DataTable(allocationBuys(arr(a, "buys")))
                    }
                }
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
    val job = JobBus.state("umap")
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var quadrant by remember { mutableStateOf<Quadrant?>(null) }
    val scope = rememberCoroutineScope()

    fun loadCached() {
        scope.launch { data = Api.umapData(universe).objOrNull() }
    }
    LaunchedEffect(Unit) { if (BackendBus.running) loadCached() }
    // Follow the picker — the map used to always show all_nse no matter which
    // universe was selected.
    LaunchedEffect(universe) { if (BackendBus.running) { quadrant = null; loadCached() } }
    // When a build completes, pull the freshly written map data.
    LaunchedEffect(job.finishedAt) { if (job.finishedAt > 0L && BackendBus.running) loadCached() }
    // The builder checkpoints to disk as it goes, so the map can fill in while
    // the crawl is still running instead of showing nothing for an hour.
    LaunchedEffect(job.running) {
        while (job.running) { delay(45_000); if (BackendBus.running) loadCached() }
    }

    ScreenScaffold(title = "Universe Map", loading = job.running, onRefresh = ::loadCached) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }
        SectionCard("Build", AccentHi) {
            UniversePicker(universe) { universe = it }
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = {
                    JobBus.run(
                        key = "umap",
                        status = "Crawling the universe — this takes a while on first run.",
                        // Idle timeout, not a total budget: a first all-NSE crawl
                        // can run for an hour and must not be abandoned.
                        maxSecs = 300,
                        onTick = { id, st ->
                            Api.umapProgress(id).objOrNull()
                                ?.optJSONObject("progress")?.let { pr ->
                                    val done = pr.optInt("done"); val total = pr.optInt("total")
                                    st.progress = if (total > 0) done.toFloat() / total else null
                                    st.detail = pr.optString("message").takeIf { it.isNotBlank() }
                                        ?.let { m ->
                                            val f = pr.optInt("fetched"); val r = pr.optInt("reused")
                                            if (f + r > 0) "$m · $f fetched, $r reused" else m
                                        }
                                }
                        },
                    ) { Api.umapBuild(universe) }
                },
                enabled = !job.running, modifier = Modifier.fillMaxWidth(),
            ) { Text(if (job.running) "Building…" else "▶ Build / refresh map") }

            if (!job.running) {
                Spacer(Modifier.height(8.dp))
                Text("The first full build crawls every stock and can take a long while — " +
                    "it saves as it goes, so stopping and running it again later picks up " +
                    "where it left off rather than starting over.",
                    color = Muted, fontSize = 10.5.sp, lineHeight = 15.sp)
            }

            if (job.running) {
                Spacer(Modifier.height(12.dp))
                val pct = job.progress
                if (pct != null) {
                    LinearProgressIndicator(progress = pct.coerceIn(0f, 1f),
                        modifier = Modifier.fillMaxWidth(), color = AccentHi, trackColor = Panel2)
                    Spacer(Modifier.height(6.dp))
                    Text("%.0f%% · %s".format(pct * 100, job.detail ?: "working…"),
                        color = Muted, fontSize = 11.sp)
                } else {
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth(),
                        color = AccentHi, trackColor = Panel2)
                    Spacer(Modifier.height(6.dp))
                    Text(job.detail ?: "Scanning the universe for price data…",
                        color = Muted, fontSize = 11.sp)
                }
                Spacer(Modifier.height(6.dp))
                Text("Safe to leave this screen — the crawl keeps running and saves " +
                    "its progress every 25 stocks.", color = Muted, fontSize = 10.sp)
            }
            job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }
        }

        // An interrupted crawl now leaves a usable map rather than nothing.
        data?.takeIf { it.optBoolean("partial") }?.let { d ->
            SectionCard("Partial map", Warn) {
                StatusBanner("This map is incomplete — ${d.optInt("count")} of " +
                    "${d.optInt("expected_total")} stocks were saved before the last " +
                    "build stopped. It is still usable; run the build again to finish " +
                    "it — everything already fetched is reused, so it picks up where " +
                    "it left off.", Warn)
            }
        }
        data?.optString("error")?.takeIf { it.isNotBlank() }?.let {
            SectionCard("Last build reported", Bear) { StatusBanner(it, Bear) }
        }

        data?.takeIf { it.optBoolean("ok", true) && it.has("count") }?.let { rep ->
            SectionCard("Stats", AccentHi) {
                KpiGrid(listOf(
                    Triple("Stocks", fmtNum(rep.opt("count")), OnBg),
                    Triple("Tech scored", fmtNum(rep.opt("tech_total")), OnBg),
                    Triple("Fetched", fmtNum(rep.opt("fund_scanned")), AccentHi),
                    Triple("Reused", fmtNum(rep.opt("fund_reused")), Muted),
                ))
                rep.optString("built_at").takeIf { it.isNotBlank() }?.let {
                    Spacer(Modifier.height(8.dp))
                    Text("Built ${it.take(16).replace("T", " ")} UTC · ${rep.optString("universe")}",
                        color = Muted, fontSize = 10.sp)
                }
            }
        }
        if (arr(data, "stocks").let { it == null || it.length() == 0 } && !job.running) {
            SectionCard("No map yet", Muted) {
                StatusBanner(data?.optString("error")?.takeIf { it.isNotBlank() }
                    ?: "Nothing cached for \"$universe\" yet. Build it above — the first " +
                       "run is the long one; later runs reuse everything still fresh.", Muted)
            }
        }
        arr(data, "stocks")?.takeIf { it.length() > 0 }?.let { stocks ->
            val pts = universePoints(stocks)
            SectionCard("Map · technical vs fundamental", AccentHi) {
                Text("Where every stock sits on balance-sheet quality (→) against price " +
                    "action (↑). Both lines split at a score of 50, so the corner a " +
                    "stock lands in is what matters.",
                    color = Muted, fontSize = 11.sp, lineHeight = 16.sp)
                Spacer(Modifier.height(12.dp))
                QuadrantScatterChart(pts, xLabel = "Fundamental score",
                    yLabel = "Technical score", highlight = quadrant)
                Spacer(Modifier.height(12.dp))
                Text("Tap a quadrant to filter the table below.",
                    color = Muted, fontSize = 11.sp)
                Spacer(Modifier.height(8.dp))
                Quadrant.values().forEach { q ->
                    val n = pts.count { quadrantOf(it.x, it.y) == q }
                    QuadrantRow(q, n, selected = quadrant == q) {
                        quadrant = if (quadrant == q) null else q
                    }
                }
                if (pts.size < stocks.length()) {
                    Spacer(Modifier.height(10.dp))
                    Text("${stocks.length() - pts.size} stocks aren't plotted — they have " +
                        "no fundamental score yet (still being crawled, or the source " +
                        "had nothing).", color = Muted, fontSize = 10.sp, lineHeight = 15.sp)
                }
            }
            val shown = filterByQuadrant(stocks, quadrant)
            SectionCard(quadrant?.let { "Universe · ${it.label}" } ?: "Universe", AccentHi) {
                if (quadrant != null) {
                    Text("${shown.length()} stocks · ${quadrant!!.blurb}",
                        color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(8.dp))
                }
                DataTable(shown, 80)
            }
        }
    }
}

/** One tappable quadrant summary row under the map. */
@Composable
private fun QuadrantRow(q: Quadrant, count: Int, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .padding(vertical = 3.dp)
            .clip(androidx.compose.foundation.shape.RoundedCornerShape(8.dp))
            .background(if (selected) q.tint.copy(alpha = 0.16f) else Panel2)
            .clickable { onClick() }
            .padding(horizontal = 11.dp, vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(9.dp)
            .clip(androidx.compose.foundation.shape.RoundedCornerShape(2.dp))
            .background(q.tint))
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(q.label, color = OnBg, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
            Text(q.blurb, color = Muted, fontSize = 10.5.sp)
        }
        Text("$count", color = q.tint, fontSize = 14.sp, fontWeight = FontWeight.Bold)
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
private fun universePoints(arr: JSONArray): List<UniversePoint> {
    val out = ArrayList<UniversePoint>()
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        // A stock with no fundamentals yet (fetch failed, or still mid-crawl)
        // must not be plotted at its technical score on BOTH axes — that would
        // pile it onto the diagonal and misreport the quadrant counts.
        val tech = (o.opt("tech_score") as? Number)?.toFloat() ?: continue
        val fund = (o.opt("fund_score") as? Number)?.toFloat() ?: continue
        val reco = o.optString("recommendation", "")
        val c = when {
            reco.contains("STRONG_BUY") || reco == "BUY" || reco.contains("TECH_BUY") -> Bull
            reco.contains("HOLD") || reco.contains("WATCH") -> Warn
            reco.contains("AVOID") || reco.contains("SELL") -> Bear
            else -> Muted
        }
        out.add(UniversePoint(x = fund, y = tech, color = c, symbol = o.optString("symbol")))
    }
    return out
}

/** Rows of the universe table that fall in one quadrant of the map. */
private fun filterByQuadrant(arr: JSONArray, q: Quadrant?): JSONArray {
    if (q == null) return arr
    val out = JSONArray()
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        val tech = (o.opt("tech_score") as? Number)?.toFloat() ?: continue
        val fund = (o.opt("fund_score") as? Number)?.toFloat() ?: continue
        if (quadrantOf(fund, tech) == q) out.put(o)
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
    val job = JobBus.state("screener")
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }
        SectionCard("Screener funnel", AccentHi) {
            UniversePicker(universe) { universe = it }
            Spacer(Modifier.height(6.dp))
            Text("Min score: $minScore", color = Muted, fontSize = 12.sp)
            Slider(value = minScore.toFloat(), onValueChange = { minScore = it.toInt() },
                valueRange = 0f..100f)
            Button(onClick = {
                JobBus.run("screener", "Scanning universe…") { Api.screener(universe, minScore) }
            }, enabled = !job.running, modifier = Modifier.fillMaxWidth()) {
                Text(if (job.running) "Scanning…" else "\u25b6 Run screener")
            }
            job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }
        }
        arr(job.result, "results")?.let { SectionCard("Results", Bull) { DataTable(it) } }
    }
}

@Composable private fun IntradayTab() {
    var days by remember { mutableStateOf(90) }
    val job = JobBus.state("intraday")
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }
        SectionCard("Trade analysis", AccentHi) {
            Text("Lookback: $days days", color = Muted, fontSize = 12.sp)
            Slider(value = days.toFloat(), onValueChange = { days = it.toInt() }, valueRange = 30f..365f)
            Button(onClick = {
                JobBus.run("intraday", "Analysing your trades…") { Api.intradayAnalyze(days) }
            }, enabled = !job.running, modifier = Modifier.fillMaxWidth()) {
                Text(if (job.running) "Analyzing…" else "▶ Analyze my trades")
            }
            job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }
        }
        job.result?.let { r ->
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
    val job = JobBus.state("performance")
    // Seed from the backend cache once, so returning to the tab shows the last run.
    LaunchedEffect(Unit) {
        if (BackendBus.running && job.result == null && !job.running) {
            Api.performanceCached().objOrNull()?.optJSONObject("data")?.let { JobBus.seed("performance", it) }
        }
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }
        SectionCard("Performance over time", AccentHi) {
            Text("Reconstructs your portfolio value day-by-day from your executed " +
                "orders, vs the capital you put in — plus money-weighted return (XIRR), " +
                "winners and losers.", color = Muted, fontSize = 12.sp)
            Spacer(Modifier.height(10.dp))
            Button(onClick = {
                JobBus.run("performance",
                    "Analysing — fetching trade history & building the curve…") { Api.performance() }
            }, enabled = !job.running, modifier = Modifier.fillMaxWidth()) {
                Text(if (job.running) "Analysing…" else "▶ Analyse performance")
            }
            job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }
        }
        job.result?.let { r ->
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
                    Triple("XIRR", (r.opt("xirr") as? Number)?.let { "%.2f%%".format(it.toDouble()) } ?: "—", AccentHi),
                    Triple("Trades", fmtNum(s.opt("total_trades")), OnBg),
                ))
                // Period returns sit above the vs-index block; say which basis
                // they use so the two numbers can be read against each other.
                r.optJSONObject("returns")?.let { ret ->
                    Spacer(Modifier.height(10.dp))
                    Text(
                        if (ret.optString("basis") == "time_weighted")
                            "Period returns are time-weighted — money you paid in isn't counted " +
                            "as a gain, so they compare like-for-like with the index below."
                        else
                            "Period returns are raw value growth (this run predates cash-flow " +
                            "tracking) — re-run the analysis for time-weighted figures.",
                        color = Muted, fontSize = 10.sp, lineHeight = 15.sp)
                }
            }
            val curve = arr(r, "equity_curve")
            val ec = equitySeries(curve)
            val pv = ec.portfolio
            SectionCard("Portfolio value vs invested vs if-held", Bull) {
                if (pv.size >= 2) {
                    LineChart(listOf(
                        ChartSeries(pv, "Portfolio", AccentHi, filled = true),
                        ChartSeries(ec.invested, "Invested", Muted, dashed = true),
                        ChartSeries(ec.hold, "If never sold", Warn),
                    ))
                    // Fair comparison: after selling you hold stock AND the cash
                    // you received, so compare (portfolio + proceeds) to if-held.
                    val heldNow = ec.hold.lastOrNull { it.isFinite() }
                    val nowVal = pv.lastOrNull { it.isFinite() }
                    val cash = ec.proceeds.lastOrNull { it.isFinite() } ?: 0f
                    if (heldNow != null && nowVal != null) {
                        val gap = heldNow - (nowVal + cash)
                        if (kotlin.math.abs(gap) > 1f) {
                            Spacer(Modifier.height(10.dp))
                            StatusBanner(
                                (if (gap > 0)
                                    "Selling cost you ₹${fmtCompact(gap)}."
                                else
                                    "Selling saved you ₹${fmtCompact(-gap)}.") +
                                "\nHolding everything: ₹${fmtCompact(heldNow)}  vs  " +
                                "what you have now: ₹${fmtCompact(nowVal)} in stock" +
                                (if (cash > 1f) " + ₹${fmtCompact(cash)} cash from sales" else "") + ".",
                                if (gap > 0) Bear else Bull)
                        }
                    }
                } else StatusBanner("No time series yet. This needs your executed order " +
                    "history — on Groww with limited API access it may be unavailable; " +
                    "Upstox provides full history.", Warn)
            }
            arr(r, "opportunity_misses")?.takeIf { it.length() > 0 }?.let { misses ->
                var missed = 0.0
                for (i in 0 until misses.length())
                    missed += (misses.optJSONObject(i)?.opt("missed_value") as? Number)?.toDouble() ?: 0.0
                SectionCard("Lost opportunity — sold too early", Warn) {
                    Text("Stocks you fully exited that are worth more now. " +
                        "\"Missed\" = (price now − your avg sell) × qty sold.",
                        color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(10.dp))
                    KpiGrid(listOf(
                        Triple("Total left on table", "₹" + fmtCompact(missed), Bear),
                        Triple("Positions", fmtNum(misses.length()), OnBg),
                    ))
                    Spacer(Modifier.height(10.dp))
                    DataTable(misses)
                }
            }
            BenchmarkSection(r)
            arr(r, "winners")?.takeIf { it.length() > 0 }?.let { SectionCard("Winners", Bull) { DataTable(it) } }
            arr(r, "losers")?.takeIf { it.length() > 0 }?.let { SectionCard("Losers", Bear) { DataTable(it) } }
            AiInsightCard(
                jobKey = "ai_perf_review",
                title = "AI performance review",
                blurb = "Reads this whole report — the index comparison, your monthly " +
                    "pattern, winners, losers and what you sold too early — and says " +
                    "where the return actually came from and what is costing you.",
                cta = "✨ Review my track record",
                accent = Bull,
            ) { Api.aiPerformanceReview() }
            Spacer(Modifier.height(24.dp))
        }
    }
}

/**
 * "How did I do against the market?" — the vs-index block of the Performance tab.
 *
 * Everything here is TIME-WEIGHTED, computed on the backend: money you paid in
 * is stripped out of the return, so a big deposit doesn't masquerade as a great
 * year and the number is directly comparable to NIFTY.
 */
@Composable
private fun BenchmarkSection(report: JSONObject) {
    val bench = report.optJSONObject("benchmark")
    val job = JobBus.state("benchmark")
    // Prefer a freshly-recomputed comparison (e.g. after switching index).
    val b = job.result ?: bench
    var index by remember { mutableStateOf("^NSEI") }

    val indices = listOf("^NSEI" to "NIFTY 50", "^BSESN" to "SENSEX",
        "^NSEBANK" to "NIFTY BANK", "^CNX100" to "NIFTY 100")

    // Self-heal. The comparison is normally computed inside the performance
    // report, but that report may be an older cached one whose benchmark
    // failed or predates this feature. Rather than telling the user to re-run
    // a long analysis they already ran, recompute here from the equity curve
    // the backend still has cached — it's a couple of seconds.
    LaunchedEffect(report) {
        if (bench?.optJSONObject("stats") == null && job.result == null && !job.running) {
            JobBus.runSync("benchmark", "Comparing against NIFTY 50…") { Api.benchmark(index, 365) }
        }
    }

    SectionCard("You vs the market", AccentHi) {
        Text("Time-weighted return, so deposits and withdrawals don't count as " +
            "gains — this measures your stock picking against the index on equal terms.",
            color = Muted, fontSize = 12.sp, lineHeight = 17.sp)
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.horizontalScroll(rememberScrollState())) {
            indices.forEach { (sym, label) ->
                FilterChip(selected = index == sym,
                    onClick = {
                        index = sym
                        JobBus.clear("benchmark")
                        JobBus.runSync("benchmark", "Fetching $label…") { Api.benchmark(sym, 365) }
                    },
                    label = { Text(label) })
            }
        }
        job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }

        if (b == null || b.optJSONObject("stats") == null) {
            // Surface the actual reason. Showing "run the analysis first" when
            // the analysis HAS been run and the comparison failed for some
            // other reason just sends the user in circles.
            val why = b?.optString("error").orEmpty()
            if (!job.running) {
                Spacer(Modifier.height(10.dp))
                StatusBanner(
                    if (why.isNotBlank()) "Couldn't build the comparison: $why"
                    else "No comparison yet — it is built from your equity curve, " +
                        "so it needs the performance analysis above to have produced one.",
                    Warn)
            }
            return@SectionCard
        }

        val st = b.optJSONObject("stats") ?: JSONObject()
        fun d(k: String): Double? = (st.opt(k) as? Number)?.toDouble()
        fun pct(k: String) = d(k)?.let { "%.2f%%".format(it) } ?: "—"
        fun plain(k: String) = d(k)?.let { "%.2f".format(it) } ?: "—"
        val name = b.optJSONObject("benchmark")?.optString("name") ?: "index"
        val excess = d("excess_pct") ?: 0.0

        Spacer(Modifier.height(14.dp))
        StatusBanner(
            (if (excess >= 0) "You beat $name by %.2f%% ".format(excess)
             else "You trailed $name by %.2f%% ".format(-excess)) +
            "over the last year — you %.2f%% vs %s %.2f%%.".format(
                d("portfolio_return_pct") ?: 0.0, name, d("index_return_pct") ?: 0.0),
            if (excess >= 0) Bull else Bear)

        Spacer(Modifier.height(14.dp))
        val (pSeries, iSeries) = rebasedSeries(arr(b, "series"))
        if (pSeries.size >= 2) {
            Text("₹100 invested a year ago", color = Muted, fontSize = 11.sp)
            Spacer(Modifier.height(6.dp))
            LineChart(listOf(
                ChartSeries(pSeries, "You", AccentHi, filled = true),
                ChartSeries(iSeries, name, Warn),
            ))
        }

        Spacer(Modifier.height(14.dp))
        KpiGrid(listOf(
            Triple("Your 1Y return", pct("portfolio_return_pct"),
                if ((d("portfolio_return_pct") ?: 0.0) >= 0) Bull else Bear),
            Triple("$name 1Y", pct("index_return_pct"),
                if ((d("index_return_pct") ?: 0.0) >= 0) Bull else Bear),
            Triple("Excess return", pct("excess_pct"), if (excess >= 0) Bull else Bear),
            Triple("Alpha (annual)", pct("alpha_pct"),
                if ((d("alpha_pct") ?: 0.0) >= 0) Bull else Bear),
            Triple("Beta", plain("beta"), OnBg),
            Triple("Correlation", plain("correlation"), OnBg),
            Triple("Your volatility", pct("portfolio_vol_pct"), Warn),
            Triple("$name volatility", pct("index_vol_pct"), Muted),
            Triple("Up capture", pct("up_capture_pct"), Bull),
            Triple("Down capture", pct("down_capture_pct"), Bear),
            Triple("Your worst fall", pct("portfolio_max_drawdown_pct"), Bear),
            Triple("$name worst fall", pct("index_max_drawdown_pct"), Muted),
        ))
        Spacer(Modifier.height(10.dp))
        Text(readBeta(d("beta"), d("up_capture_pct"), d("down_capture_pct")),
            color = Muted, fontSize = 11.sp, lineHeight = 16.sp)
    }

    // Rolling trailing-1Y return — "moving return", the shape of your form.
    val (rollLabels, rollP, rollI) = pairedPct(arr(b ?: JSONObject(), "rolling"), "date")
    if (rollP.size >= 2) {
        SectionCard("Rolling 1-year return", Bull) {
            Text("At every point, what you made over the previous 12 months versus " +
                "the index. Flat stretches above the index line are consistency; " +
                "spikes are single bets landing.", color = Muted, fontSize = 11.sp, lineHeight = 16.sp)
            Spacer(Modifier.height(10.dp))
            LineChart(listOf(
                ChartSeries(rollP, "You (1Y trailing)", AccentHi),
                ChartSeries(rollI, "Index (1Y trailing)", Warn, dashed = true),
            ))
        }
    }

    val (mLabels, mP, mI) = pairedPct(arr(b ?: JSONObject(), "monthly"), "month")
    if (mP.isNotEmpty()) {
        SectionCard("Month by month", AccentHi) {
            BarPairChart(mLabels, mP, mI, "You", "Index")
            Spacer(Modifier.height(10.dp))
            val wins = mP.indices.count { mP[it].isFinite() && mI[it].isFinite() && mP[it] > mI[it] }
            KpiGrid(listOf(
                Triple("Months beaten", "$wins of ${mP.size}", if (wins * 2 >= mP.size) Bull else Bear),
                Triple("Best month", mP.filter { it.isFinite() }.maxOrNull()
                    ?.let { "%.2f%%".format(it) } ?: "—", Bull),
                Triple("Worst month", mP.filter { it.isFinite() }.minOrNull()
                    ?.let { "%.2f%%".format(it) } ?: "—", Bear),
            ))
        }
    }
    (b ?: JSONObject()).optString("note").takeIf { it.isNotBlank() }?.let {
        SectionCard("How this is measured", Muted) {
            Text(it, color = Muted, fontSize = 11.sp, lineHeight = 16.sp)
        }
    }
}

/** Plain-English reading of the risk stats, so the KPI grid isn't just jargon. */
private fun readBeta(beta: Double?, up: Double?, down: Double?): String {
    if (beta == null) return ""
    val swing = when {
        beta > 1.15 -> "Your book swings harder than the index (beta %.2f) — expect bigger moves both ways.".format(beta)
        beta < 0.85 -> "Your book is steadier than the index (beta %.2f).".format(beta)
        else -> "Your book moves broadly with the index (beta %.2f).".format(beta)
    }
    if (up == null || down == null) return swing
    val capture = when {
        up > 100 && down < 100 ->
            " You capture %.0f%% of the market's rallies but only %.0f%% of its falls — the combination you want.".format(up, down)
        up < 100 && down > 100 ->
            " You capture only %.0f%% of rallies but %.0f%% of falls — the wrong way round.".format(up, down)
        else -> " Rally capture %.0f%%, fall capture %.0f%%.".format(up, down)
    }
    return swing + capture
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
    val job = JobBus.state("optimize")
    // Deploy-cash (reallocation by amount)
    var cash by remember { mutableStateOf("15000") }
    val deployJob = JobBus.state("deploy_cash")
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
                        if (amt > 0) {
                            JobBus.runSync("deploy_cash", "Computing allocation…") {
                                Api.deployCash(amt)
                            }
                        }
                    },
                    enabled = !deployJob.running,
                ) { Text(if (deployJob.running) "…" else "Suggest") }
            }
            deployJob.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (deployJob.running) Warn else Bear) }
            deployJob.result?.let { d ->
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
                    JobBus.run("optimize",
                        "Optimising… (prices via broker or Yahoo fallback)") {
                        Api.optimize(mode, maxW / 100.0)
                    }
                },
                enabled = !job.running, modifier = Modifier.fillMaxWidth(),
            ) { Text(if (job.running) "Optimising…" else "▶ Optimise portfolio") }
            job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }
        }
        job.result?.let { r ->
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
// DEEP DIVE — last-2-quarter results, valuation issues, quant entry price
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun DeepDiveDialog(symbol: String, onDismiss: () -> Unit) {
    // Keyed per symbol so a dive survives closing/reopening the dialog and
    // navigating away; reopening the same symbol shows the finished result.
    val job = JobBus.state("deepdive:$symbol")
    LaunchedEffect(symbol) {
        if (job.result == null && !job.running) {
            JobBus.run("deepdive:$symbol", "Analysing…", maxSecs = 240) { Api.deepDive(symbol) }
        }
    }
    val loading = job.running
    val res = job.result
    val error = job.status
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
        title = { Text("Deep dive · $symbol") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (loading) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = AccentHi)
                        Spacer(Modifier.width(10.dp))
                        Text("Pulling results PDFs, fundamentals & price model…",
                            color = Muted, fontSize = 12.sp)
                    }
                }
                error?.let { StatusBanner(it, Bear) }
                res?.let { r ->
                    val f = r.optJSONObject("fundamentals") ?: JSONObject()
                    val e = r.optJSONObject("entry") ?: JSONObject()
                    val a = r.optJSONObject("analysis") ?: JSONObject()

                    Text("Valuation & quality", color = AccentHi, fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(6.dp))
                    KpiGrid(listOf(
                        Triple("PE", fmtNum(f.opt("pe")), OnBg),
                        Triple("ROE", fmtNum(f.opt("roe_pct")) + "%", OnBg),
                        Triple("D/E", fmtNum(f.opt("debt_to_equity")), OnBg),
                        Triple("Profit gr.", fmtNum(f.opt("profit_growth_pct")) + "%",
                            if (((f.opt("profit_growth_pct") as? Number)?.toDouble() ?: 0.0) >= 0) Bull else Bear),
                    ))

                    Spacer(Modifier.height(12.dp))
                    Text("Quant entry", color = AccentHi, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(6.dp))
                    StatusBanner("CMP ₹${fmtNum(e.opt("current"))}  ·  50-DMA ₹${fmtNum(e.opt("dma50"))}  ·  " +
                        "RSI ${fmtNum(e.opt("rsi"))}\nSuggested entry ₹${fmtNum(e.opt("suggested_entry"))} " +
                        "(zone ₹${fmtNum(e.opt("entry_low"))}–${fmtNum(e.opt("entry_high"))}, " +
                        "${fmtNum(e.opt("discount_to_cmp_pct"))}% below CMP)\n${e.optString("note", "")}", Bull)

                    Spacer(Modifier.height(12.dp))
                    a.optString("verdict").takeIf { it.isNotBlank() }?.let { Pill(it, AccentHi); Spacer(Modifier.height(8.dp)) }
                    deepText("Financial health", a.optString("financial_health"))
                    deepText("Last 2 quarters", a.optString("quarter_trend"))
                    deepText("Valuation", a.optString("valuation"))
                    deepText("Entry view", a.optString("entry_view"))
                    deepList("Issues", a.optJSONArray("issues"), Warn)
                    deepList("Red flags", a.optJSONArray("red_flags"), Bear)
                    a.optString("raw").takeIf { it.isNotBlank() }?.let {
                        Spacer(Modifier.height(8.dp)); Text(it, color = Muted, fontSize = 11.sp)
                    }
                    val srcs = r.optJSONArray("sources")
                    if (srcs != null && srcs.length() > 0) {
                        Spacer(Modifier.height(10.dp))
                        Text("Sources", color = Muted, fontSize = 11.sp)
                        for (i in 0 until srcs.length())
                            Text("• ${srcs.optJSONObject(i)?.optString("title")}",
                                color = Muted, fontSize = 11.sp, maxLines = 1)
                    }
                }
            }
        },
    )
}

@Composable
private fun deepText(label: String, value: String?) {
    if (value.isNullOrBlank()) return
    Spacer(Modifier.height(8.dp))
    Text(label, color = Muted, fontSize = 11.sp)
    Text(value, color = OnBg, fontSize = 13.sp, lineHeight = 18.sp)
}

@Composable
private fun deepList(label: String, arr: org.json.JSONArray?, color: Color) {
    if (arr == null || arr.length() == 0) return
    Spacer(Modifier.height(8.dp))
    Text(label, color = color, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
    for (i in 0 until arr.length())
        Text("• ${arr.optString(i)}", color = OnBg.copy(alpha = 0.9f), fontSize = 12.sp,
            lineHeight = 16.sp, modifier = Modifier.padding(vertical = 1.dp))
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

// ─────────────────────────────────────────────────────────────────────────────
// AI insight card — one reusable shell for every LLM application.
//
// They all behave identically: submit a job, poll it, render markdown. Keeping
// that in one place means a new AI feature is three lines at the call site, and
// every one of them inherits JobBus's survive-navigation / never-double-submit
// guarantees.
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun AiInsightCard(
    jobKey: String,
    title: String,
    blurb: String,
    cta: String,
    accent: Color = AccentHi,
    submit: suspend () -> Api.Resp,
) {
    val job = JobBus.state(jobKey)
    SectionCard(title, accent) {
        Text(blurb, color = Muted, fontSize = 12.sp, lineHeight = 17.sp)
        Spacer(Modifier.height(10.dp))
        Button(
            onClick = {
                JobBus.run(jobKey, "Thinking — a slow model can take a minute or two…",
                    maxSecs = 480) { submit() }
            },
            enabled = !job.running && BackendBus.running,
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (job.running) "Thinking…" else cta) }

        job.status?.let {
            Spacer(Modifier.height(8.dp))
            StatusBanner(it, if (job.running) Warn else Bear)
        }
        job.result?.let { r ->
            Spacer(Modifier.height(12.dp))
            val text = r.optString("text", "")
            if (r.optBoolean("ok", text.isNotBlank()) && text.isNotBlank()) {
                MarkdownText(text)
                r.optJSONObject("grounding")?.let { g ->
                    Spacer(Modifier.height(10.dp))
                    Text("Grounded in ${g.optInt("events")} scheduled events and " +
                        "${g.optInt("news")} recent headlines" +
                        (if (g.optBoolean("has_benchmark")) ", plus your benchmark stats." else "."),
                        color = Muted, fontSize = 10.sp)
                }
            } else {
                StatusBanner(r.optString("error", "The model returned nothing."), Bear)
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// CALENDAR · market-moving events + news bulletin
// ─────────────────────────────────────────────────────────────────────────────

private fun impactColor(importance: String): Color = when (importance) {
    "HIGH" -> Bear
    "MEDIUM" -> Warn
    else -> Muted
}

private fun categoryIcon(category: String): String = when (category) {
    "MONETARY" -> "🏦"
    "INFLATION" -> "📈"
    "GROWTH" -> "🏗"
    "JOBS" -> "👷"
    "EARNINGS" -> "📊"
    "EXPIRY" -> "⏱"
    "POLICY" -> "🏛"
    else -> "•"
}

/**
 * Today + [days] as an ISO date. Uses Calendar, not java.time — minSdk is 24
 * and the project doesn't enable core-library desugaring, so java.time would
 * crash on older devices.
 *
 * ISO-8601 strings sort lexicographically, so callers can compare them directly.
 */
private fun isoPlusDays(days: Int): String {
    val c = java.util.Calendar.getInstance()
    c.add(java.util.Calendar.DAY_OF_YEAR, days)
    return "%04d-%02d-%02d".format(
        c.get(java.util.Calendar.YEAR),
        c.get(java.util.Calendar.MONTH) + 1,
        c.get(java.util.Calendar.DAY_OF_MONTH))
}

/** "2026-09-16" → "Wed 16 Sep". Falls back to the raw string if unparseable. */
private fun prettyDate(iso: String, weekday: String): String {
    val parts = iso.split("-")
    if (parts.size != 3) return iso
    val months = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    val m = parts[1].toIntOrNull() ?: return iso
    val d = parts[2].toIntOrNull() ?: return iso
    return "$weekday $d ${months.getOrElse(m - 1) { parts[1] }}"
}

@Composable
fun CalendarScreen() {
    val job = JobBus.state("calendar")
    var horizon by remember { mutableStateOf(60) }
    var highOnly by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<JSONObject?>(null) }

    // Seed from the backend's cache so switching tabs doesn't refetch ~20 feeds.
    LaunchedEffect(Unit) {
        if (BackendBus.running && job.result == null && !job.running) {
            val cached = Api.calendarCached().objOrNull()?.optJSONObject("data")
            if (cached != null) JobBus.seed("calendar", cached)
            else JobBus.run("calendar", "Reading the Fed calendar, RBI and 18 news feeds…",
                maxSecs = 240) { Api.calendar(horizon, 7) }
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }

        val data = job.result
        SectionCard("Market calendar", AccentHi) {
            Text("What's scheduled that can move your book — central-bank decisions, " +
                "inflation and jobs prints, results season, expiry — plus the news " +
                "bulletin behind it.", color = Muted, fontSize = 12.sp, lineHeight = 17.sp)
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(30, 60, 90).forEach { h ->
                    FilterChip(selected = horizon == h, onClick = { horizon = h },
                        label = { Text("${h}d") })
                }
                Spacer(Modifier.weight(1f))
                FilterChip(selected = highOnly, onClick = { highOnly = !highOnly },
                    label = { Text("High only") })
            }
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = {
                    JobBus.clear("calendar")
                    JobBus.run("calendar", "Reading the Fed calendar, RBI and 18 news feeds…",
                        maxSecs = 240) { Api.calendar(horizon, 7, refresh = true) }
                },
                enabled = !job.running, modifier = Modifier.fillMaxWidth(),
            ) { Text(if (job.running) "Refreshing…" else "⟳ Refresh calendar") }
            job.status?.let {
                Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear)
            }
            data?.optJSONObject("counts")?.let { c ->
                Spacer(Modifier.height(12.dp))
                KpiGrid(listOf(
                    Triple("Upcoming", fmtNum(c.opt("upcoming")), OnBg),
                    Triple("Confirmed dates", fmtNum(c.opt("confirmed")), Bull),
                    Triple("Headlines", fmtNum(c.opt("bulletin")), OnBg),
                    Triple("Window", "${horizon}d", Muted),
                ))
            }
        }

        // The AI layer over the calendar: what all this means for THIS book.
        AiInsightCard(
            jobKey = "ai_brief",
            title = "AI morning brief",
            blurb = "Reads your holdings, the events ahead and the last few days of " +
                "news together, and tells you what actually matters for your positions.",
            cta = "✨ Brief me",
            accent = Bull,
        ) { Api.aiBrief() }

        data?.let { d ->
            val today = d.optString("today")
            // The horizon chip narrows what's already loaded, so it responds
            // instantly; Refresh is what widens the fetched window.
            val cutoff = isoPlusDays(horizon)
            val fetchedTo = d.optJSONObject("window")?.optString("to") ?: cutoff
            val events = arr(d, "events") ?: JSONArray()
            val rows = ArrayList<JSONObject>()
            for (i in 0 until events.length()) {
                val e = events.optJSONObject(i) ?: continue
                val dt = e.optString("date")
                if (dt < today || dt > cutoff) continue            // outside window
                if (highOnly && e.optString("importance") != "HIGH") continue
                rows.add(e)
            }
            SectionCard("Scheduled events", Warn) {
                if (rows.isEmpty()) {
                    StatusBanner("Nothing scheduled in this window. Widen it or turn " +
                        "off \"High only\".", Muted)
                } else {
                    Text("Tap an event to see what it means for your holdings.",
                        color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(10.dp))
                    var lastDate = ""
                    rows.forEach { e ->
                        val dt = e.optString("date")
                        if (dt != lastDate) {
                            lastDate = dt
                            Text(prettyDate(dt, e.optString("weekday")),
                                color = AccentHi, fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                modifier = Modifier.padding(top = 12.dp, bottom = 4.dp))
                        }
                        EventRow(e) { selected = e }
                    }
                    if (cutoff > fetchedTo) {
                        Spacer(Modifier.height(10.dp))
                        StatusBanner("Loaded up to $fetchedTo. Tap Refresh to pull " +
                            "events further ahead.", Muted)
                    }
                    Spacer(Modifier.height(12.dp))
                    Text(d.optString("legend"), color = Muted, fontSize = 10.sp, lineHeight = 15.sp)
                }
            }

            arr(d, "bulletin")?.takeIf { it.length() > 0 }?.let { bl ->
                SectionCard("News bulletin", AccentHi) {
                    Text("Filtered to market-relevant headlines from the last few days, " +
                        "spread across sources so no single feed dominates.",
                        color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(10.dp))
                    for (i in 0 until minOf(bl.length(), 30)) {
                        val n = bl.optJSONObject(i) ?: continue
                        Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                            Text(n.optString("title"), color = OnBg, fontSize = 13.sp,
                                lineHeight = 18.sp)
                            Spacer(Modifier.height(3.dp))
                            Text(n.optString("source") +
                                (n.optString("published").takeIf { it.isNotBlank() }
                                    ?.let { " · " + it.take(22) } ?: ""),
                                color = Muted, fontSize = 10.sp)
                        }
                        if (i < minOf(bl.length(), 30) - 1)
                            Divider(color = BorderCol.copy(alpha = 0.5f))
                    }
                }
            }

            arr(d, "sources")?.let { src ->
                SectionCard("Sources", Muted) {
                    for (i in 0 until src.length())
                        Text("• " + src.optString(i), color = Muted, fontSize = 11.sp,
                            modifier = Modifier.padding(vertical = 2.dp))
                    d.optJSONObject("source_health")?.let { h ->
                        Spacer(Modifier.height(10.dp))
                        SourceHealthPanel(h)
                    }
                }
            }
        }
        Spacer(Modifier.height(24.dp))
    }

    selected?.let { ev -> EventImpactDialog(ev) { selected = null } }
}

/**
 * Which news feeds answered, and which didn't.
 *
 * A feed that quietly returns nothing is indistinguishable from "no news
 * today" unless something says otherwise — and a shrinking evidence pool
 * silently weakens every downstream judgement.
 */
@Composable
private fun SourceHealthPanel(h: JSONObject) {
    var expanded by remember { mutableStateOf(false) }
    val healthy = h.optInt("healthy")
    val total = h.optInt("total")
    val degraded = arr(h, "degraded")
    val allOk = degraded == null || degraded.length() == 0
    Column(Modifier.fillMaxWidth().clickable { expanded = !expanded }) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pill(if (allOk) "$healthy/$total live" else "$healthy/$total live",
                if (allOk) Bull else Warn)
            Spacer(Modifier.width(10.dp))
            Text(if (allOk) "All news feeds responding"
                 else "${degraded!!.length()} feed(s) not responding",
                color = Muted, fontSize = 11.sp, modifier = Modifier.weight(1f))
            Text(if (expanded) "▲" else "▼", color = Muted, fontSize = 10.sp)
        }
        if (!allOk && !expanded) {
            Spacer(Modifier.height(4.dp))
            Text("Tap for detail. Others cover the gap — the pool just shrinks.",
                color = Muted, fontSize = 10.sp)
        }
        if (expanded) {
            Spacer(Modifier.height(8.dp))
            arr(h, "sources")?.let { rows ->
                for (i in 0 until rows.length()) {
                    val r = rows.optJSONObject(i) ?: continue
                    val ok = r.optBoolean("ok")
                    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Text(if (ok) "●" else "●", color = if (ok) Bull else Bear, fontSize = 9.sp)
                        Spacer(Modifier.width(8.dp))
                        Text(r.optString("name"), color = OnBg.copy(alpha = 0.85f),
                            fontSize = 11.sp, modifier = Modifier.weight(1f), maxLines = 1)
                        Text(if (ok) "${r.optInt("items")} · ${r.optInt("ms")}ms"
                             else r.optString("error").take(28),
                            color = Muted, fontSize = 10.sp)
                    }
                }
            }
        }
    }
}

@Composable
private fun EventRow(e: JSONObject, onClick: () -> Unit) {
    val importance = e.optString("importance")
    val col = impactColor(importance)
    val confirmed = e.optString("certainty") == "confirmed"
    Row(
        Modifier.fillMaxWidth().clickable { onClick() }.padding(vertical = 8.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Box(Modifier.width(4.dp).height(38.dp)
            .background(col.copy(alpha = 0.85f), androidx.compose.foundation.shape.RoundedCornerShape(2.dp)))
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(categoryIcon(e.optString("category")) + "  " + e.optString("title"),
                color = OnBg, fontSize = 13.sp, fontWeight = FontWeight.Medium, lineHeight = 18.sp)
            Spacer(Modifier.height(5.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Pill(importance, col)
                Pill(e.optString("region"), Muted)
                // A scraped date is a fact; a pattern-derived one is not. Say which.
                Pill(if (confirmed) "confirmed" else "expected",
                    if (confirmed) Bull else Muted)
            }
        }
        Text("›", color = Muted, fontSize = 18.sp)
    }
}

@Composable
private fun EventImpactDialog(event: JSONObject, onDismiss: () -> Unit) {
    androidx.compose.ui.window.Dialog(
        onDismissRequest = onDismiss,
        properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(color = Bg, modifier = Modifier.fillMaxSize()) {
            Column(Modifier.fillMaxSize()) {
                Row(Modifier.fillMaxWidth().padding(8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = onDismiss) { Text("✕ Close") }
                }
                Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                    SectionCard(event.optString("title"), impactColor(event.optString("importance"))) {
                        Text(prettyDate(event.optString("date"), event.optString("weekday")),
                            color = AccentHi, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(8.dp))
                        Text(event.optString("why"), color = OnBg.copy(alpha = 0.9f),
                            fontSize = 13.sp, lineHeight = 19.sp)
                        Spacer(Modifier.height(10.dp))
                        Text("Date ${event.optString("certainty")} — source: " +
                            event.optString("source"), color = Muted, fontSize = 10.sp)
                    }
                    AiInsightCard(
                        jobKey = "ai_event:" + event.optString("date") + event.optString("title"),
                        title = "What this means for you",
                        blurb = "Maps this event onto your actual holdings — which names " +
                            "react, through what channel, and in which direction.",
                        cta = "✨ Analyse for my portfolio",
                        accent = Bull,
                    ) { Api.aiEventImpact(event) }
                    Spacer(Modifier.height(24.dp))
                }
            }
        }
    }
}
