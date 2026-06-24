package com.portfolio.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// A categorical palette that reads well on the dark theme.
private val ChartColors = listOf(
    Color(0xFF58A6FF), Color(0xFF3FB950), Color(0xFFD29922), Color(0xFFF85149),
    Color(0xFFA371F7), Color(0xFF39C5CF), Color(0xFFDB6D28), Color(0xFFE85AAD),
    Color(0xFF6CB6FF), Color(0xFF7EE787),
)

private fun fmtINR(v: Float): String =
    if (v >= 1e7) "₹%.2fCr".format(v / 1e7)
    else if (v >= 1e5) "₹%.2fL".format(v / 1e5)
    else if (v >= 1e3) "₹%.1fK".format(v / 1e3)
    else "₹%.0f".format(v)

/**
 * Donut/pie chart with a legend — mirrors the web dashboard's allocation chart.
 * [slices] is (label, value); values are summed for the percentages.
 */
@Composable
fun DonutChart(slices: List<Pair<String, Float>>, modifier: Modifier = Modifier) {
    val data = slices.filter { it.second > 0f }.sortedByDescending { it.second }
    if (data.isEmpty()) {
        Text("No allocation data.", color = Muted, fontSize = 12.sp); return
    }
    val total = data.sumOf { it.second.toDouble() }.toFloat().coerceAtLeast(1e-9f)

    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Canvas(Modifier.size(150.dp).padding(8.dp)) {
            val stroke = size.minDimension * 0.18f
            val inset = stroke / 2f
            val arcSize = Size(size.width - stroke, size.height - stroke)
            var start = -90f
            data.forEachIndexed { i, (_, v) ->
                val sweep = v / total * 360f
                drawArc(
                    color = ChartColors[i % ChartColors.size],
                    startAngle = start, sweepAngle = sweep - 1.2f, useCenter = false,
                    topLeft = Offset(inset, inset), size = arcSize,
                    style = Stroke(width = stroke),
                )
                start += sweep
            }
        }
        Column(Modifier.weight(1f).padding(start = 8.dp)) {
            data.take(8).forEachIndexed { i, (label, v) ->
                Row(verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(vertical = 2.dp)) {
                    Box(Modifier.size(10.dp).clip(RoundedCornerShape(2.dp))
                        .background(ChartColors[i % ChartColors.size]))
                    Spacer(Modifier.width(8.dp))
                    Text(label, color = OnBg, fontSize = 12.sp,
                        modifier = Modifier.weight(1f), maxLines = 1)
                    Text("%.1f%%".format(v / total * 100), color = Muted, fontSize = 12.sp)
                }
            }
        }
    }
}

/**
 * Two-series line chart (portfolio value vs invested) — mirrors the web
 * dashboard's equity curve. [primary] is drawn filled; [secondary] dashed.
 */
@Composable
fun LineChart(
    primary: List<Float>,
    secondary: List<Float>? = null,
    primaryLabel: String = "Portfolio",
    secondaryLabel: String = "Invested",
    modifier: Modifier = Modifier,
) {
    val pts = primary.filter { it.isFinite() }
    val sec = secondary?.filter { it.isFinite() }
    if (pts.size < 2) {
        Text("No equity curve yet.", color = Muted, fontSize = 12.sp); return
    }
    // Range from FINITE values only — a single NaN in the invested series would
    // otherwise make min/max NaN and blank the whole chart.
    val all = pts + (sec ?: emptyList())
    val lo = all.min()
    val hi = all.max()
    val range = (hi - lo).takeIf { it.isFinite() && it > 0f } ?: 1f

    Column(modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            LegendDot(AccentHi, primaryLabel)
            if (secondary != null) LegendDot(Muted, secondaryLabel)
            Spacer(Modifier.weight(1f))
            Text(fmtINR(hi), color = Muted, fontSize = 11.sp)
        }
        Spacer(Modifier.height(6.dp))
        Canvas(Modifier.fillMaxWidth().height(200.dp)) {
            val w = size.width; val h = size.height
            fun x(i: Int, n: Int) = if (n <= 1) 0f else i / (n - 1f) * w
            fun y(v: Float) = h - ((v - lo) / range) * h

            // grid baseline
            drawLine(BorderCol, Offset(0f, h - 1), Offset(w, h - 1), 1f)

            // primary: gradient fill + line
            val fill = Path().apply {
                moveTo(0f, h)
                pts.forEachIndexed { i, v -> lineTo(x(i, pts.size), y(v)) }
                lineTo(w, h); close()
            }
            drawPath(fill, Brush.verticalGradient(
                listOf(AccentHi.copy(alpha = 0.30f), AccentHi.copy(alpha = 0.02f))))
            val line = Path().apply {
                pts.forEachIndexed { i, v ->
                    val px = x(i, pts.size); val py = y(v)
                    if (i == 0) moveTo(px, py) else lineTo(px, py)
                }
            }
            drawPath(line, AccentHi, style = Stroke(width = 3f))

            // secondary: dashed muted line (already NaN-filtered into `sec`)
            if (sec != null && sec.size >= 2) {
                val p2 = Path().apply {
                    sec.forEachIndexed { i, v ->
                        val px = x(i, sec.size); val py = y(v)
                        if (i == 0) moveTo(px, py) else lineTo(px, py)
                    }
                }
                drawPath(p2, Muted, style = Stroke(
                    width = 2f,
                    pathEffect = androidx.compose.ui.graphics.PathEffect.dashPathEffect(
                        floatArrayOf(10f, 8f))))
            }
        }
        Text(fmtINR(lo), color = Muted, fontSize = 11.sp)
    }
}

