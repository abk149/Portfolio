package com.portfolio.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.nativeCanvas
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

/** One line on a [LineChart]. */
data class ChartSeries(
    val values: List<Float>,
    val label: String,
    val color: Color,
    val dashed: Boolean = false,
    val filled: Boolean = false,
)

/**
 * Multi-series line chart with a real y-axis (gridlines + ₹ tick labels).
 * Used for portfolio value vs invested capital vs the buy-and-hold
 * counterfactual. All series are NaN-tolerant and share one y-scale.
 */
@Composable
fun LineChart(series: List<ChartSeries>, modifier: Modifier = Modifier) {
    val live = series.filter { it.values.count { v -> v.isFinite() } >= 2 }
    if (live.isEmpty()) {
        Text("No equity curve yet.", color = Muted, fontSize = 12.sp); return
    }
    val finite = live.flatMap { it.values }.filter { it.isFinite() }
    val ticks = niceTicks(finite.min(), finite.max())
    val lo = ticks.first(); val hi = ticks.last()
    val range = (hi - lo).takeIf { it.isFinite() && it > 0f } ?: 1f
    // x is indexed by the longest series so they stay aligned in time.
    val n = live.maxOf { it.values.size }

    Column(modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            live.forEach { LegendDot(it.color, it.label) }
        }
        Spacer(Modifier.height(8.dp))
        Canvas(Modifier.fillMaxWidth().height(220.dp)) {
            val padL = 54.dp.toPx(); val padR = 6.dp.toPx()
            val padT = 8.dp.toPx(); val padB = 8.dp.toPx()
            val w = size.width - padL - padR
            val h = size.height - padT - padB
            fun px(i: Int) = padL + if (n <= 1) 0f else i / (n - 1f) * w
            fun py(v: Float) = padT + h - ((v - lo) / range) * h

            val label = android.graphics.Paint().apply {
                color = android.graphics.Color.argb(190, 139, 148, 158)
                textSize = 9.sp.toPx(); isAntiAlias = true
            }
            ticks.forEach { t ->
                val y = py(t)
                drawLine(BorderCol.copy(alpha = 0.4f), Offset(padL, y), Offset(padL + w, y), 1f)
                drawContext.canvas.nativeCanvas.drawText(fmtINR(t), 2f, y + label.textSize / 3f, label)
            }

            live.forEach { s ->
                val pts = s.values.mapIndexedNotNull { i, v -> if (v.isFinite()) i to v else null }
                if (pts.size < 2) return@forEach
                if (s.filled) {
                    val fill = Path().apply {
                        moveTo(px(pts.first().first), padT + h)
                        pts.forEach { (i, v) -> lineTo(px(i), py(v)) }
                        lineTo(px(pts.last().first), padT + h); close()
                    }
                    drawPath(fill, Brush.verticalGradient(
                        listOf(s.color.copy(alpha = 0.28f), s.color.copy(alpha = 0.02f))))
                }
                val line = Path().apply {
                    pts.forEachIndexed { k, (i, v) ->
                        if (k == 0) moveTo(px(i), py(v)) else lineTo(px(i), py(v))
                    }
                }
                drawPath(line, s.color, style = Stroke(
                    width = if (s.filled) 3f else 2.5f,
                    pathEffect = if (s.dashed)
                        androidx.compose.ui.graphics.PathEffect.dashPathEffect(floatArrayOf(10f, 8f))
                    else null))
            }
        }
    }
}

/**
 * Efficient-frontier chart (x = volatility %, y = expected return %).
 *
 * Proper chart furniture: padded plot area, gridlines with numeric tick labels
 * on both axes, a SMOOTHED efficient envelope (Catmull-Rom spline), your
 * holdings as small dots, and clearly distinguished Current vs Optimal markers.
 */
