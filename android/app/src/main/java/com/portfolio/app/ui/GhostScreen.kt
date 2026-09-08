package com.portfolio.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.portfolio.app.net.Api
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/**
 * Process-wide ghost state, so a buy made from the Ideas tab is immediately
 * visible on the Ghost tab without either screen owning the data.
 */
object GhostBus {
    var snapshot by mutableStateOf<JSONObject?>(null)
        private set
    /** Pending engine recommendations waiting to be taken or dismissed. */
    var recommendations by mutableStateOf<JSONObject?>(null)
        private set
    var lastMessage by mutableStateOf<String?>(null)
    var busyId by mutableStateOf<String?>(null)
        private set

    private val scope = kotlinx.coroutines.CoroutineScope(
        kotlinx.coroutines.SupervisorJob() + kotlinx.coroutines.Dispatchers.Main.immediate)

    fun refresh() {
        scope.launch {
            Api.ghost().objOrNull()?.let { snapshot = it }
            Api.recommendations().objOrNull()?.let { recommendations = it }
        }
    }

    /** Buy a recommendation into the ghost book, keeping its provenance. */
    fun take(id: String, amount: Double?) {
        if (busyId != null) return
        busyId = id
        scope.launch {
            lastMessage = when (val r = Api.recommendationTake(id, amount)) {
                is Api.Resp.Err -> "Couldn't invest: ${r.message}"
                is Api.Resp.Ok -> {
                    val err = r.body.optString("error")
                    if (err.isNotBlank()) err else {
                        val p = r.body.optJSONObject("position")
                        "Bought ${p?.optInt("qty")} ${p?.optString("symbol")} " +
                            "at ₹${p?.optDouble("entry_price")}"
                    }
                }
            }
            busyId = null
            refresh()
        }
    }

    fun dismiss(id: String) {
        scope.launch { Api.recommendationDismiss(id); refresh() }
    }

    fun buy(symbol: String, amount: Double, source: String, onDone: (String) -> Unit = {}) {
        scope.launch {
            val msg = when (val r = Api.ghostBuy(symbol, amount, source)) {
                is Api.Resp.Err -> "Couldn't invest: ${r.message}"
                is Api.Resp.Ok -> {
                    val err = r.body.optString("error")
                    if (err.isNotBlank()) err else {
                        val p = r.body.optJSONObject("position")
                        refresh()
                        "Bought ${p?.optInt("qty")} ${symbol} at ₹${p?.optDouble("entry_price")}"
                    }
                }
            }
            lastMessage = msg
            onDone(msg)
        }
    }

    fun sell(id: String) {
        scope.launch { Api.ghostSell(id); refresh() }
    }
}

/**
 * "Invest this idea on paper" — an inline control, not a popup.
 *
 * Dropped straight into any card that recommends a stock, so the flow from
 * "the system suggests X" to "I'm tracking X" is one tap and an amount.
 */
