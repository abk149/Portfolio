package com.portfolio.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.portfolio.app.net.Api
import org.json.JSONArray
import org.json.JSONObject

// ─────────────────────────────────────────────────────────────────────────────
// HIDDEN GEMS — bases + accumulation + fundamental inflection, before the run
// ─────────────────────────────────────────────────────────────────────────────

/**
 * One checklist entry from the engine ("OBV accumulation", "Earnings
 * acceleration", …). Every field is optional on the wire — parse defensively.
 */
private class GemSignal(
    val label: String,
    val value: String,
    val passed: Boolean,
    val weight: Int,
    val detail: String,
)

/** One gem pick, already defensively parsed out of the raw JSON. */
private class Gem(
    val symbol: String,
    val name: String,
    val sector: String,
    val price: Double?,
    val score: Double,
    val verdict: String,
    val baseMonths: Int?,
    val baseDepthPct: Double?,
    val volContraction: Boolean?,
    val dist52wHighPct: Double?,
    val dist52wLowPct: Double?,
    val ret6mPct: Double?,
    val ret12mPct: Double?,
    val relStrength3m: Double?,
    val technical: List<GemSignal>,
    val fundamental: List<GemSignal>,
    val summary: String,
)

private fun num(o: JSONObject, key: String): Double? =
    (o.opt(key) as? Number)?.toDouble()

private fun text(o: JSONObject, key: String): String =
    o.optString(key, "").takeIf { it.isNotBlank() && it != "null" } ?: ""

private fun parseSignals(arr: JSONArray?): List<GemSignal> {
    if (arr == null) return emptyList()
    val out = ArrayList<GemSignal>(arr.length())
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        out.add(GemSignal(
            label = text(o, "label").ifBlank { text(o, "key").ifBlank { "Signal" } },
            value = text(o, "value"),
            passed = o.optBoolean("passed", false),
            weight = (o.opt("weight") as? Number)?.toInt() ?: 0,
            detail = text(o, "detail"),
        ))
    }
    return out
}

private fun parseGems(arr: JSONArray?): List<Gem> {
    if (arr == null) return emptyList()
    val out = ArrayList<Gem>(arr.length())
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        val base = o.optJSONObject("base")
        out.add(Gem(
            symbol = text(o, "symbol").ifBlank { "—" },
            name = text(o, "name"),
            sector = text(o, "sector"),
            price = num(o, "price"),
            score = num(o, "gem_score") ?: 0.0,
            verdict = text(o, "verdict").ifBlank { "WATCH" },
            baseMonths = base?.let { (it.opt("months") as? Number)?.toInt() },
            baseDepthPct = base?.let { num(it, "depth_pct") },
            volContraction = base?.takeIf { it.has("volatility_contraction") && !it.isNull("volatility_contraction") }
                ?.optBoolean("volatility_contraction"),
            dist52wHighPct = num(o, "distance_from_52w_high_pct"),
            dist52wLowPct = num(o, "distance_from_52w_low_pct"),
            ret6mPct = num(o, "return_6m_pct"),
            ret12mPct = num(o, "return_12m_pct"),
            relStrength3m = num(o, "relative_strength_3m"),
            technical = parseSignals(o.optJSONArray("technical_signals")),
            fundamental = parseSignals(o.optJSONArray("fundamental_signals")),
            summary = text(o, "summary"),
        ))
    }
    // Best candidates first — the engine should already sort, but don't rely on it.
    return out.sortedByDescending { it.score }
}

private fun verdictColor(verdict: String): Color = when (verdict.uppercase()) {
    "STRONG_GEM" -> Bull
    "GEM" -> AccentHi
    else -> Warn          // WATCH and anything unexpected
}

private fun verdictLabel(verdict: String): String =
    verdict.uppercase().replace("_", " ").ifBlank { "WATCH" }

private fun pct(v: Double): String = "%+.1f%%".format(v)

/**
 * Small score ring in the Charts.kt idiom — a Canvas arc, no library.
 * Colour follows the verdict so the strongest reads at a glance.
 */
