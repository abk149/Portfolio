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

internal fun arr(o: JSONObject?, key: String): JSONArray? =
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

/**
 * The last portfolio snapshot, kept for the life of the process.
 *
 * HomeScreen is disposed whenever you switch tabs, and it used to re-fetch
 * from the broker on every return — a blank screen and a spinner each time
 * you glanced back at your own book. Now the last snapshot shows instantly
 * and a refresh runs behind it.
 */
private object HomeCache {
    var data by mutableStateOf<JSONObject?>(null)
    var risk by mutableStateOf<JSONObject?>(null)
    var error by mutableStateOf<String?>(null)
    var loading by mutableStateOf(false)
    var loadedAt by mutableStateOf(0L)
}

/** One holding, parsed once, in the shape the list needs. */
private class Holding(
    val symbol: String,
    val name: String?,
    val qty: Double,
    val value: Double,
    val pnl: Double,
    val pnlPct: Double,
    val dayValue: Double,
    val dayPct: Double,
)

private fun parseHoldings(a: JSONArray?): List<Holding> {
    if (a == null) return emptyList()
    val out = ArrayList<Holding>()
    for (i in 0 until a.length()) {
        val o = a.optJSONObject(i) ?: continue
        fun n(k: String) = (o.opt(k) as? Number)?.toDouble() ?: 0.0
        fun str(vararg ks: String) = ks.asSequence().map { o.optString(it) }
            .firstOrNull { it.isNotBlank() && it != "null" }
        val sym = str("tradingsymbol", "trading_symbol", "symbol") ?: continue
        val ltp = n("last_price"); val chg = n("day_change")
        val prev = ltp - chg
        out.add(Holding(
            symbol = sym,
            name = str("company_name", "name")?.takeIf { !it.equals(sym, true) },
            qty = n("quantity"), value = n("current_value"),
            pnl = n("pnl"), pnlPct = n("pnl_pct"),
            dayValue = n("day_change_value"),
            dayPct = if (prev > 0) chg / prev * 100 else 0.0,
        ))
    }
    return out
}

private fun signed(v: Double, pct: Boolean = false): String =
    (if (v >= 0) "+" else "−") +
        (if (pct) "%.2f%%".format(kotlin.math.abs(v)) else "₹" + fmtCompact(kotlin.math.abs(v)))

// ─────────────────────────────────────────────────────────────────────────────
// HOME · Portfolio
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun HomeScreen() {
    val scope = rememberCoroutineScope()

    fun load() {
        if (HomeCache.loading) return
        scope.launch {
            HomeCache.loading = true
            when (val r = Api.portfolio()) {
                is Api.Resp.Ok -> {
                    if (r.body.has("error")) {
                        HomeCache.error = r.body.optString("message", "Not authenticated")
                    } else {
                        HomeCache.data = r.body; HomeCache.error = null
                        HomeCache.loadedAt = System.currentTimeMillis()
                    }
                }
                is Api.Resp.Err -> HomeCache.error = r.message
            }
            Api.portfolioRisk().objOrNull()?.takeIf { !it.has("error") }?.let { HomeCache.risk = it }
            HomeCache.loading = false
        }
    }
    // Show what we have at once; refresh behind it if it's more than a minute old.
    LaunchedEffect(BackendBus.running) {
        if (BackendBus.running &&
            System.currentTimeMillis() - HomeCache.loadedAt > 60_000) load()
    }

    val data = HomeCache.data
    val risk = HomeCache.risk
    ScreenScaffold(title = "Portfolio", loading = HomeCache.loading, onRefresh = ::load) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }

        HomeCache.error?.let {
            if (data == null) {
                Card("Broker not connected", accent = Bear) {
                    Lede("Sign in to your broker to see holdings, P&L and risk.")
                    Spacer(Modifier.height(Space.sm))
                    Note(it, Bear)
                    Spacer(Modifier.height(Space.md))
                    PrimaryAction("Open broker login",
                        onClick = { UiNav.open(UiNav.Screen.Login) })
                }
            } else {
                Box(Modifier.padding(horizontal = Space.edge, vertical = Space.xs)) {
                    Banner("Couldn't refresh — showing the last snapshot. $it", Warn)
                }
            }
        }
        if (data == null && HomeCache.error == null) {
            Card {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp,
                        color = AccentHi)
                    Spacer(Modifier.width(Space.md))
                    Lede("Fetching your holdings from the broker…")
                }
            }
        }

        data?.let { d ->
            val s = d.optJSONObject("summary") ?: JSONObject()
            fun num(k: String) = (s.opt(k) as? Number)?.toDouble() ?: 0.0
            val holdings = remember(d) { parseHoldings(arr(d, "holdings")) }
            val total = num("holdings_value")

            // ── Hero: the number you opened the app for ──
            Card {
                Note("PORTFOLIO VALUE")
                Spacer(Modifier.height(Space.xs))
                Text("₹" + fmtNum(kotlin.math.round(total).toLong()), color = OnBg,
                    fontSize = Type.display, fontWeight = Type.displayWeight)
                Spacer(Modifier.height(Space.xs))
                val pnl = num("holdings_pnl"); val day = num("day_change_value")
                Row(verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(Space.sm)) {
                    Text(signed(pnl) + "  (" + signed(num("holdings_pnl_pct"), true) + ")",
                        color = if (pnl >= 0) Bull else Bear, fontSize = Type.body,
                        fontWeight = FontWeight.SemiBold)
                    Note("all time")
                }
                Spacer(Modifier.height(Space.md))
                MetricGrid(listOf(
                    Triple("Today", signed(day), if (day >= 0) Bull else Bear),
                    Triple("Invested", "₹" + fmtCompact(s.opt("holdings_invested")), OnBg),
                ))
            }

            // ── Holdings: a list you can read without swiping sideways ──
            if (holdings.isNotEmpty()) HoldingsCard(holdings, total)

            arr(d, "positions")?.takeIf { it.length() > 0 }?.let {
                Card("Open positions", subtitle = "Intraday and short-term") { DataTable(it) }
            }

            arr(d, "allocation")?.takeIf { it.length() > 0 }?.let { al ->
                Card("Allocation") {
                    DonutChart(remember(d) { allocationSlices(al) })
                }
            }
        }

        risk?.let { rk -> RiskCard(rk) }

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
        }
    }
}