@Composable
fun GhostInvestRow(symbol: String, source: String) {
    var open by remember { mutableStateOf(false) }
    var amount by remember { mutableStateOf("25000") }
    var msg by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    Column(Modifier.fillMaxWidth()) {
        if (!open) {
            TextButton(onClick = { open = true }, contentPadding = PaddingValues(0.dp)) {
                Text("👻 Invest on paper", color = AccentHi, fontSize = 12.sp)
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = amount,
                    onValueChange = { amount = it.filter { c -> c.isDigit() } },
                    label = { Text("₹ amount") }, singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = {
                        val amt = amount.toDoubleOrNull() ?: 0.0
                        if (amt > 0) {
                            busy = true
                            GhostBus.buy(symbol, amt, source) { m -> msg = m; busy = false }
                        }
                    },
                    enabled = !busy && BackendBus.running && amount.isNotBlank(),
                ) { Text(if (busy) "…" else "Invest") }
                Spacer(Modifier.width(4.dp))
                TextButton(onClick = { open = false; msg = null }) { Text("✕") }
            }
            Text("Books at the live price right now — you don't get to pick the fill.",
                color = Muted, fontSize = 10.sp)
        }
        msg?.let {
            Spacer(Modifier.height(6.dp))
            StatusBanner(it, if (it.startsWith("Bought")) Bull else Bear)
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// GHOST PORTFOLIO
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun GhostScreen() {
    val snap = GhostBus.snapshot
    val curveJob = JobBus.state("ghost_curve")
    val reviewJob = JobBus.state("ghost_review")
    var symInput by remember { mutableStateOf("") }
    var amtInput by remember { mutableStateOf("25000") }
    var confirmReset by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) { if (BackendBus.running && snap == null) GhostBus.refresh() }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (!BackendBus.running) { BackendOfflineHint(); return@Column }

        SectionCard("Ghost portfolio", AccentHi) {
            Text("Paper positions, treated exactly like real ones — same prices, " +
                "same technicals, same calendar, same sell discipline.\n\n" +
                "Buy from the queue below rather than typing symbols in: the " +
                "engines' picks arrive here on their own, so what you end up " +
                "measuring is the system's judgement rather than your own.",
                color = Muted, fontSize = 12.sp, lineHeight = 17.sp)
            GhostBus.lastMessage?.let {
                Spacer(Modifier.height(10.dp))
                StatusBanner(it, if (it.startsWith("Bought")) Bull else Bear)
            }
        }

        // ── The queue: what the ENGINES suggested, waiting to be tested ──
        val recs = GhostBus.recommendations
        val pending = ArrayList<JSONObject>()
        arr(recs, "items")?.let { items ->
            for (i in 0 until items.length()) {
                val it0 = items.optJSONObject(i) ?: continue
                if (it0.optString("status") == "pending") pending.add(it0)
            }
        }
        SectionCard("Recommendations to test (${pending.size})", Bull) {
            Text("Everything the engines have suggested — Macro Ideas, the " +
                "DR-Quant funnel and the cash optimiser — lands here on its own. " +
                "Buy them from this list rather than typing symbols in, so the " +
                "track record measures the system's calls and not your own.",
                color = Muted, fontSize = 12.sp, lineHeight = 17.sp)
            if (pending.isEmpty()) {
                Spacer(Modifier.height(10.dp))
                StatusBanner("Nothing waiting. Run Macro Ideas, the DR-Quant " +
                    "funnel, or the cash optimiser and their picks will appear here.",
                    Muted)
            } else {
                Spacer(Modifier.height(6.dp))
                pending.forEach { RecommendationCard(it) }
            }
            recs?.optJSONObject("counts")?.let { c ->
                Spacer(Modifier.height(10.dp))
                Text("${c.optInt("taken")} taken · ${c.optInt("dismissed")} dismissed " +
                    "· ${c.optInt("pending")} pending", color = Muted, fontSize = 10.sp)
            }
        }

        // ── Which engine is actually worth listening to ──
        arr(snap?.optJSONObject("attribution"), "by_source")
            ?.takeIf { it.length() > 0 }?.let { rows ->
                SectionCard("Scorecard by engine", AccentHi) {
                    Text("Paper P&L split by which engine suggested the position. " +
                        "This is the number the whole exercise exists to produce.",
                        color = Muted, fontSize = 11.sp, lineHeight = 16.sp)
                    Spacer(Modifier.height(10.dp))
                    for (i in 0 until rows.length()) {
                        rows.optJSONObject(i)?.let { EngineScoreRow(it) }
                    }
                    snap?.optJSONObject("attribution")?.optString("note")
                        ?.takeIf { it.isNotBlank() }?.let {
                            Spacer(Modifier.height(10.dp))
                            Text(it, color = Muted, fontSize = 10.sp, lineHeight = 15.sp)
                        }
                }
            }

        snap?.optJSONObject("summary")?.let { s ->
            fun d(k: String) = (s.opt(k) as? Number)?.toDouble() ?: 0.0
            SectionCard("Paper P&L", if (d("total_pnl") >= 0) Bull else Bear) {
                KpiGrid(listOf(
                    Triple("Invested", "₹" + fmtCompact(s.opt("invested")), OnBg),
                    Triple("Value now", "₹" + fmtCompact(s.opt("current_value")), OnBg),
                    Triple("Unrealised", "₹" + fmtCompact(s.opt("unrealised_pnl")),
                        if (d("unrealised_pnl") >= 0) Bull else Bear),
                    Triple("Realised", "₹" + fmtCompact(s.opt("realised_pnl")),
                        if (d("realised_pnl") >= 0) Bull else Bear),
                    Triple("Total paper P&L", "₹" + fmtCompact(s.opt("total_pnl")),
                        if (d("total_pnl") >= 0) Bull else Bear),
                    Triple("Return", "%.2f%%".format(d("unrealised_pnl_pct")),
                        if (d("unrealised_pnl_pct") >= 0) Bull else Bear),
                ))
            }
        }

        // ---- the two charts ----
        SectionCard("Charts", AccentHi) {
            Button(
                onClick = {
                    JobBus.clear("ghost_curve")
                    JobBus.run("ghost_curve", "Building the ghost curves…",
                        maxSecs = 300) { Api.ghostCurve() }
                },
                enabled = !curveJob.running, modifier = Modifier.fillMaxWidth(),
            ) { Text(if (curveJob.running) "Building…" else "📈 Build / refresh charts") }
            curveJob.status?.let {
                Spacer(Modifier.height(8.dp))
                StatusBanner(it, if (curveJob.running) Warn else Bear)
            }
        }

        curveJob.result?.let { c ->
            val pts = arr(c, "points")
            val port = ArrayList<Float>(); val gh = ArrayList<Float>()
            val comb = ArrayList<Float>(); val totalPnl = ArrayList<Float>()
            val realPnl = ArrayList<Float>()
            if (pts != null) for (i in 0 until pts.length()) {
                val o = pts.optJSONObject(i) ?: continue
                port.add((o.opt("portfolio") as? Number)?.toFloat() ?: 0f)
                gh.add((o.opt("ghost") as? Number)?.toFloat() ?: 0f)
                comb.add((o.opt("combined") as? Number)?.toFloat() ?: 0f)
                totalPnl.add((o.opt("total_pnl") as? Number)?.toFloat() ?: 0f)
                realPnl.add((o.opt("real_pnl") as? Number)?.toFloat() ?: 0f)
            }

            if (comb.size >= 2) {
                SectionCard("If you'd acted on these ideas", Bull) {
                    Text("Your real book, the paper positions, and the two together.",
                        color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(10.dp))
                    LineChart(listOf(
                        ChartSeries(comb, "Combined", AccentHi, filled = true),
                        ChartSeries(port, "Real portfolio", Muted, dashed = true),
                        ChartSeries(gh, "Ghost only", Warn),
                    ))
                    Spacer(Modifier.height(14.dp))
                    Text("Total P&L — real plus paper", color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(6.dp))
                    LineChart(listOf(
                        ChartSeries(totalPnl, "Total P&L", Bull, filled = true),
                        ChartSeries(realPnl, "Real P&L only", Muted, dashed = true),
                    ))
                    if (!c.optBoolean("has_real", false)) {
                        Spacer(Modifier.height(10.dp))
                        StatusBanner("The real leg is flat because the performance " +
                            "analysis hasn't run yet — do that in Analysis › " +
                            "Performance and rebuild to see them side by side.", Warn)
                    }
                    c.optString("note").takeIf { it.isNotBlank() }?.let {
                        Spacer(Modifier.height(10.dp))
                        Text(it, color = Muted, fontSize = 10.sp, lineHeight = 15.sp)
                    }
                }
            }

            val g = c.optJSONObject("ghost")
            val gp = arr(g ?: JSONObject(), "points")
            val gv = ArrayList<Float>(); val gi = ArrayList<Float>()
            if (gp != null) for (i in 0 until gp.length()) {
                val o = gp.optJSONObject(i) ?: continue
                gv.add((o.opt("value") as? Number)?.toFloat() ?: 0f)
                gi.add((o.opt("invested") as? Number)?.toFloat() ?: 0f)
            }
            if (gv.size >= 2) {
                SectionCard("Ghost portfolio on its own", Warn) {
                    Text("Paper value against paper capital deployed.",
                        color = Muted, fontSize = 11.sp)
                    Spacer(Modifier.height(10.dp))
                    LineChart(listOf(
                        ChartSeries(gv, "Ghost value", Warn, filled = true),
                        ChartSeries(gi, "Invested", Muted, dashed = true),
                    ))
                }
            } else if (comb.size < 2) {
                SectionCard("Charts", Muted) {
                    StatusBanner("Not enough history yet — the curve starts from your " +
                        "first paper position, so give it a day or two.", Muted)
                }
            }
        }

        // ---- sell discipline ----
        SectionCard("When to sell", Bear) {
            Text("Runs the same exit review a real position would get: RSI, moving " +
                "averages, give-back from the high, time held — then weighs those " +
                "against the market regime and what's on the calendar.",
                color = Muted, fontSize = 12.sp, lineHeight = 17.sp)
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = {
                    JobBus.clear("ghost_review")
                    JobBus.run("ghost_review", "Reviewing every paper position…",
                        maxSecs = 480) { Api.ghostReview() }
                },
                enabled = !reviewJob.running, modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Bear),
            ) { Text(if (reviewJob.running) "Reviewing…" else "🔍 Review my positions") }
            reviewJob.status?.let {
                Spacer(Modifier.height(8.dp))
                StatusBanner(it, if (reviewJob.running) Warn else Bear)
            }
        }

        reviewJob.result?.let { r ->
            r.optJSONObject("signals")?.let { sg ->
                arr(sg, "positions")?.takeIf { it.length() > 0 }?.let { rows ->
                    SectionCard("Exit signals", Warn) {
                        Text("Computed from price data, not guessed.",
                            color = Muted, fontSize = 11.sp)
                        Spacer(Modifier.height(8.dp))
                        for (i in 0 until rows.length()) {
                            rows.optJSONObject(i)?.let { SignalRow(it) }
                        }
                    }
                }
            }
            (r.optJSONObject("ai"))?.let { ai ->
                val text = ai.optString("text", "")
                SectionCard("The call", Bear) {
                    if (ai.optBoolean("ok", text.isNotBlank()) && text.isNotBlank())
                        MarkdownText(text)
                    else StatusBanner(ai.optString("error", "No review returned."), Bear)
                }
            }
        }

        // ---- holdings ----
        arr(snap ?: JSONObject(), "open")?.takeIf { it.length() > 0 }?.let { rows ->
            SectionCard("Open paper positions", Bull) {
                for (i in 0 until rows.length()) {
                    rows.optJSONObject(i)?.let { PositionRow(it) }
                }
            }
        }
        arr(snap ?: JSONObject(), "closed")?.takeIf { it.length() > 0 }?.let { rows ->
            SectionCard("Closed — the honest track record", Muted) {
                Text("Kept deliberately: a paper record that only shows the winners " +
                    "tells you nothing.", color = Muted, fontSize = 11.sp)
                Spacer(Modifier.height(8.dp))
                DataTable(rows, 40)
            }
        }

        if (snap != null && (arr(snap, "open")?.length() ?: 0) == 0 &&
            (arr(snap, "closed")?.length() ?: 0) == 0) {
            SectionCard("Nothing yet", Muted) {
                StatusBanner("No paper positions. Add one above, or tap " +
                    "\"👻 Invest on paper\" on any idea in the Ideas or DR-Quant tabs.",
                    Muted)
            }
        }

        SectionCard("Add your own pick", Muted) {
            Text("Outside the queue. Tracked separately in the scorecard as " +
                "\"Your own pick\", so it can't be confused with the engines' " +
                "record — which is the thing being tested.",
                color = Muted, fontSize = 11.sp, lineHeight = 16.sp)
            Spacer(Modifier.height(10.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(symInput, { symInput = it.uppercase() },
                    label = { Text("Symbol") }, singleLine = true,
                    modifier = Modifier.weight(1.1f))
                Spacer(Modifier.width(8.dp))
                OutlinedTextField(amtInput, { amtInput = it.filter { c -> c.isDigit() } },
                    label = { Text("₹") }, singleLine = true, modifier = Modifier.weight(1f))
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = {
                        val amt = amtInput.toDoubleOrNull() ?: 0.0
                        if (symInput.isNotBlank() && amt > 0) {
                            GhostBus.buy(symInput.trim(), amt, "manual")
                            symInput = ""
                        }
                    },
                    enabled = symInput.isNotBlank() && amtInput.isNotBlank(),
                ) { Text("Invest") }
            }
        }

        if (snap != null && ((arr(snap, "open")?.length() ?: 0) > 0 ||
                (arr(snap, "closed")?.length() ?: 0) > 0)) {
            SectionCard("Reset", Muted) {
                if (!confirmReset) {
                    TextButton(onClick = { confirmReset = true }) {
                        Text("Clear the whole ghost portfolio", color = Bear, fontSize = 12.sp)
                    }
                } else {
                    StatusBanner("This deletes every paper position and its track " +
                        "record. It cannot be undone.", Bear)
                    Spacer(Modifier.height(8.dp))
                    Row {
                        Button(onClick = {
                            scope.launch { Api.ghostReset(); GhostBus.refresh() }
                            confirmReset = false
                        }, colors = ButtonDefaults.buttonColors(containerColor = Bear)) {
                            Text("Yes, clear it")
                        }
                        Spacer(Modifier.width(8.dp))
                        TextButton(onClick = { confirmReset = false }) { Text("Cancel") }
                    }
                }
            }
        }
        Spacer(Modifier.height(24.dp))
    }
}

