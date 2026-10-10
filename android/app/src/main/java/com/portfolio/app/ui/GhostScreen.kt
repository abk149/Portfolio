package com.portfolio.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
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

    /**
     * Re-run the analysis for ONE recommendation, optionally only the part
     * that failed. A full pass fetches filings and coverage and takes about a
     * minute, so redoing all of it because the model blinked is waste.
     */
    fun retryResearch(id: String, scope0: String) {
        if (busyId != null) return
        busyId = id
        scope.launch {
            lastMessage = when (val r = Api.recommendationResearch(id, scope0)) {
                is Api.Resp.Err -> "Retry failed: ${r.message}"
                is Api.Resp.Ok -> {
                    val jid = r.body.optString("job_id")
                    if (jid.isBlank()) r.body.optString("error", "Couldn't start the retry.")
                    else {
                        var out: org.json.JSONObject? = null
                        for (i in 0 until 240) {          // up to ~8 min
                            kotlinx.coroutines.delay(2000)
                            val j = Api.job(jid).objOrNull() ?: continue
                            if (j.optString("status") != "running") { out = j; break }
                        }
                        val res = out?.optJSONObject("result")?.optJSONObject("research")
                        when {
                            out == null -> "Still running — check back shortly."
                            res == null -> "Retry failed: ${out.optString("error", "unknown")}"
                            res.optBoolean("complete") -> "Analysis complete."
                            else -> "Still incomplete: " +
                                (arr(res, "failed_sections")?.let { f ->
                                    (0 until f.length()).joinToString(", ") { f.optString(it) }
                                } ?: "unknown")
                        }
                    }
                }
            }
            busyId = null
            refresh()
        }
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
                Text("👻 Invest on paper", color = AccentHi, fontSize = Type.secondary)
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
                color = Muted, fontSize = Type.label)
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
    var sizeCash by remember { mutableStateOf("50000") }
    var confirmReset by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    // Refresh on EVERY visit, not just the first. GhostBus is a singleton, so
    // the old `if (snap == null)` guard meant the queue was fetched once and
    // never again — run an engine, come back, and its picks were missing even
    // though the backend had recorded them.
    LaunchedEffect(Unit) { if (BackendBus.running) GhostBus.refresh() }

    // And refresh the moment a producing engine finishes, so picks appear even
    // if you never leave this screen.
    val producers = listOf(
        JobBus.state("themes").finishedAt,
        JobBus.state("quant").finishedAt,
        JobBus.state("deploy_cash").finishedAt,
        JobBus.state("alloc_themes").finishedAt,
        JobBus.state("optimize").finishedAt,
        JobBus.state("gems").finishedAt,
    )
    LaunchedEffect(producers) { if (BackendBus.running) GhostBus.refresh() }

    // The queue is computed once per refresh, not on every recomposition.
    val recs = GhostBus.recommendations
    val (pending, blocked) = remember(recs) {
        val p = ArrayList<JSONObject>(); val b = ArrayList<JSONObject>()
        arr(recs, "items")?.let { items ->
            for (i in 0 until items.length()) {
                val it0 = items.optJSONObject(i) ?: continue
                when (it0.optString("status")) {
                    "pending" -> p.add(it0)
                    "blocked" -> b.add(it0)
                }
            }
        }
        p to b
    }
    val nOpen = arr(snap ?: JSONObject(), "open")?.length() ?: 0

    // Three views instead of one twelve-section page: what to test, what you
    // hold, and whether the engines have earned trust. Only the visible view is
    // composed, and the queue is a lazy list — each recommendation card carries
    // a full research dossier, so building all of them up front was the main
    // reason this tab felt slow.
    val view = UiNav.ghostView

    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = Space.xl)) {
        if (!BackendBus.running) {
            item { Column { BackendOfflineHint() } }
            return@LazyColumn
        }

        item(key = "header") {
            Column(Modifier.padding(start = Space.edge, end = Space.edge, top = Space.md)) {
                Text("Ghost portfolio", color = OnBg, fontSize = Type.title,
                    fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(Space.xs))
                Lede("Paper-trade the engines' picks — same prices, same sell " +
                    "discipline as real money — and find out whether they deserve " +
                    "your trust before any real rupee follows them.")
                GhostBus.lastMessage?.let {
                    Spacer(Modifier.height(Space.sm))
                    Banner(it, if (it.startsWith("Bought")) Bull else Bear)
                }
                Spacer(Modifier.height(Space.md))
                ChipRow(
                    options = listOf(
                        "queue" to "To test · ${pending.size}",
                        "positions" to "Positions · $nOpen",
                        "record" to "Track record",
                    ),
                    selected = view, onSelect = { UiNav.ghostView = it },
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                )
            }
        }

        when (view) {
            "queue" -> {
                item(key = "queue-head") {
                    SectionCard("Recommendations to test", Bull) {
                        Lede("Every pick from Macro Ideas, DR-Quant, Hidden Gems and " +
                            "the cash optimiser lands here on its own. Buying from this list " +
                            "(not typing symbols in) keeps the track record about the " +
                            "system's calls, not yours.")
                        // Engines size their ideas inconsistently — the optimiser thinks
                        // in weights of your book, the others don't size at all. This puts
                        // every pending name through the same optimiser against your real
                        // holdings, so the queue speaks one language.
                        if (pending.isNotEmpty()) {
                            Spacer(Modifier.height(12.dp))
                            val sizeJob = JobBus.state("size_recs")
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                OutlinedTextField(
                                    value = sizeCash,
                                    onValueChange = { sizeCash = it.filter { c -> c.isDigit() } },
                                    label = { Text("Deploy ₹") }, singleLine = true,
                                    modifier = Modifier.weight(1f),
                                )
                                Spacer(Modifier.width(8.dp))
                                Button(
                                    onClick = {
                                        val amt = sizeCash.toDoubleOrNull() ?: 0.0
                                        if (amt > 0) {
                                            JobBus.clear("size_recs")
                                            JobBus.run(
                                                key = "size_recs",
                                                status = "Sizing and researching every pending idea…",
                                                maxSecs = 900,          // full research per name is slow
                                                onTick = { id, st ->
                                                    Api.recommendationsProgress(id).objOrNull()
                                                        ?.optJSONObject("progress")?.let { pr ->
                                                            val pct = (pr.opt("pct") as? Number)?.toFloat()
                                                            st.progress = pct?.let { it / 100f }
                                                            st.detail = pr.optString("message")
                                                                .takeIf { it.isNotBlank() }
                                                        }
                                                },
                                            ) { Api.recommendationsOptimize(amt) }
                                        }
                                    },
                                    enabled = !sizeJob.running && BackendBus.running,
                                ) { Text(if (sizeJob.running) "…" else "⚖ Size these") }
                            }
                            Text("Sizes every pending idea against the portfolio you already " +
                                "hold — in whole shares you can actually place — and builds a " +
                                "full research dossier for each: fundamentals, the last " +
                                "filings, the price model, the macro regime, upcoming events, " +
                                "news, and social that named sources corroborate.\n" +
                                "Expect a minute or so per name; it keeps running if you " +
                                "leave this screen.",
                                color = Muted, fontSize = Type.label, lineHeight = 15.sp)
                            sizeJob.status?.let {
                                Spacer(Modifier.height(6.dp))
                                StatusBanner(it, if (sizeJob.running) Warn else Bear)
                            }
                            sizeJob.result?.let { r ->
                                LaunchedEffect(sizeJob.finishedAt) { GhostBus.refresh() }
                                Spacer(Modifier.height(8.dp))
                                val up = (r.opt("sharpe_uplift") as? Number)?.toDouble()
                                val t = r.optJSONObject("totals")
                                StatusBanner(
                                    "${r.optInt("funded")} of ${r.optInt("sized")} ideas funded" +
                                    (if (r.has("researched")) " and researched" else "") + ".\n" +
                                    "Deploying ₹${fmtCompact(t?.opt("deployed"))} of " +
                                    "₹${fmtCompact(t?.opt("cash"))} across ${t?.optInt("n_positions")} " +
                                    "positions — ₹${fmtCompact(t?.opt("leftover"))} left over." +
                                    (up?.let { "\nSharpe ${fmtNum(r.optJSONObject("before")?.opt("sharpe"))} → " +
                                        "${fmtNum(r.optJSONObject("after")?.opt("sharpe"))} (+${fmtNum(it)})." } ?: ""),
                                    Bull)
                                arr(r, "unaffordable")?.takeIf { it.length() > 0 }?.let { un ->
                                    Spacer(Modifier.height(8.dp))
                                    Text("Couldn't fund a whole share of: " +
                                        (0 until un.length()).joinToString(", ") {
                                            un.optJSONObject(it)?.optString("symbol") ?: ""
                                        }, color = Warn, fontSize = Type.label)
                                }
                            }
                        }

                        if (pending.isEmpty()) {
                            Spacer(Modifier.height(10.dp))
                            val everRan = (recs?.optJSONObject("counts")?.length() ?: 0) > 0
                            StatusBanner(
                                if (everRan)
                                    "Nothing pending — you've acted on everything suggested " +
                                    "so far. Run an engine again for fresh picks."
                                else
                                    "Nothing waiting yet. Run Macro Ideas, the DR-Quant funnel, " +
                                    "a Hidden Gems scan or the cash optimiser and their " +
                                    "picks land here automatically.",
                                Muted)
                            Spacer(Modifier.height(8.dp))
                            TextButton(onClick = { GhostBus.refresh() },
                                contentPadding = PaddingValues(0.dp)) {
                                Text("↻ Check again", color = AccentHi, fontSize = Type.secondary)
                            }
                        }
                        recs?.optJSONObject("counts")?.let { c ->
                            Spacer(Modifier.height(Space.sm))
                            Note("${c.optInt("taken")} taken · ${c.optInt("dismissed")} dismissed " +
                                "· ${c.optInt("pending")} pending")
                        }
                    }
                }
                items(pending, key = { "p-" + it.optString("id") }) { RecommendationCard(it) }
                if (blocked.isNotEmpty()) {
                    item(key = "blocked-head") {
                        SectionCard("Rejected by the checks (${blocked.size})", Bear) {
                            Lede("Suggested by an engine but failed a required test, so " +
                                "not offered. Kept visible — the system saying no is " +
                                "worth seeing, and you can still take one deliberately.")
                        }
                    }
                    items(blocked, key = { "b-" + it.optString("id") }) {
                        RecommendationCard(it, blocked = true)
                    }
                }
            }

            "positions" -> item(key = "positions") {
                Column {
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

                // ---- sell discipline ----
                SectionCard("When to sell", Bear) {
                    Text("Runs the same exit review a real position would get: RSI, moving " +
                        "averages, give-back from the high, time held — then weighs those " +
                        "against the market regime and what's on the calendar.",
                        color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
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
                                    color = Muted, fontSize = Type.secondary)
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
                            "tells you nothing.", color = Muted, fontSize = Type.secondary)
                        Spacer(Modifier.height(8.dp))
                        DataTable(rows, 40)
                    }
                }

                if (snap != null && (arr(snap, "open")?.length() ?: 0) == 0 &&
                    (arr(snap, "closed")?.length() ?: 0) == 0) {
                    EmptyState(
                        icon = "👻",
                        title = "No paper positions yet",
                        body = "Buy a pick from the To-test list, or tap \"👻 Invest on " +
                            "paper\" on any idea under Discover.",
                        actionText = "Go to the To-test list",
                        onAction = { UiNav.ghostView = "queue" },
                    )
                }

                SectionCard("Add your own pick", Muted) {
                    Text("Outside the queue. Tracked separately in the scorecard as " +
                        "\"Your own pick\", so it can't be confused with the engines' " +
                        "record — which is the thing being tested.",
                        color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
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
                                Text("Clear the whole ghost portfolio", color = Bear, fontSize = Type.secondary)
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
                }
            }

            else -> item(key = "record") {
                Column {
                // ── Which engine is actually worth listening to ──
                arr(snap?.optJSONObject("attribution"), "by_source")
                    ?.takeIf { it.length() > 0 }?.let { rows ->
                        SectionCard("Scorecard by engine", AccentHi) {
                            Text("Paper P&L split by which engine suggested the position. " +
                                "This is the number the whole exercise exists to produce.",
                                color = Muted, fontSize = Type.secondary, lineHeight = 18.sp)
                            Spacer(Modifier.height(10.dp))
                            for (i in 0 until rows.length()) {
                                rows.optJSONObject(i)?.let { EngineScoreRow(it) }
                            }
                            snap?.optJSONObject("attribution")?.optString("note")
                                ?.takeIf { it.isNotBlank() }?.let {
                                    Spacer(Modifier.height(10.dp))
                                    Text(it, color = Muted, fontSize = Type.label, lineHeight = 15.sp)
                                }
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
                                color = Muted, fontSize = Type.secondary)
                            Spacer(Modifier.height(10.dp))
                            LineChart(listOf(
                                ChartSeries(comb, "Combined", AccentHi, filled = true),
                                ChartSeries(port, "Real portfolio", Muted, dashed = true),
                                ChartSeries(gh, "Ghost only", Warn),
                            ))
                            Spacer(Modifier.height(14.dp))
                            Text("Total P&L — real plus paper", color = Muted, fontSize = Type.secondary)
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
                                Text(it, color = Muted, fontSize = Type.label, lineHeight = 15.sp)
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
                                color = Muted, fontSize = Type.secondary)
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

                    if (snap != null && arr(snap?.optJSONObject("attribution"), "by_source")
                            .let { it == null || it.length() == 0 } && curveJob.result == null) {
                        SectionCard("Nothing to score yet", Muted) {
                            Lede("The scorecard fills in once paper positions have a few " +
                                "days of history. Buy something from the queue to start.")
                        }
                    }
                }
            }
        }
    }
}