@Composable
private fun ScoreRing(score: Double, color: Color, modifier: Modifier = Modifier) {
    Box(modifier.size(60.dp), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = 5.5.dp.toPx()
            val inset = stroke / 2f
            val arcSize = Size(size.width - stroke, size.height - stroke)
            drawArc(
                color = BorderCol, startAngle = -90f, sweepAngle = 360f,
                useCenter = false, topLeft = Offset(inset, inset), size = arcSize,
                style = Stroke(width = stroke),
            )
            drawArc(
                color = color, startAngle = -90f,
                sweepAngle = (score.coerceIn(0.0, 100.0) / 100.0 * 360.0).toFloat(),
                useCenter = false, topLeft = Offset(inset, inset), size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Round),
            )
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("%.0f".format(score), color = color, fontSize = 16.sp,
                fontWeight = FontWeight.Bold)
            Text("GEM", color = Muted, fontSize = 7.sp, letterSpacing = 0.5.sp)
        }
    }
}

@Composable
fun GemsScreen() {
    val job = JobBus.state("gems")

    // Seed from the backend's cached last scan, so opening the tab is instant.
    LaunchedEffect(Unit) {
        if (BackendBus.running && job.result == null && !job.running) {
            Api.gemsLast().objOrNull()
                ?.takeIf { it.has("gems") }
                ?.let { JobBus.seed("gems", it) }
        }
    }

    ScreenScaffold(title = "Hidden gems", loading = job.running, onRefresh = null) {
        if (!BackendBus.running) { BackendOfflineHint(); return@ScreenScaffold }

        SectionCard("Scan for hidden gems", AccentHi) {
            Text("Names that haven't had their bull run yet — long bases, far " +
                "from their 52-week highs — but showing early accumulation and " +
                "a fundamental inflection.",
                color = Muted, fontSize = 12.sp, lineHeight = 17.sp)
            Spacer(Modifier.height(10.dp))
            Button(
                onClick = {
                    JobBus.run("gems",
                        "Scanning the universe for quiet accumulation…") {
                        Api.gemsScan()
                    }
                },
                enabled = !job.running, modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Bull),
            ) { Text(if (job.running) "Scanning…" else "▶ Scan for gems") }
            job.status?.let {
                Spacer(Modifier.height(8.dp))
                StatusBanner(it, if (job.running) Warn else Bear)
            }
        }

        val res = job.result
        if (res == null) {
            SectionCard("No scan yet", Muted) {
                Text("Run a scan to surface stocks quietly building a base " +
                    "before the crowd notices. The last result is kept on the " +
                    "backend, so re-opening this tab is instant after the first run.",
                    color = Muted, fontSize = 12.sp, lineHeight = 17.sp)
            }
            return@ScreenScaffold
        }

        val gems = remember(res) { parseGems(arr(res, "gems")) }

        SectionCard("Last scan", AccentHi) {
            KpiGrid(listOf(
                Triple("Scanned", fmtNum(res.opt("scanned")), OnBg),
                Triple("Gems found", fmtNum(gems.size), if (gems.isEmpty()) Muted else Bull),
                Triple("Took", num(res, "elapsed_sec")?.let { "%.1fs".format(it) } ?: "—", AccentHi),
            ))
            text(res, "generated_at").takeIf { it.isNotBlank() }?.let {
                Spacer(Modifier.height(8.dp))
                Text("Generated ${it.replace("T", " ").take(19)}",
                    color = Muted, fontSize = 10.sp)
            }
        }

        if (gems.isEmpty()) {
            SectionCard("No gems this time", Warn) {
                Text("The scan completed but nothing passed every filter. " +
                    "That is the engine being picky, not broken — bases with " +
                    "accumulation AND a fundamental turn are rare by design.",
                    color = Muted, fontSize = 12.sp, lineHeight = 17.sp)
            }
        } else {
            SectionCard("Hidden gems", Bull) {
                Text("Ranked by gem score. Tap a card for the full signal " +
                    "checklist and thesis.", color = Muted, fontSize = 11.sp)
                Spacer(Modifier.height(4.dp))
                gems.forEach { GemCard(it) }
            }
        }
    }
}