/** One pending engine recommendation, with Buy and Dismiss. */
@Composable
private fun RecommendationCard(r: JSONObject) {
    val id = r.optString("id")
    val suggested = (r.opt("suggested_amount") as? Number)?.toDouble()
    var amount by remember(id) {
        mutableStateOf(suggested?.let { "%.0f".format(it) } ?: "25000")
    }
    val busy = GhostBus.busyId == id
    val conv = r.optString("conviction").takeIf { it.isNotBlank() && it != "null" }

    Column(
        Modifier.fillMaxWidth().padding(vertical = 5.dp)
            .clip(RoundedCornerShape(10.dp)).background(Panel2)
            .border(1.dp, BorderCol.copy(alpha = 0.6f), RoundedCornerShape(10.dp))
            .padding(12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(r.optString("symbol"), color = OnBg, fontSize = 15.sp,
                    fontWeight = FontWeight.Bold)
                Text(r.optString("source_label") +
                    (r.optString("sector").takeIf { it.isNotBlank() && it != "null" }
                        ?.let { " · $it" } ?: ""),
                    color = AccentHi, fontSize = 10.5.sp)
            }
            conv?.let { Pill(it, if (it == "HIGH") Bull else Muted) }
        }
        r.optString("rationale").takeIf { it.isNotBlank() && it != "null" }?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, color = OnBg.copy(alpha = 0.88f), fontSize = 12.sp, lineHeight = 17.sp)
        }
        // What the engine itself proposed — shown so you can take its call
        // verbatim instead of substituting your own judgement.
        val entry = (r.opt("suggested_entry") as? Number)?.toDouble()
        val shares = (r.opt("suggested_shares") as? Number)?.toInt()
        if (entry != null || suggested != null) {
            Spacer(Modifier.height(8.dp))
            Text(listOfNotNull(
                entry?.let { "suggested entry ₹${fmtNum(it)}" },
                suggested?.let { "suggested ₹${fmtCompact(it)}" },
                shares?.takeIf { it > 0 }?.let { "$it shares" },
            ).joinToString("  ·  "), color = Muted, fontSize = 10.5.sp)
        }
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = amount,
                onValueChange = { amount = it.filter { c -> c.isDigit() } },
                label = { Text("₹") }, singleLine = true,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = { GhostBus.take(id, amount.toDoubleOrNull()) },
                enabled = !busy && BackendBus.running && amount.isNotBlank(),
                colors = ButtonDefaults.buttonColors(containerColor = Bull),
            ) { Text(if (busy) "…" else "Buy") }
            Spacer(Modifier.width(4.dp))
            TextButton(onClick = { GhostBus.dismiss(id) }, enabled = !busy) {
                Text("Skip", color = Muted, fontSize = 12.sp)
            }
        }
    }
}