/** One pending engine recommendation, with Buy and Dismiss. */
@Composable
private fun RecommendationCard(r: JSONObject, blocked: Boolean = false) {
    val id = r.optString("id")
    val suggested = (r.opt("suggested_amount") as? Number)?.toDouble()
    // Saveable: the queue is a lazy list, so a card scrolled off screen is
    // disposed — a plain remember would forget an amount you'd typed.
    var amount by rememberSaveable(id) {
        mutableStateOf(suggested?.let { "%.0f".format(it) } ?: "25000")
    }
    val busy = GhostBus.busyId == id
    val conv = r.optString("conviction").takeIf { it.isNotBlank() && it != "null" }

    Column(
        Modifier.fillMaxWidth().padding(horizontal = Space.edge, vertical = Space.xs)
            .clip(RoundedCornerShape(Radius.card)).background(Panel)
            .border(1.dp, if (blocked) Bear.copy(alpha = 0.35f) else BorderCol,
                RoundedCornerShape(Radius.card))
            .padding(Space.lg)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(r.optString("symbol"), color = OnBg, fontSize = Type.heading,
                    fontWeight = FontWeight.Bold)
                Text(r.optString("source_label") +
                    (r.optString("sector").takeIf { it.isNotBlank() && it != "null" }
                        ?.let { " · $it" } ?: ""),
                    color = AccentHi, fontSize = Type.label)
            }
            conv?.let { Pill(it, if (it == "HIGH") Bull else Muted) }
        }
        if (blocked) {
            r.optString("blocked_reason").takeIf { it.isNotBlank() && it != "null" }?.let {
                Spacer(Modifier.height(8.dp))
                StatusBanner(it, Bear)
            }
            arr(r, "blocked_checks")?.let { bc ->
                for (i in 0 until bc.length()) {
                    val c = bc.optJSONObject(i) ?: continue
                    Text("• ${c.optString("label")}: ${c.optString("detail")}",
                        color = Bear.copy(alpha = 0.9f), fontSize = Type.secondary,
                        lineHeight = 18.sp, modifier = Modifier.padding(top = 3.dp))
                }
            }
        }
        r.optString("rationale").takeIf { it.isNotBlank() && it != "null" }?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, color = OnBg.copy(alpha = 0.88f), fontSize = Type.secondary, lineHeight = 18.sp)
        }
        // What to actually buy. After sizing this is the optimiser's answer
        // against your real book; before it, whatever the engine offered.
        val entry = (r.opt("suggested_entry") as? Number)?.toDouble()
        val shares = (r.opt("suggested_shares") as? Number)?.toInt()
        val weight = (r.opt("suggested_weight_pct") as? Number)?.toDouble()
        val sized = r.optString("sized_at").isNotBlank()
        if (entry != null || suggested != null || weight != null) {
            Spacer(Modifier.height(8.dp))
            Text(listOfNotNull(
                suggested?.let { "₹${fmtCompact(it)}" },
                weight?.let { "%.1f%% of book".format(it) },
                shares?.takeIf { it > 0 }?.let { "$it shares" },
                entry?.let { "at ₹${fmtNum(it)}" },
            ).joinToString("  ·  "),
                color = if (sized) AccentHi else Muted, fontSize = Type.secondary)
            if (sized) Text("sized against your holdings", color = Muted, fontSize = Type.label)
        }
        r.optString("sizing_note").takeIf { it.isNotBlank() && it != "null" }?.let {
            Spacer(Modifier.height(6.dp))
            StatusBanner(it, Warn)
        }
        r.optString("age_label").takeIf { it.isNotBlank() && it != "null" }?.let {
            Spacer(Modifier.height(6.dp))
            Text("suggested $it", color = Muted, fontSize = Type.label)
        }

        // Analysis is available on EVERY recommendation, sized or not. It used
        // to be reachable only through "Size these", so a single stock could
        // not be looked at on its own — which is backwards, since deciding
        // whether you want a name at all comes before deciding how much.
        val research = r.optJSONObject("research")
        if (research != null) {
            ResearchPanel(research, id)
        } else {
            val busy = GhostBus.busyId == id
            Spacer(Modifier.height(10.dp))
            Divider(color = BorderCol.copy(alpha = 0.5f))
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Not analysed yet", color = Muted, fontSize = Type.secondary,
                    modifier = Modifier.weight(1f))
                OutlinedButton(
                    onClick = { GhostBus.retryResearch(id, "all") },
                    enabled = !busy && BackendBus.running,
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 2.dp),
                ) { Text(if (busy) "Analysing…" else "🔬 Analyse this stock", fontSize = Type.secondary) }
            }
            Text("Runs the full pipeline for this one name — macro, the last " +
                "four quarters, company reports, cyclicality and the required " +
                "checks. About a minute.",
                color = Muted, fontSize = Type.label, lineHeight = 15.sp,
                modifier = Modifier.padding(top = 4.dp))
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
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (blocked) Muted else Bull),
            ) { Text(if (busy) "…" else if (blocked) "Buy anyway" else "Buy") }
            Spacer(Modifier.width(4.dp))
            TextButton(onClick = { GhostBus.dismiss(id) }, enabled = !busy) {
                Text("Skip", color = Muted, fontSize = Type.secondary)
            }
        }
    }
}