/**
 * Efficient-frontier scatter (x = volatility %, y = expected return %).
 * Plots the frontier line, each holding as a dot, the current portfolio (◆),
 * and the optimal portfolio (★). Mirrors the web dashboard's frontier chart.
 */
@Composable
fun FrontierChart(
    frontier: List<Pair<Float, Float>>,          // (vol%, ret%)
    holdings: List<Pair<Float, Float>> = emptyList(),
    current: Pair<Float, Float>? = null,
    optimal: Pair<Float, Float>? = null,
    modifier: Modifier = Modifier,
) {
    val all = frontier + holdings + listOfNotNull(current, optimal)
    if (all.size < 2) { Text("Not enough data for the frontier.", color = Muted, fontSize = 12.sp); return }
    val xs = all.map { it.first }; val ys = all.map { it.second }
    val xlo = xs.min(); val xhi = xs.max(); val ylo = ys.min(); val yhi = ys.max()
    val xr = (xhi - xlo).takeIf { it > 0 } ?: 1f
    val yr = (yhi - ylo).takeIf { it > 0 } ?: 1f

    Column(modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            LegendDot(AccentHi, "Frontier"); LegendDot(Muted, "Holdings")
            LegendDot(Bear, "Current"); LegendDot(Bull, "Optimal")
        }
        Spacer(Modifier.height(6.dp))
        Canvas(Modifier.fillMaxWidth().height(220.dp)) {
            val w = size.width; val h = size.height; val pad = 8f
            fun px(x: Float) = pad + (x - xlo) / xr * (w - 2 * pad)
            fun py(y: Float) = h - pad - (y - ylo) / yr * (h - 2 * pad)
            drawLine(BorderCol, Offset(0f, h - 1), Offset(w, h - 1), 1f)

            // Draw the EFFICIENT (upper) envelope only: sort by volatility, then
            // keep a point only if its return beats every lower-vol point
            // (running max). This drops the inefficient lower branch and the
            // solver's noisy interior points → a clean, monotone, smooth curve.
            val eff = run {
                var best = Float.NEGATIVE_INFINITY
                frontier.sortedBy { it.first }.filter { (_, r) ->
                    if (r >= best - 1e-4f) { best = maxOf(best, r); true } else false
                }
            }
            if (eff.size >= 2) {
                val path = Path()
                eff.forEachIndexed { i, (x, y) ->
                    if (i == 0) path.moveTo(px(x), py(y)) else path.lineTo(px(x), py(y))
                }
                drawPath(path, AccentHi, style = Stroke(width = 3f))
            }
            holdings.forEach { (x, y) -> drawCircle(Muted, 4f, Offset(px(x), py(y))) }
            current?.let { drawCircle(Bear, 7f, Offset(px(it.first), py(it.second))) }
            optimal?.let { drawCircle(Bull, 7f, Offset(px(it.first), py(it.second))) }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("vol %", color = Muted, fontSize = 10.sp)
            Text("return % (↑)", color = Muted, fontSize = 10.sp)
        }
    }
}

/** Generic scatter — used for the Universe Map (tech vs fundamental score). */
@Composable
fun ScatterChart(
    points: List<Triple<Float, Float, Color>>,    // x, y, color
    xLabel: String, yLabel: String,
    modifier: Modifier = Modifier,
) {
    if (points.size < 2) { Text("No universe data — build the map first.", color = Muted, fontSize = 12.sp); return }
    val xs = points.map { it.first }; val ys = points.map { it.second }
    val xlo = xs.min(); val xhi = xs.max(); val ylo = ys.min(); val yhi = ys.max()
    val xr = (xhi - xlo).takeIf { it > 0 } ?: 1f
    val yr = (yhi - ylo).takeIf { it > 0 } ?: 1f
    Column(modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            LegendDot(Bull, "Buy"); LegendDot(Warn, "Hold"); LegendDot(Bear, "Avoid")
        }
        Spacer(Modifier.height(6.dp))
        Canvas(Modifier.fillMaxWidth().height(260.dp)) {
            val w = size.width; val h = size.height; val pad = 10f
            drawLine(BorderCol, Offset(0f, h - 1), Offset(w, h - 1), 1f)
            drawLine(BorderCol, Offset(1f, 0f), Offset(1f, h), 1f)
            points.forEach { (x, y, c) ->
                val px = pad + (x - xlo) / xr * (w - 2 * pad)
                val py = h - pad - (y - ylo) / yr * (h - 2 * pad)
                drawCircle(c.copy(alpha = 0.85f), 4.5f, Offset(px, py))
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("↑ $yLabel", color = Muted, fontSize = 10.sp)
            Text("$xLabel →", color = Muted, fontSize = 10.sp)
        }
    }
}

@Composable
private fun LegendDot(color: Color, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(9.dp).clip(RoundedCornerShape(2.dp)).background(color))
        Spacer(Modifier.width(5.dp))
        Text(label, color = Muted, fontSize = 11.sp)
    }
}