/** One engine's paper track record. */
@Composable
private fun EngineScoreRow(r: JSONObject) {
    val pnl = (r.opt("total_pnl") as? Number)?.toDouble() ?: 0.0
    val col = if (pnl >= 0) Bull else Bear
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(r.optString("label"), color = OnBg, fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Text("₹" + fmtCompact(r.opt("total_pnl")), color = col,
                fontSize = 14.sp, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.height(3.dp))
        val ret = (r.opt("return_pct") as? Number)?.toDouble()
        val hit = (r.opt("hit_rate_pct") as? Number)?.toDouble()
        Text(listOfNotNull(
            "${r.optInt("n_total")} position(s)",
            "₹${fmtCompact(r.opt("invested"))} put in",
            ret?.let { "%.2f%% return".format(it) },
            hit?.let { "%.0f%% hit rate (%d closed)".format(it, r.optInt("n_closed")) },
        ).joinToString("  ·  "), color = Muted, fontSize = 10.5.sp)
        Divider(color = BorderCol.copy(alpha = 0.5f), modifier = Modifier.padding(top = 8.dp))
    }
}

@Composable
private fun PositionRow(p: JSONObject) {
    val pnl = (p.opt("pnl") as? Number)?.toDouble() ?: 0.0
    val col = if (pnl >= 0) Bull else Bear
    Column(
        Modifier.fillMaxWidth().padding(vertical = 5.dp)
            .clip(RoundedCornerShape(10.dp)).background(Panel2)
            .border(1.dp, BorderCol.copy(alpha = 0.6f), RoundedCornerShape(10.dp))
            .padding(12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(p.optString("symbol"), color = OnBg, fontSize = 14.sp,
                    fontWeight = FontWeight.Bold)
                Text("${p.optInt("qty")} sh @ ₹${fmtNum(p.opt("entry_price"))} · " +
                    "${p.optInt("held_days")}d · ${p.optString("source")}",
                    color = Muted, fontSize = 10.5.sp)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text("₹" + fmtCompact(p.opt("pnl")), color = col,
                    fontSize = 15.sp, fontWeight = FontWeight.Bold)
                Text("%.2f%%".format((p.opt("pnl_pct") as? Number)?.toDouble() ?: 0.0),
                    color = col, fontSize = 11.sp)
            }
        }
        if (!p.optBoolean("priced", true)) {
            Spacer(Modifier.height(6.dp))
            Text("No live price right now — shown at cost.", color = Warn, fontSize = 10.sp)
        }
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { GhostBus.sell(p.optString("id")) },
                contentPadding = PaddingValues(0.dp)) {
                Text("Sell at market", color = Bear, fontSize = 12.sp)
            }
            Spacer(Modifier.weight(1f))
            TextButton(onClick = { UiNav.open(UiNav.Screen.DeepDive(p.optString("symbol"))) },
                contentPadding = PaddingValues(0.dp)) {
                Text("Deep dive", color = AccentHi, fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun SignalRow(s: JSONObject) {
    val flags = arr(s, "flags")
    val n = flags?.length() ?: 0
    val col = if (n == 0) Bull else Warn
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(s.optString("symbol"), color = OnBg, fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            val g = (s.opt("gain_pct") as? Number)?.toDouble() ?: 0.0
            Text("%+.2f%%".format(g), color = if (g >= 0) Bull else Bear, fontSize = 12.sp)
        }
        Spacer(Modifier.height(4.dp))
        Text("RSI ${fmtNum(s.opt("rsi"))} · 50DMA ₹${fmtNum(s.opt("dma50"))} · " +
            "200DMA ₹${fmtNum(s.opt("dma200"))}", color = Muted, fontSize = 10.5.sp)
        if (n > 0) {
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                for (i in 0 until minOf(n, 3)) Pill(flags!!.optString(i), col)
            }
        }
        arr(s, "reasons")?.let { rs ->
            Spacer(Modifier.height(6.dp))
            for (i in 0 until rs.length())
                Text("• " + rs.optString(i), color = Muted, fontSize = 11.sp,
                    lineHeight = 16.sp, modifier = Modifier.padding(vertical = 1.dp))
        }
        Divider(color = BorderCol.copy(alpha = 0.5f),
            modifier = Modifier.padding(top = 8.dp))
    }
}