/**
 * The full dossier behind a recommendation, collapsed by default.
 *
 * The ghost book is a rehearsal for real money, so the reasoning has to be
 * inspectable — not just a verdict. Collapsed so the queue stays scannable;
 * everything that went into the call is one tap away.
 */
@Composable
private fun ResearchPanel(res: JSONObject, recId: String) {
    var open by remember { mutableStateOf(false) }
    val v = res.optJSONObject("verdict")
    val counts = res.optJSONObject("counts")
    val verdict = v?.optString("verdict").orEmpty()
    val conviction = v?.optString("conviction").orEmpty()
    val col = when (verdict) {
        "BUY", "ACCUMULATE" -> Bull
        "AVOID" -> Bear
        else -> Warn
    }

    Spacer(Modifier.height(10.dp))
    Divider(color = BorderCol.copy(alpha = 0.5f))
    Spacer(Modifier.height(8.dp))
    Row(Modifier.fillMaxWidth().clickable { open = !open },
        verticalAlignment = Alignment.CenterVertically) {
        if (verdict.isNotBlank()) {
            Pill(verdict + (if (conviction.isNotBlank()) " · $conviction" else ""), col)
            Spacer(Modifier.width(8.dp))
        }
        Text(
            counts?.let {
                "${it.optInt("news")} articles · ${it.optInt("reports")} filings · " +
                "${it.optInt("social")} corroborated posts · ${it.optInt("events")} events"
            } ?: "research",
            color = Muted, fontSize = Type.label, modifier = Modifier.weight(1f))
        Text(if (open) "▲" else "▼ detail", color = AccentHi, fontSize = Type.label)
    }

    v?.optString("thesis")?.takeIf { it.isNotBlank() }?.let {
        Spacer(Modifier.height(6.dp))
        Text(it, color = OnBg.copy(alpha = 0.9f), fontSize = Type.secondary, lineHeight = 18.sp,
            maxLines = if (open) Int.MAX_VALUE else 3)
    }
    v?.optString("error")?.takeIf { it.isNotBlank() }?.let {
        Spacer(Modifier.height(6.dp))
        StatusBanner("Couldn't produce a final judgement: $it\nThe research " +
            "below is still complete.", Warn)
    }

    // Whatever failed gets its own retry, so a working dossier isn't thrown
    // away to fix one broken part.
    val failed = arr(res, "failed_sections")
    val retryable = arr(res, "retryable")
    if (failed != null && failed.length() > 0) {
        Spacer(Modifier.height(8.dp))
        Text("Incomplete: " + (0 until failed.length()).joinToString(", ") {
            failed.optString(it) }, color = Warn, fontSize = Type.secondary)
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            val busy = GhostBus.busyId == recId
            if (retryable != null) for (i in 0 until retryable.length()) {
                val sc = retryable.optString(i)
                OutlinedButton(
                    onClick = { GhostBus.retryResearch(recId, sc) },
                    enabled = !busy && BackendBus.running,
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 2.dp),
                ) {
                    Text(when (sc) {
                        "judgement" -> "↻ Re-run judgement"
                        "documents" -> "↻ Re-fetch filings"
                        "news" -> "↻ Re-fetch news"
                        else -> "↻ Re-run all"
                    }, fontSize = Type.secondary)
                }
            }
            OutlinedButton(
                onClick = { GhostBus.retryResearch(recId, "all") },
                enabled = !busy && BackendBus.running,
                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 2.dp),
            ) { Text(if (busy) "…" else "↻ Full re-run", fontSize = Type.secondary) }
        }
    } else {
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            val busy = GhostBus.busyId == recId
            OutlinedButton(
                onClick = { GhostBus.retryResearch(recId, "judgement") },
                enabled = !busy && BackendBus.running,
                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 2.dp),
            ) { Text("↻ Re-judge", fontSize = Type.secondary) }
            OutlinedButton(
                onClick = { GhostBus.retryResearch(recId, "all") },
                enabled = !busy && BackendBus.running,
                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 2.dp),
            ) { Text("↻ Refresh analysis", fontSize = Type.secondary) }
        }
    }

    if (!open) return

    @Composable
    fun para(label: String, value: String?) {
        if (value.isNullOrBlank() || value == "null") return
        Spacer(Modifier.height(10.dp))
        Text(label.uppercase(), color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        Spacer(Modifier.height(3.dp))
        Text(value, color = OnBg.copy(alpha = 0.9f), fontSize = Type.secondary, lineHeight = 18.sp)
    }

    @Composable
    fun bullets(label: String, arr0: JSONArray?, color: Color) {
        if (arr0 == null || arr0.length() == 0) return
        Spacer(Modifier.height(10.dp))
        Text(label.uppercase(), color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        for (i in 0 until arr0.length()) {
            Text("• " + arr0.optString(i), color = color, fontSize = Type.secondary,
                lineHeight = 18.sp, modifier = Modifier.padding(top = 2.dp))
        }
    }

    para("Is the move still ahead?", v?.optString("move_left"))
    bullets("What could re-rate it", v?.optJSONArray("catalysts"), Bull)
    para("The company itself", v?.optString("micro_view"))
    para("What the measured sensitivities mean", v?.optString("macro_view"))
    para("Timing", v?.optString("timing"))
    para("What people are saying", v?.optString("what_people_say"))
    bullets("Key risks", v?.optJSONArray("key_risks"), Warn)
    bullets("What would change this view", v?.optJSONArray("what_would_change_my_mind"), AccentHi)
    para("Confidence", v?.optString("confidence_note"))

    // ---- the uniform channels: identical for every stock ----
    res.optJSONObject("gate")?.takeIf { it.has("checks") }?.let { g ->
        Spacer(Modifier.height(12.dp))
        Text("REQUIRED CHECKS (${g.optInt("n_passed")}/${g.optInt("n_total")})",
            color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        arr(g, "checks")?.let { cs ->
            for (i in 0 until cs.length()) {
                val c = cs.optJSONObject(i) ?: continue
                val st = c.optString("status")
                val col = when (st) { "pass" -> Bull; "fail" -> Bear; else -> Muted }
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                    Text(when (st) { "pass" -> "✓"; "fail" -> "✗"; else -> "–" },
                        color = col, fontSize = Type.secondary)
                    Spacer(Modifier.width(8.dp))
                    Column {
                        Text(c.optString("label") +
                            (if (c.optBoolean("required")) "" else "  (context)"),
                            color = OnBg.copy(alpha = 0.9f), fontSize = Type.secondary)
                        Text(c.optString("detail"), color = Muted, fontSize = Type.label,
                            lineHeight = 15.sp)
                    }
                }
            }
        }
    }

    res.optJSONObject("runway")?.takeIf { !it.has("error") }?.let { rw ->
        Spacer(Modifier.height(12.dp))
        Text("HOW MUCH MOVE IS LEFT", color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        Spacer(Modifier.height(3.dp))
        val stance = rw.optString("stance")
        Pill("$stance · ${fmtNum(rw.opt("runway_score"))}/100",
            if (stance == "room left") Bull else if (stance == "move largely made") Bear else Warn)
        Spacer(Modifier.height(6.dp))
        Text("3m ${fmtNum(rw.opt("ret_3m"))}% · 6m ${fmtNum(rw.opt("ret_6m"))}% · " +
            "${fmtNum(rw.opt("from_52w_high_pct"))}% from the 52-week high · " +
            "RSI ${fmtNum(rw.opt("rsi"))}",
            color = OnBg.copy(alpha = 0.85f), fontSize = Type.secondary, lineHeight = 18.sp)
        Text(rw.optString("reading"), color = Muted, fontSize = Type.label, lineHeight = 15.sp)
    }

    res.optJSONObject("factors")?.takeIf { !it.has("error") }?.let { fo ->
        Spacer(Modifier.height(12.dp))
        Text("WHAT DRIVES IT", color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        arr(fo, "factors")?.let { fs ->
            for (i in 0 until fs.length()) {
                val f = fs.optJSONObject(i) ?: continue
                val beta = (f.opt("beta") as? Number)?.toDouble() ?: 0.0
                val material = f.optBoolean("material")
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Text(if (material) "●" else "○",
                        color = if (material) AccentHi else Muted, fontSize = Type.label)
                    Spacer(Modifier.width(8.dp))
                    Text(f.optString("label"), color = OnBg.copy(alpha = 0.9f),
                        fontSize = Type.secondary, modifier = Modifier.weight(1f))
                    Text("%+.2f".format(beta),
                        color = if (beta >= 0) Bull else Bear, fontSize = Type.secondary)
                }
            }
        }
        Text(fo.optString("reading"), color = Muted, fontSize = Type.label, lineHeight = 15.sp,
            modifier = Modifier.padding(top = 4.dp))
    }

    res.optJSONObject("quarters")?.takeIf { !it.has("error") }?.let { q ->
        Spacer(Modifier.height(12.dp))
        Text("LAST FOUR QUARTERS (${q.optString("basis")})", color = Muted,
            fontSize = Type.caps, letterSpacing = 0.6.sp)
        Spacer(Modifier.height(4.dp))
        arr(q, "quarters")?.let { qs ->
            for (i in 0 until qs.length()) {
                val r0 = qs.optJSONObject(i) ?: continue
                val yoyP = (r0.opt("pat_yoy_pct") as? Number)?.toDouble()
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Text(r0.optString("label"), color = OnBg.copy(alpha = 0.9f),
                        fontSize = Type.secondary, modifier = Modifier.width(62.dp))
                    Text("₹${fmtCompact(r0.opt("revenue_cr"))}cr",
                        color = Muted, fontSize = Type.label, modifier = Modifier.weight(1f))
                    Text("PAT ₹${fmtCompact(r0.opt("pat_cr"))}cr",
                        color = Muted, fontSize = Type.label, modifier = Modifier.weight(1f))
                    Text(yoyP?.let { "%+.0f%%".format(it) } ?: "—",
                        color = if ((yoyP ?: 0.0) >= 0) Bull else Bear, fontSize = Type.secondary)
                }
            }
        }
        (q.optJSONObject("trend"))?.optString("reading")?.takeIf { it.isNotBlank() }?.let {
            Spacer(Modifier.height(4.dp))
            Text(it, color = OnBg.copy(alpha = 0.85f), fontSize = Type.label, lineHeight = 15.sp)
        }
        // Say how old the newest filing is — an exchange feed can lag badly,
        // and "latest quarter" that is really a year old is misleading.
        q.optString("freshness").takeIf { it.isNotBlank() }?.let {
            Spacer(Modifier.height(4.dp))
            Text(it, color = if (q.optBoolean("stale")) Warn else Muted,
                fontSize = Type.label, lineHeight = 15.sp)
        }
    }

    res.optJSONObject("seasonality")?.takeIf { !it.has("error") }?.let { se ->
        Spacer(Modifier.height(12.dp))
        Text("SEASONALITY", color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        Spacer(Modifier.height(4.dp))
        val labels = ArrayList<String>(); val avgs = ArrayList<Float>()
        val wins = ArrayList<Float?>()
        arr(se, "by_month")?.let { ms ->
            for (i in 0 until ms.length()) {
                val m = ms.optJSONObject(i) ?: continue
                labels.add(m.optString("label"))
                avgs.add((m.opt("avg") as? Number)?.toFloat() ?: Float.NaN)
                wins.add((m.opt("win_rate") as? Number)?.toFloat())
            }
        }
        if (labels.isNotEmpty()) SeasonalityChart(labels, avgs, wins)
        Text(se.optString("reading"), color = Muted, fontSize = Type.label, lineHeight = 15.sp,
            modifier = Modifier.padding(top = 6.dp))
        TextButton(onClick = { UiNav.open(UiNav.Screen.DeepDive(res.optString("symbol"))) },
            contentPadding = PaddingValues(0.dp)) {
            Text("Full cyclicality & deep dive →", color = AccentHi, fontSize = Type.secondary)
        }
    }

    // The evidence, so the judgement can be checked rather than trusted.
    res.optJSONObject("entry")?.let { e ->
        Spacer(Modifier.height(10.dp))
        Text("PRICE MODEL", color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        Text("CMP ₹${fmtNum(e.opt("current"))} · 50-DMA ₹${fmtNum(e.opt("dma50"))} · " +
            "200-DMA ₹${fmtNum(e.opt("dma200"))} · RSI ${fmtNum(e.opt("rsi"))}\n" +
            "entry zone ₹${fmtNum(e.opt("entry_low"))}–${fmtNum(e.opt("entry_high"))}",
            color = OnBg.copy(alpha = 0.85f), fontSize = Type.secondary, lineHeight = 18.sp)
    }
    arr(res, "reports")?.takeIf { it.length() > 0 }?.let { rep ->
        Spacer(Modifier.height(10.dp))
        Text("FILINGS READ", color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        for (i in 0 until rep.length())
            Text("• " + (rep.optJSONObject(i)?.optString("title") ?: ""),
                color = Muted, fontSize = Type.label, maxLines = 2, lineHeight = 15.sp)
    }
    arr(res, "news")?.takeIf { it.length() > 0 }?.let { nw ->
        Spacer(Modifier.height(10.dp))
        Text("NEWS USED", color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        for (i in 0 until minOf(nw.length(), 6)) {
            val n = nw.optJSONObject(i) ?: continue
            Text("• ${n.optString("title")}  (${n.optString("source")})",
                color = Muted, fontSize = Type.label, maxLines = 2, lineHeight = 15.sp)
        }
    }
    arr(res, "events")?.takeIf { it.length() > 0 }?.let { ev ->
        Spacer(Modifier.height(10.dp))
        Text("EVENTS AHEAD", color = Muted, fontSize = Type.caps, letterSpacing = 0.6.sp)
        for (i in 0 until ev.length()) {
            val e = ev.optJSONObject(i) ?: continue
            Text("• ${e.optString("date")} — ${e.optString("title")}",
                color = Warn, fontSize = Type.label, lineHeight = 15.sp)
        }
    }
    arr(res, "gaps")?.takeIf { it.length() > 0 }?.let { g ->
        Spacer(Modifier.height(10.dp))
        // Naming what couldn't be gathered matters: "no bad news found" and
        // "the news lookup failed" are very different things.
        Text("Couldn't gather: " + (0 until g.length()).joinToString(", ") { g.optString(it) },
            color = Warn, fontSize = Type.label, lineHeight = 15.sp)
    }
}

/** One engine's paper track record. */
@Composable
private fun EngineScoreRow(r: JSONObject) {
    val pnl = (r.opt("total_pnl") as? Number)?.toDouble() ?: 0.0
    val col = if (pnl >= 0) Bull else Bear
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(r.optString("label"), color = OnBg, fontSize = Type.body,
                fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Text("₹" + fmtCompact(r.opt("total_pnl")), color = col,
                fontSize = Type.body, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.height(3.dp))
        val ret = (r.opt("return_pct") as? Number)?.toDouble()
        val hit = (r.opt("hit_rate_pct") as? Number)?.toDouble()
        Text(listOfNotNull(
            "${r.optInt("n_total")} position(s)",
            "₹${fmtCompact(r.opt("invested"))} put in",
            ret?.let { "%.2f%% return".format(it) },
            hit?.let { "%.0f%% hit rate (%d closed)".format(it, r.optInt("n_closed")) },
        ).joinToString("  ·  "), color = Muted, fontSize = Type.label)
        Divider(color = BorderCol.copy(alpha = 0.5f), modifier = Modifier.padding(top = 8.dp))
    }
}

@Composable
private fun PositionRow(p: JSONObject) {
    val pnl = (p.opt("pnl") as? Number)?.toDouble() ?: 0.0
    val col = if (pnl >= 0) Bull else Bear
    Column(
        Modifier.fillMaxWidth().padding(vertical = 5.dp)
            .clip(RoundedCornerShape(Radius.control)).background(Panel2)
            .border(1.dp, BorderCol.copy(alpha = 0.6f), RoundedCornerShape(Radius.control))
            .padding(Space.md)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(p.optString("symbol"), color = OnBg, fontSize = Type.body,
                    fontWeight = FontWeight.Bold)
                Text("${p.optInt("qty")} sh @ ₹${fmtNum(p.opt("entry_price"))} · " +
                    "${p.optInt("held_days")}d · ${p.optString("source")}",
                    color = Muted, fontSize = Type.label)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text("₹" + fmtCompact(p.opt("pnl")), color = col,
                    fontSize = Type.heading, fontWeight = FontWeight.Bold)
                Text("%.2f%%".format((p.opt("pnl_pct") as? Number)?.toDouble() ?: 0.0),
                    color = col, fontSize = Type.secondary)
            }
        }
        if (!p.optBoolean("priced", true)) {
            Spacer(Modifier.height(6.dp))
            Text("No live price right now — shown at cost.", color = Warn, fontSize = Type.label)
        }
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { GhostBus.sell(p.optString("id")) },
                contentPadding = PaddingValues(0.dp)) {
                Text("Sell at market", color = Bear, fontSize = Type.secondary)
            }
            Spacer(Modifier.weight(1f))
            TextButton(onClick = { UiNav.open(UiNav.Screen.DeepDive(p.optString("symbol"))) },
                contentPadding = PaddingValues(0.dp)) {
                Text("Deep dive", color = AccentHi, fontSize = Type.secondary)
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
            Text(s.optString("symbol"), color = OnBg, fontSize = Type.body,
                fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            val g = (s.opt("gain_pct") as? Number)?.toDouble() ?: 0.0
            Text("%+.2f%%".format(g), color = if (g >= 0) Bull else Bear, fontSize = Type.secondary)
        }
        Spacer(Modifier.height(4.dp))
        Text("RSI ${fmtNum(s.opt("rsi"))} · 50DMA ₹${fmtNum(s.opt("dma50"))} · " +
            "200DMA ₹${fmtNum(s.opt("dma200"))}", color = Muted, fontSize = Type.label)
        if (n > 0) {
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                for (i in 0 until minOf(n, 3)) Pill(flags!!.optString(i), col)
            }
        }
        arr(s, "reasons")?.let { rs ->
            Spacer(Modifier.height(6.dp))
            for (i in 0 until rs.length())
                Text("• " + rs.optString(i), color = Muted, fontSize = Type.secondary,
                    lineHeight = 18.sp, modifier = Modifier.padding(vertical = 1.dp))
        }
        Divider(color = BorderCol.copy(alpha = 0.5f),
            modifier = Modifier.padding(top = 8.dp))
    }
}