/** One gem pick as an expandable card: headline stats, then the checklists. */
@Composable
private fun GemCard(gem: Gem) {
    var expanded by remember { mutableStateOf(false) }
    val vColor = verdictColor(gem.verdict)

    Column(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Panel2)
            .border(1.dp, BorderCol.copy(alpha = 0.7f), RoundedCornerShape(12.dp))
            .clickable { expanded = !expanded }
            .padding(14.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            ScoreRing(gem.score, vColor)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(gem.symbol, color = OnBg, fontSize = 16.sp,
                    fontWeight = FontWeight.Bold, maxLines = 1,
                    overflow = TextOverflow.Ellipsis)
                if (gem.name.isNotBlank() && gem.name != gem.symbol)
                    Text(gem.name, color = Muted, fontSize = 11.5.sp, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
                if (gem.sector.isNotBlank())
                    Text(gem.sector, color = Muted, fontSize = 11.sp, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
            }
            Column(horizontalAlignment = Alignment.End) {
                Pill(verdictLabel(gem.verdict), vColor)
                gem.price?.let {
                    Spacer(Modifier.height(6.dp))
                    Text("₹" + fmtNum(it), color = OnBg, fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold)
                }
            }
        }

        // Base & momentum stats — only the ones the engine actually returned.
        val stats = listOfNotNull(
            gem.baseMonths?.let { "Base" to "${it}mo" },
            gem.baseDepthPct?.let { "Base depth" to "%.1f%%".format(it) },
            gem.volContraction?.let { "Vol contraction" to if (it) "yes" else "no" },
            gem.dist52wHighPct?.let { "vs 52w high" to pct(it) },
            gem.dist52wLowPct?.let { "vs 52w low" to pct(it) },
            gem.ret6mPct?.let { "6m" to pct(it) },
            gem.ret12mPct?.let { "12m" to pct(it) },
            gem.relStrength3m?.let { "RS 3m" to pct(it) },
        )
        if (stats.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                stats.forEach { (label, value) ->
                    Column {
                        Text(label.uppercase(), color = Muted, fontSize = 9.5.sp,
                            letterSpacing = 0.5.sp, maxLines = 1)
                        Spacer(Modifier.height(2.dp))
                        Text(value, color = OnBg, fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold, maxLines = 1)
                    }
                }
            }
        }

        if (!expanded) {
            Spacer(Modifier.height(10.dp))
            Text("Tap for signals & thesis  ▼", color = Muted, fontSize = 10.sp)
        } else {
            SignalSection("Technical signals", gem.technical, AccentHi)
            SignalSection("Fundamental signals", gem.fundamental, Bull)
            if (gem.summary.isNotBlank()) {
                Spacer(Modifier.height(12.dp))
                Text("THESIS", color = Muted, fontSize = 9.5.sp, letterSpacing = 0.6.sp)
                Spacer(Modifier.height(4.dp))
                Text(gem.summary, color = OnBg.copy(alpha = 0.9f),
                    fontSize = 12.5.sp, lineHeight = 18.sp)
            }
            Spacer(Modifier.height(10.dp))
            Text("Tap to collapse  ▲", color = Muted, fontSize = 10.sp)
        }
    }
}

/** One labelled checklist — "Technical signals" or "Fundamental signals". */
@Composable
private fun SignalSection(title: String, signals: List<GemSignal>, accent: Color) {
    if (signals.isEmpty()) return
    Spacer(Modifier.height(12.dp))
    Text(title.uppercase(), color = accent, fontSize = 10.sp,
        fontWeight = FontWeight.SemiBold, letterSpacing = 0.6.sp)
    Spacer(Modifier.height(6.dp))
    signals.forEach { s ->
        Column(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(if (s.passed) "✓" else "✗",
                    color = if (s.passed) Bull else Bear,
                    fontSize = 13.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.width(8.dp))
                Text(s.label, color = OnBg, fontSize = 12.5.sp,
                    fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
                if (s.value.isNotBlank())
                    Text(s.value, color = if (s.passed) Bull else Muted,
                        fontSize = 11.5.sp, maxLines = 1)
                if (s.weight > 0) {
                    Spacer(Modifier.width(8.dp))
                    Text("w${s.weight}", color = Muted, fontSize = 10.sp)
                }
            }
            if (s.detail.isNotBlank()) {
                Spacer(Modifier.height(2.dp))
                Text(s.detail, color = Muted, fontSize = 11.sp, lineHeight = 15.sp,
                    modifier = Modifier.padding(start = 21.dp))
            }
        }
    }
}