@Composable
private fun HoldingsCard(holdings: List<Holding>, total: Double) {
    var sort by remember { mutableStateOf("value") }
    var showAll by remember { mutableStateOf(false) }
    val sorted = remember(holdings, sort) {
        when (sort) {
            "pnl" -> holdings.sortedByDescending { it.pnlPct }
            "day" -> holdings.sortedByDescending { it.dayPct }
            else -> holdings.sortedByDescending { it.value }
        }
    }
    val shown = if (showAll) sorted else sorted.take(8)

    Card("Holdings", subtitle = "${holdings.size} stocks · tap one for a deep dive") {
        ChipRow(
            listOf("value" to "Largest", "pnl" to "Best P&L", "day" to "Today"),
            selected = sort, onSelect = { sort = it },
        )
        Spacer(Modifier.height(Space.sm))
        shown.forEachIndexed { i, h ->
            if (i > 0) Divider(color = BorderCol.copy(alpha = 0.5f))
            HoldingRow(h, if (total > 0) h.value / total * 100 else 0.0)
        }
        if (sorted.size > 8) {
            Spacer(Modifier.height(Space.sm))
            SecondaryAction(
                if (showAll) "Show fewer" else "Show all ${sorted.size}",
                onClick = { showAll = !showAll },
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun HoldingRow(h: Holding, weightPct: Double) {
    Row(
        Modifier.fillMaxWidth()
            .clickable { UiNav.open(UiNav.Screen.DeepDive(h.symbol)) }
            .padding(vertical = Space.md),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(h.symbol, color = OnBg, fontSize = Type.body, fontWeight = FontWeight.SemiBold,
                maxLines = 1)
            Text(
                listOfNotNull(
                    h.name,
                    "${fmtNum(h.qty)} sh",
                    "%.1f%% of book".format(weightPct),
                ).joinToString(" · "),
                color = Muted, fontSize = Type.label, maxLines = 1,
                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(Space.sm))
        Column(horizontalAlignment = Alignment.End) {
            Text("₹" + fmtCompact(h.value), color = OnBg, fontSize = Type.body,
                fontWeight = FontWeight.SemiBold)
            Row {
                Text(signed(h.pnlPct, true), color = if (h.pnl >= 0) Bull else Bear,
                    fontSize = Type.label)
                Text("  today " + signed(h.dayPct, true),
                    color = if (h.dayValue >= 0) Bull.copy(alpha = 0.75f) else Bear.copy(alpha = 0.75f),
                    fontSize = Type.label)
            }
        }
    }
}

/** Concentration and laggards, collapsed until you want the detail. */
@Composable
private fun RiskCard(rk: JSONObject) {
    val conc = arr(rk, "concentration")
    val under = arr(rk, "underperformers")
    val nConc = conc?.length() ?: 0
    val nUnder = under?.length() ?: 0
    var openConc by remember { mutableStateOf(false) }
    var openUnder by remember { mutableStateOf(false) }
    Card("Risk check", accent = if (nConc + nUnder > 0) Warn else Bull) {
        if (nConc + nUnder == 0) {
            Lede("No position above the concentration limit and nothing down " +
                "more than 10% — nothing to flag.")
        } else {
            DisclosureRow("Concentrated positions",
                if (nConc == 0) "none" else "$nConc", openConc, { openConc = !openConc }) {
                DataTable(conc, 20)
                Spacer(Modifier.height(Space.sm))
            }
            Divider(color = BorderCol.copy(alpha = 0.5f))
            DisclosureRow("Down more than 10%",
                if (nUnder == 0) "none" else "$nUnder", openUnder, { openUnder = !openUnder }) {
                DataTable(under, 20)
            }
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
    var symInput by remember { mutableStateOf("") }

    LaunchedEffect(Unit) { if (BackendBus.running) macro = Api.macro().objOrNull() }

    LaunchedEffect(BackendBus.running) { if (BackendBus.running) JobBus.restore("quant", "quant") }

    ScreenScaffold(title = "DR-Quant", loading = job.running, onRefresh = null) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }
        SectionCard("Run the funnel", Bull) {
            Lede("Screens the universe, validates fundamentals and technicals, " +
                "and favours names that still have room to run over ones that already moved.")
            Spacer(Modifier.height(Space.md))
            UniversePicker(universe) { universe = it }
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = {
                    JobBus.run("quant",
                        "Running on-device — progress shows in the bar above…") {
                        Api.quantRun(universe)
                    }
                },
                enabled = !job.running, modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Bull),
            ) { Text(if (job.running) "Running…" else "▶ Run funnel") }
            job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }
        }
        job.result?.let { res ->
            val p = res.optJSONObject("portfolio")
            val validated = arr(res, "validated")
            SectionCard("Result", Bull) {
                FreshnessLine("quant")
                KpiGrid(listOf(
                    Triple("Candidates", fmtNum(res.opt("candidates")), OnBg),
                    Triple("Validated", fmtNum(validated?.length() ?: 0), Bull),
                    Triple("Sharpe", fmtNum(p?.opt("sharpe")), AccentHi),
                ))
                arr(res, "rejected")?.takeIf { it.length() > 0 }?.let { rej ->
                    Spacer(Modifier.height(10.dp))
                    val names = (0 until rej.length()).joinToString(", ") { rej.optString(it) }
                    Text("Rejected ${rej.length()}: $names", color = Muted,
                        fontSize = Type.secondary, lineHeight = 18.sp)
                }
            }
            if (validated != null && validated.length() > 0) {
                SectionCard("Validated picks", Bull) {
                    Text("The names that survived the funnel, with why each one passed.",
                        color = Muted, fontSize = Type.secondary)
                    Spacer(Modifier.height(4.dp))
                    for (i in 0 until validated.length()) {
                        validated.optJSONObject(i)?.let { v ->
                            ValidatedCard(v) { UiNav.open(UiNav.Screen.DeepDive(v.optString("symbol"))) }
                        }
                    }
                }
            }
            arr(res, "intraday_alerts")?.takeIf { it.length() > 0 }?.let {
                SectionCard("Intraday alerts", Warn) { DataTable(it) }
            }
        }
        macro?.let { m -> MacroCard(m) }
        SectionCard("Look up one stock", AccentHi) {
            Text("Drill into last-2-quarter results, valuation issues, and a quant " +
                "entry price for any symbol (or a funnel-validated one).",
                color = Muted, fontSize = Type.secondary)
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(symInput, { symInput = it.uppercase() },
                    label = { Text("Symbol (e.g. RELIANCE)") }, singleLine = true,
                    modifier = Modifier.weight(1f))
                Spacer(Modifier.width(8.dp))
                Button(onClick = { if (symInput.isNotBlank()) UiNav.open(UiNav.Screen.DeepDive(symInput.trim())) },
                    enabled = symInput.isNotBlank()) { Text("🔍 Dive") }
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
            Text("Regime", color = Muted, fontSize = Type.secondary)
            Spacer(Modifier.width(10.dp))
            Pill(regime, regimeColor)
            Spacer(Modifier.weight(1f))
            m.optString("as_of").takeIf { it.isNotBlank() }?.let {
                Text(it.replace("T", " "), color = Muted, fontSize = Type.label)
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
                Text("• " + rs.optString(i), color = Muted, fontSize = Type.secondary,
                    lineHeight = 18.sp, modifier = Modifier.padding(vertical = 1.dp))
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
                    fontSize = Type.heading, fontWeight = FontWeight.Bold)
                v.optString("sector").takeIf { it.isNotBlank() && it != "null" }?.let {
                    Text(it, color = Muted, fontSize = Type.secondary)
                }
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(score?.let { "%.0f".format(it) } ?: "—",
                    color = scoreColor, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Text("HEALTH", color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
            }
        }

        v.optString("thesis").takeIf { it.isNotBlank() && it != "null" }?.let {
            Spacer(Modifier.height(10.dp))
            Text(it, color = OnBg.copy(alpha = 0.9f), fontSize = Type.secondary, lineHeight = 18.sp)
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
                        Text(label, color = Muted, fontSize = Type.caps, letterSpacing = 0.5.sp)
                        Spacer(Modifier.height(2.dp))
                        Text(value, color = OnBg, fontSize = Type.body, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }

        arr(v, "key_risks")?.takeIf { it.length() > 0 }?.let { risks ->
            Spacer(Modifier.height(12.dp))
            Text("KEY RISKS", color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
            Spacer(Modifier.height(4.dp))
            for (i in 0 until risks.length()) {
                val r = risks.optString(i)
                if (r.isNotBlank() && r != "null")
                    Text("• $r", color = Warn.copy(alpha = 0.92f), fontSize = Type.secondary,
                        lineHeight = 18.sp, modifier = Modifier.padding(vertical = 1.dp))
            }
        }

        Spacer(Modifier.height(12.dp))
        TextButton(onClick = onDeepDive, contentPadding = PaddingValues(0.dp)) {
            Text("🔍 Deep dive ${v.optString("symbol")}", color = AccentHi, fontSize = Type.secondary)
        }
        // Take the idea on paper without leaving the card.
        GhostInvestRow(v.optString("symbol"), source = "dr-quant")
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
    val scope = rememberCoroutineScope()


    // Bring back the last run rather than showing an empty screen after a
    // restart — these cost minutes and a pile of network calls to rebuild.
    LaunchedEffect(BackendBus.running) { if (BackendBus.running) JobBus.restore("themes", "themes") }

    ScreenScaffold(title = "Macro Ideas", loading = job.running, onRefresh = null) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }
        SectionCard("Generate ideas", AccentHi) {
            Text("Pulls recent macro, news (domestic + global), and Reddit chatter, " +
                "maps them to sectors, and picks stocks from your Universe Map — " +
                "then allocates an amount toward the efficient frontier.",
                color = Muted, fontSize = Type.secondary)
            Spacer(Modifier.height(8.dp))
            Note("LOOK BACK OVER")
            ChipRow(listOf(7 to "7 days", 14 to "14 days", 30 to "30 days"),
                selected = days, onSelect = { days = it })
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
            Card("Market backdrop",
                subtitle = "As of ${r.optString("as_of")} · last ${r.optInt("window_days")} days") {
                fun v(k: String) = macro?.opt(k)?.takeIf { it != JSONObject.NULL }?.let(::fmtNum) ?: "—"
                MetricGrid(listOf(
                    Triple("Mode", macro?.optString("mode")?.takeIf { it.isNotBlank() && it != "null" } ?: "—", AccentHi),
                    Triple("India VIX", v("india_vix"), OnBg),
                    Triple("Nifty PCR", v("nifty_pcr"), OnBg),
                    Triple("USD/INR", v("usdinr"), OnBg),
                ))
                Spacer(Modifier.height(Space.sm))
                Note("Read ${counts?.optInt("news") ?: 0} news items, " +
                    "${counts?.optInt("reddit") ?: 0} corroborated Reddit posts and " +
                    "${counts?.optInt("universe") ?: 0} universe stocks.")
            }
            r.optString("macro_view").takeIf { it.isNotBlank() }?.let { view ->
                SectionCard("Holistic macro view", AccentHi) {
                    Text(view, color = OnBg, fontSize = Type.body, lineHeight = 20.sp)
                }
            }
            val picks = r.optJSONArray("picks")
            if (picks == null || picks.length() == 0) {
                SectionCard("No picks", Muted) { Text("No high-conviction picks from recent " +
                    "signals. Build/refresh the Universe Map for more coverage.",
                    color = Muted, fontSize = Type.secondary) }
            } else {
                SectionCard("Top picks (${picks.length()})", Bull) {
                    FreshnessLine("themes")
                    Text("All factors weighed together. Tap a stock for the full deep dive.",
                        color = Muted, fontSize = Type.secondary)
                }
                for (i in 0 until picks.length()) {
                    val p = picks.optJSONObject(i) ?: continue
                    val sym = p.optString("symbol")
                    val conv = p.optString("conviction", "MEDIUM")
                    val col = when (conv) { "HIGH" -> Bull; "LOW" -> Muted; else -> AccentHi }
                    val e = p.optJSONObject("entry")
                    SectionCard(sym, col, trailing = { Pill(conv, col) }) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(p.optString("sector", ""), color = Muted, fontSize = Type.secondary,
                                modifier = Modifier.weight(1f))
                            Text("🔍 deep dive", color = AccentHi, fontSize = Type.secondary,
                                modifier = Modifier.clickable { UiNav.open(UiNav.Screen.DeepDive(sym)) })
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
                            fontSize = Type.body, lineHeight = 20.sp)
                        Spacer(Modifier.height(6.dp))
                        GhostInvestRow(sym, source = "ideas")
                    }
                }

                // Allocate the idea stocks toward the efficient frontier.
                val tickers = buildList {
                    r.optJSONArray("tickers")?.let { for (k in 0 until it.length()) add(it.optString(k)) }
                }
                SectionCard("Allocate toward the frontier", Bull) {
                    Text("Deploy an amount across these ${tickers.size} idea stocks to best " +
                        "improve your portfolio's risk/return (₹ + whole shares).",
                        color = Muted, fontSize = Type.secondary)
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
                    color = Muted, fontSize = Type.label, lineHeight = 15.sp)
            }

            if (job.running) {
                Spacer(Modifier.height(12.dp))
                val pct = job.progress
                if (pct != null) {
                    LinearProgressIndicator(progress = pct.coerceIn(0f, 1f),
                        modifier = Modifier.fillMaxWidth(), color = AccentHi, trackColor = Panel2)
                    Spacer(Modifier.height(6.dp))
                    Text("%.0f%% · %s".format(pct * 100, job.detail ?: "working…"),
                        color = Muted, fontSize = Type.secondary)
                } else {
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth(),
                        color = AccentHi, trackColor = Panel2)
                    Spacer(Modifier.height(6.dp))
                    Text(job.detail ?: "Scanning the universe for price data…",
                        color = Muted, fontSize = Type.secondary)
                }
                Spacer(Modifier.height(6.dp))
                Text("Safe to leave this screen — the crawl keeps running and saves " +
                    "its progress every 25 stocks.", color = Muted, fontSize = Type.label)
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
                        color = Muted, fontSize = Type.label)
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
            val pts = remember(stocks) { universePoints(stocks) }
            SectionCard("Map · technical vs fundamental", AccentHi) {
                Text("Where every stock sits on balance-sheet quality (→) against price " +
                    "action (↑). Both lines split at a score of 50, so the corner a " +
                    "stock lands in is what matters.",
                    color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
                Spacer(Modifier.height(12.dp))
                QuadrantScatterChart(pts, xLabel = "Fundamental score",
                    yLabel = "Technical score", highlight = quadrant)
                Spacer(Modifier.height(12.dp))
                Text("Tap a quadrant to filter the table below.",
                    color = Muted, fontSize = Type.secondary)
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
                        "had nothing).", color = Muted, fontSize = Type.label, lineHeight = 15.sp)
                }
            }
            val shown = filterByQuadrant(stocks, quadrant)
            SectionCard(quadrant?.let { "Universe · ${it.label}" } ?: "Universe", AccentHi) {
                if (quadrant != null) {
                    Text("${shown.length()} stocks · ${quadrant!!.blurb}",
                        color = Muted, fontSize = Type.secondary)
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
            Text(q.label, color = OnBg, fontSize = Type.secondary, fontWeight = FontWeight.Medium)
            Text(q.blurb, color = Muted, fontSize = Type.label)
        }
        Text("$count", color = q.tint, fontSize = Type.body, fontWeight = FontWeight.Bold)
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
    // Ghost has its own bottom tab now; Analysis is the tools for your own book.
    val sub = UiNav.analysisTab
    val tabs = listOf("Performance", "Optimize", "Screener", "Intraday", "KB")
    Column(Modifier.fillMaxSize()) {
        ScrollableTabRow(
            selectedTabIndex = tabs.indexOf(sub),
            containerColor = Bg, edgePadding = 12.dp,
        ) {
            tabs.forEach { t ->
                Tab(selected = sub == t, onClick = { UiNav.analysisTab = t },
                    text = { Text(t, fontSize = Type.body) })
            }
        }
        Box(Modifier.weight(1f)) {
            when (sub) {
                "Optimize" -> OptimizeTab()
                "Screener" -> ScreenerTab()
                "Intraday" -> IntradayTab()
                "KB" -> KbTab()
                else -> PerformanceTab()
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
            Text("Min score: $minScore", color = Muted, fontSize = Type.secondary)
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
            Text("Lookback: $days days", color = Muted, fontSize = Type.secondary)
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
                        Text("• ${mistakes.optString(i)}", color = OnBg, fontSize = Type.secondary,
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
                "winners and losers.", color = Muted, fontSize = Type.secondary)
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
                        color = Muted, fontSize = Type.label, lineHeight = 15.sp)
                }
            }
            val curve = arr(r, "equity_curve")
            val ec = remember(curve) { equitySeries(curve) }
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
                        color = Muted, fontSize = Type.secondary)
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
            color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
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
        val (pSeries, iSeries) = remember(b) { rebasedSeries(arr(b, "series")) }
        if (pSeries.size >= 2) {
            Text("₹100 invested a year ago", color = Muted, fontSize = Type.secondary)
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
            color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
    }

    // Rolling trailing-1Y return — "moving return", the shape of your form.
    val (rollLabels, rollP, rollI) = remember(b) { pairedPct(arr(b ?: JSONObject(), "rolling"), "date") }
    if (rollP.size >= 2) {
        SectionCard("Rolling 1-year return", Bull) {
            Text("At every point, what you made over the previous 12 months versus " +
                "the index. Flat stretches above the index line are consistency; " +
                "spikes are single bets landing.", color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
            Spacer(Modifier.height(10.dp))
            LineChart(listOf(
                ChartSeries(rollP, "You (1Y trailing)", AccentHi),
                ChartSeries(rollI, "Index (1Y trailing)", Warn, dashed = true),
            ))
        }
    }

    val (mLabels, mP, mI) = remember(b) { pairedPct(arr(b ?: JSONObject(), "monthly"), "month") }
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
            Text(it, color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
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
    // Restore the last allocation so a restart doesn't blank the tab.
    LaunchedEffect(BackendBus.running) {
        if (BackendBus.running) JobBus.restore("deploy_cash", "deploy_cash")
    }
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
                "to best improve your portfolio's risk/return.", color = Muted, fontSize = Type.secondary)
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
                FreshnessLine("deploy_cash")
                KpiGrid(listOf(
                    Triple("Sharpe now", fmtNum(before?.opt("sharpe")), OnBg),
                    Triple("Sharpe after", fmtNum(after?.opt("sharpe")), Bull),
                ))
                Spacer(Modifier.height(10.dp))
                Text("Buy", color = Muted, fontSize = Type.secondary)
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
            Text("Max weight per name: $maxW%", color = Muted, fontSize = Type.secondary)
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
    // Ask the engine which code it is running, so a stale install is obvious.
    LaunchedEffect(BackendBus.state.value) {
        if (BackendBus.running && BackendBus.backendBuild.value == null) {
            Api.status().objOrNull()?.optJSONObject("build")?.let { b ->
                BackendBus.backendBuild.value =
                    "${b.optString("id")} · ${b.optString("built_at")}"
            }
        }
    }
    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("System Terminal", color = OnBg, fontSize = Type.heading, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            val (lbl, col2) = when (BackendBus.state.value) {
                BackendBus.State.RUNNING -> "● RUNNING" to Bull
                BackendBus.State.STARTING -> "● STARTING" to Warn
                BackendBus.State.ERROR -> "● ERROR" to Bear
                else -> "● STOPPED" to Muted
            }
            Pill(lbl, col2)
        }
        BackendBus.backendBuild.value?.let {
            Spacer(Modifier.height(4.dp))
            Text("engine build $it", color = Muted, fontSize = Type.label,
                fontFamily = FontFamily.Monospace)
        }
        Spacer(Modifier.height(8.dp))
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth()
                .background(Color(0xFF010409)).padding(8.dp),
        ) {
            items(BackendBus.logs.size) { i ->
                Text(BackendBus.logs[i], color = AccentHi, fontFamily = FontFamily.Monospace, fontSize = Type.secondary)
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
    val msgs = ChatBus.messages
    val listState = rememberLazyListState()
    LaunchedEffect(msgs.size) { if (msgs.isNotEmpty()) listState.animateScrollToItem(msgs.size - 1) }

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("AI Assistant", color = OnBg, fontSize = Type.heading, fontWeight = FontWeight.SemiBold)
                Text("Knows your portfolio, the latest DR-Quant run, the Universe " +
                    "Map, your benchmark stats and the calendar.",
                    color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
            }
            if (msgs.isNotEmpty()) TextButton(onClick = { ChatBus.clear() }) {
                Text("Clear", fontSize = Type.secondary)
            }
        }
        Spacer(Modifier.height(8.dp))
        if (!BackendBus.running) StatusBanner("Start the engine first (⋮ → System terminal ▶).", Warn)
        LazyColumn(state = listState, modifier = Modifier.weight(1f).fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(msgs.size) { i ->
                val m = msgs[i]
                Row(Modifier.fillMaxWidth(),
                    horizontalArrangement = if (m.fromUser) Arrangement.End else Arrangement.Start) {
                    Box(
                        Modifier.widthIn(max = 320.dp)
                            .background(if (m.fromUser) Accent else Panel2,
                                androidx.compose.foundation.shape.RoundedCornerShape(12.dp))
                            .padding(10.dp)
                    ) {
                        if (m.fromUser) Text(m.text, color = OnBg, fontSize = Type.body)
                        else MarkdownText(m.text)
                    }
                }
            }
        }
        if (ChatBus.busy) Row(Modifier.padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp, color = AccentHi)
            Spacer(Modifier.width(8.dp))
            Text("Thinking — you can leave this screen, the answer will be here.",
                color = Muted, fontSize = Type.secondary)
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(ChatBus.draft, { ChatBus.draft = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Ask about your data…") }, maxLines = 3)
            Spacer(Modifier.width(8.dp))
            Button(onClick = { ChatBus.send(ChatBus.draft) },
                enabled = !ChatBus.busy && ChatBus.draft.isNotBlank() && BackendBus.running) {
                Text("Send")
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// DEEP DIVE — last-2-quarter results, valuation issues, quant entry price
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun DeepDiveScreen(symbol: String) {
    // Keyed per symbol so a dive survives leaving the screen; coming back to
    // the same symbol shows the finished result rather than re-running it.
    val job = JobBus.state("deepdive:$symbol")
    LaunchedEffect(symbol) {
        if (job.result == null && !job.running) {
            JobBus.run("deepdive:$symbol", "Analysing…", maxSecs = 240) { Api.deepDive(symbol) }
        }
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (job.running) {
            SectionCard("Working", AccentHi) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = AccentHi)
                    Spacer(Modifier.width(10.dp))
                    Text("Pulling results PDFs, fundamentals & the price model…",
                        color = Muted, fontSize = Type.secondary)
                }
                Spacer(Modifier.height(8.dp))
                Text("This keeps running if you navigate away or minimise the app.",
                    color = Muted, fontSize = Type.label)
            }
        }
        job.status?.takeIf { !job.running }?.let {
            SectionCard("Couldn't complete", Bear) { StatusBanner(it, Bear) }
        }
        job.result?.let { r ->
            val f = r.optJSONObject("fundamentals") ?: JSONObject()
            val e = r.optJSONObject("entry") ?: JSONObject()
            val a = r.optJSONObject("analysis") ?: JSONObject()
            val prov = r.optJSONObject("fundamentals_provenance")

            SectionCard("Valuation & quality", AccentHi) {
                KpiGrid(listOf(
                    Triple("P/E", fundVal(f, "pe"), OnBg),
                    Triple("ROE", fundVal(f, "roe_pct", "%"), OnBg),
                    Triple("D/E", fundVal(f, "debt_to_equity"), OnBg),
                    Triple("Sales gr.", fundVal(f, "sales_growth_pct", "%"),
                        signColor(f.opt("sales_growth_pct"))),
                    Triple("Profit gr.", fundVal(f, "profit_growth_pct", "%"),
                        signColor(f.opt("profit_growth_pct"))),
                    Triple("Mkt cap", fundVal(f, "market_cap_cr", " cr"), OnBg),
                ))
                // Name the gaps. A grid of dashes is indistinguishable from a
                // broken screen; saying which sources answered is not.
                arr(r, "fundamentals_missing")?.takeIf { it.length() > 0 }?.let { miss ->
                    Spacer(Modifier.height(10.dp))
                    val names = (0 until miss.length()).joinToString(", ") {
                        prettyMetric(miss.optString(it))
                    }
                    Text("Not available from any source right now: $names. " +
                        "These come from public financial sites, which sometimes " +
                        "block or rate-limit us; the Universe Map's stored values " +
                        "are used as a fallback where it has them.",
                        color = Muted, fontSize = Type.label, lineHeight = 15.sp)
                }
                prov?.let { pv ->
                    val stored = pv.keys().asSequence()
                        .filter { pv.optString(it).contains("universe") }.toList()
                    if (stored.isNotEmpty()) {
                        Spacer(Modifier.height(6.dp))
                        Text("From the Universe Map (stored): " +
                            stored.joinToString(", ") { prettyMetric(it) },
                            color = Muted, fontSize = Type.label)
                    }
                }
            }

            SectionCard("Take it on paper", Bull) {
                Text("Book this at the live price and track it in the ghost " +
                    "portfolio — the system will then review it for exits like a " +
                    "real position.", color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
                Spacer(Modifier.height(8.dp))
                GhostInvestRow(symbol, source = "deep-dive")
            }

            SeasonalityCard(symbol)

            SectionCard("Quant entry", Bull) {
                StatusBanner("CMP ₹${fmtNum(e.opt("current"))}  ·  50-DMA ₹${fmtNum(e.opt("dma50"))}  ·  " +
                    "RSI ${fmtNum(e.opt("rsi"))}\nSuggested entry ₹${fmtNum(e.opt("suggested_entry"))} " +
                    "(zone ₹${fmtNum(e.opt("entry_low"))}–${fmtNum(e.opt("entry_high"))}, " +
                    "${fmtNum(e.opt("discount_to_cmp_pct"))}% below CMP)\n${e.optString("note", "")}", Bull)
            }

            SectionCard("Analysis", AccentHi) {
                a.optString("verdict").takeIf { it.isNotBlank() }?.let {
                    Pill(it, AccentHi); Spacer(Modifier.height(8.dp))
                }
                deepText("Financial health", a.optString("financial_health"))
                deepText("Last 2 quarters", a.optString("quarter_trend"))
                deepText("Valuation", a.optString("valuation"))
                deepText("Entry view", a.optString("entry_view"))
                deepList("Issues", a.optJSONArray("issues"), Warn)
                deepList("Red flags", a.optJSONArray("red_flags"), Bear)
                a.optString("error").takeIf { it.isNotBlank() }?.let {
                    Spacer(Modifier.height(8.dp)); StatusBanner(it, Bear)
                }
                a.optString("raw").takeIf { it.isNotBlank() }?.let {
                    Spacer(Modifier.height(8.dp)); Text(it, color = Muted, fontSize = Type.secondary)
                }
            }

            arr(r, "sources")?.takeIf { it.length() > 0 }?.let { srcs ->
                SectionCard("Sources", Muted) {
                    for (i in 0 until srcs.length())
                        Text("• ${srcs.optJSONObject(i)?.optString("title")}",
                            color = Muted, fontSize = Type.secondary, maxLines = 2, lineHeight = 18.sp,
                            modifier = Modifier.padding(vertical = 2.dp))
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

/**
 * Month-by-month history, for "is this a cyclical stock I can time?".
 *
 * Deliberately leads with how much evidence there is. Twelve averages drawn
 * from five years will always show some months looking strong — that is what
 * randomness looks like — so the sample size, the win rate and the caveat are
 * given the same prominence as the chart itself.
 */
@Composable
private fun SeasonalityCard(symbol: String) {
    var years by remember { mutableStateOf(5) }
    val job = JobBus.state("seasonality:$symbol:$years")
    LaunchedEffect(symbol, years) {
        if (BackendBus.running && job.result == null && !job.running) {
            JobBus.runSync("seasonality:$symbol:$years", "Reading $years years of history…") {
                Api.seasonality(symbol, years)
            }
        }
    }
    SectionCard("Seasonality · is it cyclical?", Warn) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(3, 5, 10).forEach { y ->
                FilterChip(selected = years == y, onClick = { years = y },
                    label = { Text("${y}y") })
            }
        }
        job.status?.let { Spacer(Modifier.height(8.dp)); StatusBanner(it, if (job.running) Warn else Bear) }
        job.result?.let { r ->
            r.optString("error").takeIf { it.isNotBlank() }?.let {
                StatusBanner(it, Warn); return@SectionCard
            }
            val months = arr(r, "by_month")
            val labels = ArrayList<String>(); val avgs = ArrayList<Float>()
            val wins = ArrayList<Float?>()
            if (months != null) for (i in 0 until months.length()) {
                val m = months.optJSONObject(i) ?: continue
                labels.add(m.optString("label"))
                avgs.add((m.opt("avg") as? Number)?.toFloat() ?: Float.NaN)
                wins.add((m.opt("win_rate") as? Number)?.toFloat())
            }
            Spacer(Modifier.height(12.dp))
            SeasonalityChart(labels, avgs, wins)

            Spacer(Modifier.height(12.dp))
            val verdict = r.optString("verdict")
            StatusBanner(r.optString("reading"),
                when (verdict) {
                    "possible seasonality" -> Bull
                    "not enough history" -> Muted
                    else -> Warn
                })

            Spacer(Modifier.height(12.dp))
            val best = r.optJSONObject("best_month")
            val worst = r.optJSONObject("worst_month")
            KpiGrid(listOf(
                Triple("Strongest month",
                    best?.let { "${it.optString("label")} ${"%+.1f%%".format(
                        (it.opt("avg") as? Number)?.toDouble() ?: 0.0)}" } ?: "—", Bull),
                Triple("Weakest month",
                    worst?.let { "${it.optString("label")} ${"%+.1f%%".format(
                        (worst.opt("avg") as? Number)?.toDouble() ?: 0.0)}" } ?: "—", Bear),
                Triple("Years covered", fmtNum(r.opt("years_covered")), OnBg),
                Triple("Samples per month", fmtNum(r.opt("observations_per_month")), Muted),
            ))
            best?.let {
                Spacer(Modifier.height(8.dp))
                Text("${it.optString("label")}: positive in ${fmtNum(it.opt("win_rate"))}% " +
                    "of years, t-stat ${fmtNum(it.opt("t_stat"))} " +
                    "(above ~2 is where a pattern starts to look real).",
                    color = Muted, fontSize = Type.label, lineHeight = 15.sp)
            }
            Spacer(Modifier.height(10.dp))
            Text(r.optString("caveat"), color = Muted, fontSize = Type.label, lineHeight = 15.sp)
        }
    }
}

/** Metric value, or an explicit dash when no source supplied it. */
private fun fundVal(f: JSONObject, key: String, suffix: String = ""): String {
    val v = f.opt(key)
    if (v == null || v == JSONObject.NULL) return "—"
    return (if (key == "market_cap_cr") fmtCompact(v) else fmtNum(v)) + suffix
}

private fun signColor(v: Any?): Color {
    val d = (v as? Number)?.toDouble() ?: return Muted
    return if (d >= 0) Bull else Bear
}

private fun prettyMetric(key: String): String = when (key) {
    "pe" -> "P/E"
    "roe_pct" -> "ROE"
    "roce_pct" -> "ROCE"
    "debt_to_equity" -> "D/E"
    "sales_growth_pct" -> "sales growth"
    "profit_growth_pct" -> "profit growth"
    "market_cap_cr" -> "market cap"
    else -> key.replace("_", " ")
}

@Composable
private fun deepText(label: String, value: String?) {
    if (value.isNullOrBlank()) return
    Spacer(Modifier.height(8.dp))
    Text(label, color = Muted, fontSize = Type.secondary)
    Text(value, color = OnBg, fontSize = Type.body, lineHeight = 20.sp)
}

@Composable
private fun deepList(label: String, arr: org.json.JSONArray?, color: Color) {
    if (arr == null || arr.length() == 0) return
    Spacer(Modifier.height(8.dp))
    Text(label, color = color, fontSize = Type.secondary, fontWeight = FontWeight.SemiBold)
    for (i in 0 until arr.length())
        Text("• ${arr.optString(i)}", color = OnBg.copy(alpha = 0.9f), fontSize = Type.secondary,
            lineHeight = 18.sp, modifier = Modifier.padding(vertical = 1.dp))
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
    // Inside a hub (Discover) the tab row already names the screen, so a second
    // big title would only push the content down.
    val inHub = LocalInHub.current
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!inHub || loading || onRefresh != null) {
            Row(Modifier.fillMaxWidth().padding(start = Space.edge, top = if (inHub) 0.dp else Space.md, end = Space.md),
                verticalAlignment = Alignment.CenterVertically) {
                if (!inHub) {
                    Text(title, color = OnBg, fontSize = Type.title, fontWeight = FontWeight.Bold,
                        modifier = Modifier.weight(1f))
                } else Spacer(Modifier.weight(1f))
                if (loading) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = AccentHi)
                else if (onRefresh != null) TextButton(onClick = onRefresh) {
                    Text("Refresh", fontSize = Type.secondary)
                }
            }
        }
        content()
        Spacer(Modifier.height(Space.xl))
    }
}

@Composable
fun ColumnScope.BackendOfflineHint() {
    val starting = BackendBus.state.value == BackendBus.State.STARTING
    Card("The engine is off", accent = Warn) {
        Lede("Everything in this app runs on an analysis engine inside the phone. " +
            "It isn't running yet.")
        Spacer(Modifier.height(Space.md))
        PrimaryAction(if (starting) "Starting…" else "Start the engine",
            onClick = { BackendBus.onStart() }, busy = starting, color = Bull)
        Spacer(Modifier.height(Space.sm))
        Note("First start takes a little longer. Progress: ⋮ → System terminal.")
    }
}

@Composable
fun UniversePicker(selected: String, onSelect: (String) -> Unit) {
    ChipRow(
        UNIVERSES.map { it to UNIVERSE_LABELS.getOrDefault(it, it) },
        selected = selected, onSelect = onSelect,
        modifier = Modifier.horizontalScroll(rememberScrollState()),
    )
}

private val UNIVERSE_LABELS = mapOf(
    "nifty50" to "NIFTY 50", "nifty100" to "NIFTY 100", "all_nse" to "All NSE")

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
        Text(blurb, color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
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
                        color = Muted, fontSize = Type.label)
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

    // Seed from the backend's cache so switching tabs doesn't refetch ~20 feeds.
    LaunchedEffect(Unit) {
        if (BackendBus.running && job.result == null && !job.running) {
            val cached = Api.calendarCached().objOrNull()?.optJSONObject("data")
            if (cached != null) JobBus.seed("calendar", cached)
            else JobBus.run("calendar", "Reading the Fed calendar, RBI and 18 news feeds…",
                maxSecs = 240) { Api.calendar(horizon, 7) }
        }
    }

    val refresh = {
        JobBus.clear("calendar")
        JobBus.run("calendar", "Reading the Fed calendar, RBI and 18 news feeds…",
            maxSecs = 240) { Api.calendar(horizon, 7, refresh = true) }
    }
    val view = UiNav.calendarView

    ScreenScaffold(title = "Calendar", loading = job.running, onRefresh = refresh) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }

        val data = job.result
        Column(Modifier.padding(horizontal = Space.edge)) {
            Lede("Central-bank decisions, inflation and jobs prints, results " +
                "season, expiry — and the headlines behind them.")
            Spacer(Modifier.height(Space.md))
            Row(verticalAlignment = Alignment.CenterVertically) {
                ChipRow(listOf(30 to "30 days", 60 to "60 days", 90 to "90 days"),
                    selected = horizon, onSelect = { horizon = it })
                Spacer(Modifier.weight(1f))
            }
            job.status?.let {
                Spacer(Modifier.height(Space.sm)); Banner(it, if (job.running) Warn else Bear)
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

        if (data == null && !job.running) {
            EmptyState("📅", "No calendar loaded",
                "Pull the schedule of market-moving events and today's headlines.",
                "Load calendar", refresh)
        }

        data?.let { d ->
            val today = d.optString("today")
            // The horizon chip narrows what's already loaded, so it responds
            // instantly; Refresh is what widens the fetched window.
            val cutoff = isoPlusDays(horizon)
            val fetchedTo = d.optJSONObject("window")?.optString("to") ?: cutoff
            val rows = remember(d, horizon, highOnly) {
                val events = arr(d, "events") ?: JSONArray()
                val out = ArrayList<JSONObject>()
                for (i in 0 until events.length()) {
                    val e = events.optJSONObject(i) ?: continue
                    val dt = e.optString("date")
                    if (dt < today || dt > cutoff) continue            // outside window
                    if (highOnly && e.optString("importance") != "HIGH") continue
                    out.add(e)
                }
                out
            }
            val nNews = minOf(arr(d, "bulletin")?.length() ?: 0, 30)

            Row(Modifier.padding(horizontal = Space.edge, vertical = Space.sm),
                verticalAlignment = Alignment.CenterVertically) {
                ChipRow(listOf("events" to "Events · ${rows.size}", "news" to "News · $nNews",
                    "sources" to "Sources"), selected = view, onSelect = { UiNav.calendarView = it },
                    modifier = Modifier.weight(1f).horizontalScroll(rememberScrollState()))
            }
            if (view == "events") {
                Row(Modifier.padding(horizontal = Space.edge),
                    verticalAlignment = Alignment.CenterVertically) {
                    Text("High-impact only", color = OnBg, fontSize = Type.body,
                        modifier = Modifier.weight(1f))
                    Switch(checked = highOnly, onCheckedChange = { highOnly = it })
                }
            }

            when (view) {
                "events" -> {
                SectionCard("Scheduled events", Warn) {
                    if (rows.isEmpty()) {
                        StatusBanner("Nothing scheduled in this window. Widen it or turn " +
                            "off \"High-impact only\".", Muted)
                    } else {
                        Text("Tap an event to see what it means for your holdings.",
                            color = Muted, fontSize = Type.secondary)
                        Spacer(Modifier.height(10.dp))
                        var lastDate = ""
                        rows.forEach { e ->
                            val dt = e.optString("date")
                            if (dt != lastDate) {
                                lastDate = dt
                                Text(prettyDate(dt, e.optString("weekday")),
                                    color = AccentHi, fontSize = Type.secondary,
                                    fontWeight = FontWeight.Bold,
                                    modifier = Modifier.padding(top = 12.dp, bottom = 4.dp))
                            }
                            EventRow(e) { UiNav.open(UiNav.Screen.EventImpact(e)) }
                        }
                        if (cutoff > fetchedTo) {
                            Spacer(Modifier.height(10.dp))
                            StatusBanner("Loaded up to $fetchedTo. Tap Refresh (top right) to " +
                                "pull events further ahead.", Muted)
                        }
                        Spacer(Modifier.height(12.dp))
                        Text(d.optString("legend"), color = Muted, fontSize = Type.label, lineHeight = 15.sp)
                    }
                }
                }
                "news" -> {
                arr(d, "bulletin")?.takeIf { it.length() > 0 }?.let { bl ->
                    SectionCard("News bulletin", AccentHi) {
                        Text("Filtered to market-relevant headlines from the last few days, " +
                            "spread across sources so no single feed dominates.",
                            color = Muted, fontSize = Type.secondary)
                        Spacer(Modifier.height(10.dp))
                        for (i in 0 until minOf(bl.length(), 30)) {
                            val n = bl.optJSONObject(i) ?: continue
                            Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                                Text(n.optString("title"), color = OnBg, fontSize = Type.body,
                                    lineHeight = 20.sp)
                                Spacer(Modifier.height(3.dp))
                                Text(n.optString("source") +
                                    (n.optString("published").takeIf { it.isNotBlank() }
                                        ?.let { " · " + it.take(22) } ?: ""),
                                    color = Muted, fontSize = Type.label)
                            }
                            if (i < minOf(bl.length(), 30) - 1)
                                Divider(color = BorderCol.copy(alpha = 0.5f))
                        }
                    }
                }
                }
                else -> {
                arr(d, "sources")?.let { src ->
                    SectionCard("Sources", Muted) {
                        for (i in 0 until src.length())
                            Text("• " + src.optString(i), color = Muted, fontSize = Type.secondary,
                                modifier = Modifier.padding(vertical = 2.dp))
                        d.optJSONObject("source_health")?.let { h ->
                            Spacer(Modifier.height(10.dp))
                            SourceHealthPanel(h)
                        }
                    }
                }
                }
            }
        }
    }

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
                color = Muted, fontSize = Type.secondary, modifier = Modifier.weight(1f))
            Text(if (expanded) "▲" else "▼", color = Muted, fontSize = Type.label)
        }
        if (!allOk && !expanded) {
            Spacer(Modifier.height(4.dp))
            Text("Tap for detail. Others cover the gap — the pool just shrinks.",
                color = Muted, fontSize = Type.label)
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
                            fontSize = Type.secondary, modifier = Modifier.weight(1f), maxLines = 1)
                        Text(if (ok) "${r.optInt("items")} · ${r.optInt("ms")}ms"
                             else r.optString("error").take(28),
                            color = Muted, fontSize = Type.label)
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
                color = OnBg, fontSize = Type.body, fontWeight = FontWeight.Medium, lineHeight = 20.sp)
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
fun EventImpactScreen(event: JSONObject) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        SectionCard(event.optString("title"), impactColor(event.optString("importance"))) {
            Text(prettyDate(event.optString("date"), event.optString("weekday")),
                color = AccentHi, fontSize = Type.body, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Text(event.optString("why"), color = OnBg.copy(alpha = 0.9f),
                fontSize = Type.body, lineHeight = 20.sp)
            Spacer(Modifier.height(10.dp))
            Text("Date ${event.optString("certainty")} — source: " +
                event.optString("source"), color = Muted, fontSize = Type.label)
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