@Composable
fun FrontierChart(
    frontier: List<Pair<Float, Float>>,          // (vol%, ret%)
    holdings: List<Pair<Float, Float>> = emptyList(),
    current: Pair<Float, Float>? = null,
    optimal: Pair<Float, Float>? = null,
    modifier: Modifier = Modifier,
) {
    // Efficient (upper) envelope only: sort by vol, keep running-max return.
    // Drops the inefficient lower branch and solver noise.
    val eff = run {
        var best = Float.NEGATIVE_INFINITY
        frontier.filter { it.first.isFinite() && it.second.isFinite() }
            .sortedBy { it.first }
            .filter { (_, r) -> if (r >= best - 1e-4f) { best = maxOf(best, r); true } else false }
    }
    val all = eff + holdings + listOfNotNull(current, optimal)
    if (all.size < 2) {
        Text("Not enough data for the frontier.", color = Muted, fontSize = 12.sp); return
    }

    // Padded, "nice" axis ranges so nothing sits on the edge.
    val xTicks = niceTicks(all.minOf { it.first }, all.maxOf { it.first })
    val yTicks = niceTicks(all.minOf { it.second }, all.maxOf { it.second })
    val xlo = xTicks.first(); val xhi = xTicks.last()
    val ylo = yTicks.first(); val yhi = yTicks.last()
    val xr = (xhi - xlo).takeIf { it > 0f } ?: 1f
    val yr = (yhi - ylo).takeIf { it > 0f } ?: 1f

    Column(modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            LegendDot(AccentHi, "Frontier"); LegendDot(Muted, "Holdings")
            LegendDot(Bear, "Current"); LegendDot(Bull, "Optimal")
        }
        Spacer(Modifier.height(8.dp))
        Canvas(Modifier.fillMaxWidth().height(240.dp)) {
            val padL = 42.dp.toPx(); val padR = 10.dp.toPx()
            val padT = 10.dp.toPx(); val padB = 26.dp.toPx()
            val plotW = size.width - padL - padR
            val plotH = size.height - padT - padB
            fun px(x: Float) = padL + (x - xlo) / xr * plotW
            fun py(y: Float) = padT + plotH - (y - ylo) / yr * plotH

            val label = android.graphics.Paint().apply {
                color = android.graphics.Color.argb(190, 139, 148, 158)
                textSize = 9.sp.toPx(); isAntiAlias = true
            }

            // Horizontal gridlines + y tick labels
            yTicks.forEach { t ->
                val y = py(t)
                drawLine(BorderCol.copy(alpha = 0.45f), Offset(padL, y),
                    Offset(padL + plotW, y), 1f)
                drawContext.canvas.nativeCanvas.drawText(
                    "%.0f%%".format(t), 4f, y + label.textSize / 3f, label)
            }
            // Vertical gridlines + x tick labels
            label.textAlign = android.graphics.Paint.Align.CENTER
            xTicks.forEach { t ->
                val x = px(t)
                drawLine(BorderCol.copy(alpha = 0.3f), Offset(x, padT),
                    Offset(x, padT + plotH), 1f)
                drawContext.canvas.nativeCanvas.drawText(
                    "%.0f".format(t), x, size.height - 6f, label)
            }
            label.textAlign = android.graphics.Paint.Align.LEFT

            // Smoothed efficient frontier (Catmull-Rom → cubic Bezier)
            if (eff.size >= 2) {
                val pts = eff.map { Offset(px(it.first), py(it.second)) }
                val path = Path().apply {
                    moveTo(pts[0].x, pts[0].y)
                    for (i in 0 until pts.size - 1) {
                        val p0 = pts[if (i - 1 >= 0) i - 1 else 0]
                        val p1 = pts[i]; val p2 = pts[i + 1]
                        val p3 = pts[if (i + 2 <= pts.size - 1) i + 2 else pts.size - 1]
                        cubicTo(
                            p1.x + (p2.x - p0.x) / 6f, p1.y + (p2.y - p0.y) / 6f,
                            p2.x - (p3.x - p1.x) / 6f, p2.y - (p3.y - p1.y) / 6f,
                            p2.x, p2.y,
                        )
                    }
                }
                drawPath(path, AccentHi, style = Stroke(width = 3.5f))
            }

            // Holdings
            holdings.forEach { (x, y) ->
                drawCircle(Muted.copy(alpha = 0.75f), 4f, Offset(px(x), py(y)))
            }
            // Current — hollow ring so it reads differently from Optimal
            current?.let {
                val c = Offset(px(it.first), py(it.second))
                drawCircle(Bear, 7.5f, c, style = Stroke(width = 3f))
            }
            // Optimal — filled dot with a soft halo
            optimal?.let {
                val c = Offset(px(it.first), py(it.second))
                drawCircle(Bull.copy(alpha = 0.25f), 12f, c)
                drawCircle(Bull, 6f, c)
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("↑ expected return %", color = Muted, fontSize = 10.sp)
            Text("volatility % →", color = Muted, fontSize = 10.sp)
        }
    }
}

/** Human-friendly tick values spanning [lo, hi], padded outward by ~8%. */
private fun niceTicks(lo: Float, hi: Float, target: Int = 5): List<Float> {
    if (!lo.isFinite() || !hi.isFinite()) return listOf(0f, 1f)
    var a = lo.toDouble(); var b = hi.toDouble()
    if (b - a < 1e-6) { a -= 1.0; b += 1.0 }
    val pad = (b - a) * 0.08
    a -= pad; b += pad
    val raw = (b - a) / target
    val exp = kotlin.math.floor(kotlin.math.log10(raw))
    val base = Math.pow(10.0, exp)
    val frac = raw / base
    val step = base * when {
        frac <= 1.0 -> 1.0
        frac <= 2.0 -> 2.0
        frac <= 5.0 -> 5.0
        else -> 10.0
    }
    val start = kotlin.math.floor(a / step) * step
    val end = kotlin.math.ceil(b / step) * step
    val out = ArrayList<Float>()
    var v = start
    var guard = 0
    while (v <= end + step * 0.5 && guard++ < 40) { out.add(v.toFloat()); v += step }
    return if (out.size >= 2) out else listOf(lo, hi)
}

/** Generic scatter — used for the Universe Map (tech vs fundamental score). */
/**
 * One dot on the universe map.
 *
 * Axis order matches the web dashboard deliberately — x is the balance sheet,
 * y is the chart. Two surfaces of the same product showing the same map
 * transposed would be worse than either choice on its own.
 */
data class UniversePoint(
    val x: Float,          // fundamental score, 0..100
    val y: Float,          // technical score, 0..100
    val color: Color,
    val symbol: String = "",
)

/** The four corners of the map. */
enum class Quadrant(val label: String, val blurb: String, val tint: Color) {
    LEADERS("Leaders", "Strong books, strong chart", Bull),
    MOMENTUM("Momentum only", "Weak books, strong chart", Warn),
    OUT_OF_FAVOUR("Out of favour", "Strong books, weak chart", AccentHi),
    WEAK("Weak on both", "Weak books, weak chart", Bear),
}

/** Which quadrant a point falls in. x = fundamental, y = technical. */
fun quadrantOf(fund: Float, tech: Float, mid: Float = 50f): Quadrant = when {
    fund >= mid && tech >= mid -> Quadrant.LEADERS
    fund < mid && tech >= mid -> Quadrant.MOMENTUM
    fund >= mid && tech < mid -> Quadrant.OUT_OF_FAVOUR
    else -> Quadrant.WEAK
}

/**
 * Universe map: fundamental score (y) against technical score (x), split into
 * four labelled quadrants.
 *
 * The axes are pinned to the full 0-100 score range rather than scaled to the
 * data. That matters: quadrants only mean something if the dividing line sits
 * at a fixed score, and a fixed frame also makes two builds comparable — under
 * auto-scaling the same stock could drift across a boundary just because the
 * rest of the universe moved.
 */
@Composable
fun QuadrantScatterChart(
    points: List<UniversePoint>,
    xLabel: String,
    yLabel: String,
    mid: Float = 50f,
    highlight: Quadrant? = null,
    modifier: Modifier = Modifier,
) {
    if (points.isEmpty()) {
        Text("No universe data — build the map first.", color = Muted, fontSize = 12.sp); return
    }
    val counts = remember(points) { points.groupingBy { quadrantOf(it.x, it.y, mid) }.eachCount() }

    Column(modifier.fillMaxWidth()) {
        Canvas(Modifier.fillMaxWidth().height(300.dp)) {
            val padL = 30.dp.toPx(); val padR = 8.dp.toPx()
            val padT = 8.dp.toPx(); val padB = 22.dp.toPx()
            val w = size.width - padL - padR
            val h = size.height - padT - padB
            fun px(v: Float) = padL + (v / 100f).coerceIn(0f, 1f) * w
            fun py(v: Float) = padT + h - (v / 100f).coerceIn(0f, 1f) * h
            val mx = px(mid); val my = py(mid)

            // Quadrant washes — faint, so the dots stay the subject.
            fun wash(q: Quadrant, l: Float, t: Float, r: Float, b: Float) {
                val a = if (highlight == null || highlight == q) 0.10f else 0.03f
                drawRect(q.tint.copy(alpha = a), Offset(l, t), Size(r - l, b - t))
            }
            wash(Quadrant.MOMENTUM, padL, padT, mx, my)          // low fund, high tech
            wash(Quadrant.LEADERS, mx, padT, padL + w, my)        // high fund, high tech
            wash(Quadrant.WEAK, padL, my, mx, padT + h)           // low fund, low tech
            wash(Quadrant.OUT_OF_FAVOUR, mx, my, padL + w, padT + h)

            val axis = android.graphics.Paint().apply {
                color = android.graphics.Color.argb(170, 139, 148, 158)
                textSize = 9.sp.toPx(); isAntiAlias = true
            }
            val tag = android.graphics.Paint().apply {
                color = android.graphics.Color.argb(210, 139, 148, 158)
                textSize = 9.5.sp.toPx(); isAntiAlias = true
                isFakeBoldText = true
            }

            // Gridlines + ticks at 0/25/50/75/100 on both axes.
            listOf(0f, 25f, 50f, 75f, 100f).forEach { t ->
                val y = py(t); val x = px(t)
                drawLine(BorderCol.copy(alpha = 0.25f), Offset(padL, y), Offset(padL + w, y), 1f)
                drawLine(BorderCol.copy(alpha = 0.25f), Offset(x, padT), Offset(x, padT + h), 1f)
                drawContext.canvas.nativeCanvas.drawText(
                    "%.0f".format(t), 2f, y + axis.textSize / 3f, axis)
                if (t > 0f) drawContext.canvas.nativeCanvas.drawText(
                    "%.0f".format(t), x - axis.measureText("%.0f".format(t)) / 2f,
                    size.height - 10f, axis)
            }

            // The dividing lines themselves, drawn stronger than the grid.
            drawLine(Muted.copy(alpha = 0.55f), Offset(mx, padT), Offset(mx, padT + h), 2f)
            drawLine(Muted.copy(alpha = 0.55f), Offset(padL, my), Offset(padL + w, my), 2f)

            // Corner labels, inset so they never sit under the dividing lines.
            val inset = 6.dp.toPx()
            fun corner(q: Quadrant, right: Boolean, top: Boolean) {
                val n = counts[q] ?: 0
                val text = "${q.label} · $n"
                val tw = tag.measureText(text)
                val x = if (right) padL + w - inset - tw else padL + inset
                val y = if (top) padT + inset + tag.textSize else padT + h - inset
                tag.color = android.graphics.Color.argb(
                    if (highlight == null || highlight == q) 235 else 90,
                    (q.tint.red * 255).toInt(), (q.tint.green * 255).toInt(),
                    (q.tint.blue * 255).toInt())
                drawContext.canvas.nativeCanvas.drawText(text, x, y, tag)
            }
            corner(Quadrant.LEADERS, right = true, top = true)
            corner(Quadrant.MOMENTUM, right = false, top = true)
            corner(Quadrant.OUT_OF_FAVOUR, right = true, top = false)
            corner(Quadrant.WEAK, right = false, top = false)

            points.forEach { p ->
                val dim = highlight != null && quadrantOf(p.x, p.y, mid) != highlight
                drawCircle(p.color.copy(alpha = if (dim) 0.15f else 0.8f), 4.5f,
                    Offset(px(p.x), py(p.y)))
            }

            // Frame last, so it sits over the washes.
            drawLine(BorderCol, Offset(padL, padT + h), Offset(padL + w, padT + h), 1.5f)
            drawLine(BorderCol, Offset(padL, padT), Offset(padL, padT + h), 1.5f)
        }
        Row(Modifier.fillMaxWidth().padding(top = 2.dp),
            horizontalArrangement = Arrangement.SpaceBetween) {
            Text("↑ $yLabel", color = Muted, fontSize = 10.sp)
            Text("$xLabel →", color = Muted, fontSize = 10.sp)
        }
        Spacer(Modifier.height(8.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            LegendDot(Bull, "Buy"); LegendDot(Warn, "Hold"); LegendDot(Bear, "Avoid")
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

/**
 * Paired bar chart for month-by-month "you vs the index".
 *
 * Bars are signed around a zero line — a losing month must read as a bar going
 * DOWN, not a short bar going up, or the comparison is misleading at a glance.
 * Labels are thinned when they'd collide.
 */
@Composable
fun BarPairChart(
    labels: List<String>,
    seriesA: List<Float>,
    seriesB: List<Float>,
    labelA: String,
    labelB: String,
    colorA: Color = AccentHi,
    colorB: Color = Muted,
    modifier: Modifier = Modifier,
) {
    val n = minOf(labels.size, seriesA.size, seriesB.size)
    if (n == 0) {
        Text("No monthly data yet.", color = Muted, fontSize = 12.sp); return
    }
    val all = (seriesA.take(n) + seriesB.take(n)).filter { it.isFinite() }
    if (all.isEmpty()) {
        Text("No monthly data yet.", color = Muted, fontSize = 12.sp); return
    }
    // Symmetric-ish scale that always includes zero.
    val ticks = niceTicks(minOf(all.min(), 0f), maxOf(all.max(), 0f))
    val lo = ticks.first(); val hi = ticks.last()
    val range = (hi - lo).takeIf { it > 0f } ?: 1f

    Column(modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            LegendDot(colorA, labelA); LegendDot(colorB, labelB)
        }
        Spacer(Modifier.height(8.dp))
        Canvas(Modifier.fillMaxWidth().height(190.dp)) {
            val padL = 44.dp.toPx(); val padR = 4.dp.toPx()
            val padT = 8.dp.toPx(); val padB = 20.dp.toPx()
            val w = size.width - padL - padR
            val h = size.height - padT - padB
            fun py(v: Float) = padT + h - ((v - lo) / range) * h
            val slot = w / n
            val barW = (slot * 0.34f).coerceAtMost(14.dp.toPx())

            val paint = android.graphics.Paint().apply {
                color = android.graphics.Color.argb(190, 139, 148, 158)
                textSize = 9.sp.toPx(); isAntiAlias = true
            }
            ticks.forEach { t ->
                val y = py(t)
                drawLine(BorderCol.copy(alpha = if (t == 0f) 0.9f else 0.35f),
                    Offset(padL, y), Offset(padL + w, y), if (t == 0f) 1.5f else 1f)
                drawContext.canvas.nativeCanvas.drawText(
                    "%.0f%%".format(t), 2f, y + paint.textSize / 3f, paint)
            }

            val zeroY = py(0f)
            // Thin x labels so they never overlap on a narrow phone.
            val every = kotlin.math.ceil(n / 6.0).toInt().coerceAtLeast(1)
            for (i in 0 until n) {
                val cx = padL + slot * (i + 0.5f)
                listOf(seriesA[i] to colorA, seriesB[i] to colorB)
                    .forEachIndexed { k, (v, c) ->
                        if (!v.isFinite()) return@forEachIndexed
                        val x = cx + (if (k == 0) -barW - 1.dp.toPx() else 1.dp.toPx())
                        val y = py(v)
                        drawRect(c,
                            topLeft = Offset(x, kotlin.math.min(y, zeroY)),
                            size = Size(barW, kotlin.math.abs(y - zeroY).coerceAtLeast(1.5f)))
                    }
                if (i % every == 0) {
                    val t = labels[i]
                    drawContext.canvas.nativeCanvas.drawText(
                        t, cx - paint.measureText(t) / 2f, size.height - 4f, paint)
                }
            }
        }
    }
}
