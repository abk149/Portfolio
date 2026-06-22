package com.portfolio.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs

// ── formatting ──────────────────────────────────────────────────────────────
fun fmtNum(v: Any?): String = when (v) {
    null, JSONObject.NULL -> "—"
    is Int -> "%,d".format(v)
    is Long -> "%,d".format(v)
    is Double -> if (v == v.toLong().toDouble()) "%,d".format(v.toLong())
                 else "%,.2f".format(v)
    is Number -> v.toString()
    else -> v.toString()
}

private fun numericSign(v: Any?): Double? = when (v) {
    is Number -> v.toDouble()
    is String -> v.replace(",", "").replace("%", "").replace("₹", "").trim().toDoubleOrNull()
    else -> null
}

private fun isSignedKey(key: String): Boolean {
    val k = key.lowercase()
    return k.contains("pnl") || k.contains("change") || k.contains("pct") ||
        k.contains("return") || k.contains("gain") || k.contains("day")
}

// ── building blocks ───────────────────────────────────────────────────────────
@Composable
fun SectionCard(
    title: String? = null,
    accent: Color = AccentHi,
    trailing: @Composable (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp)
            .background(Panel, RoundedCornerShape(12.dp))
            .border(1.dp, BorderCol, RoundedCornerShape(12.dp))
            .padding(14.dp)
    ) {
        if (title != null) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    title.uppercase(), color = accent, fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold, letterSpacing = 0.6.sp,
                    modifier = Modifier.weight(1f),
                )
                if (trailing != null) trailing()
            }
            Spacer(Modifier.height(10.dp))
        }
        content()
    }
}

@Composable
fun Pill(text: String, color: Color) {
    Box(
        Modifier
            .background(color.copy(alpha = 0.15f), RoundedCornerShape(999.dp))
            .padding(horizontal = 10.dp, vertical = 4.dp)
    ) { Text(text, color = color, fontSize = 11.sp, fontWeight = FontWeight.SemiBold) }
}

@Composable
fun StatusBanner(text: String, color: Color = Muted) {
    Box(
        Modifier
            .fillMaxWidth()
            .background(color.copy(alpha = 0.10f), RoundedCornerShape(8.dp))
            .padding(10.dp)
    ) { Text(text, color = color, fontSize = 12.sp) }
}

/** KPI tiles laid out in a flowing 2-up grid. */
@Composable
fun KpiGrid(pairs: List<Triple<String, String, Color>>) {
    val rows = pairs.chunked(2)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        rows.forEach { row ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { (label, value, color) ->
                    Column(
                        Modifier
                            .weight(1f)
                            .background(Panel2, RoundedCornerShape(10.dp))
                            .padding(12.dp)
                    ) {
                        Text(label.uppercase(), color = Muted, fontSize = 10.sp, letterSpacing = 0.5.sp)
                        Spacer(Modifier.height(4.dp))
                        Text(value, color = color, fontSize = 18.sp, fontWeight = FontWeight.Bold,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

/**
 * Generic native table from a JSON array of objects. Columns are inferred from
 * the keys of the first row. Numeric P&L-ish columns are colored green/red.
 * Horizontally scrollable so wide tables stay usable on a phone.
 */
@Composable
fun DataTable(arr: JSONArray?, maxRows: Int = 60) {
    if (arr == null || arr.length() == 0) {
        Text("No data.", color = Muted, fontSize = 12.sp); return
    }
    val first = arr.optJSONObject(0) ?: run {
        Text("No data.", color = Muted, fontSize = 12.sp); return
    }
    val cols = ArrayList<String>(); first.keys().forEach { cols.add(it) }
    val n = minOf(arr.length(), maxRows)

    Column(Modifier.horizontalScroll(rememberScrollState())) {
        // header
        Row(
            Modifier
                .background(Panel2, RoundedCornerShape(6.dp))
                .padding(vertical = 6.dp)
        ) {
            cols.forEach { c ->
                Text(
                    c, color = Muted, fontSize = 11.sp, fontWeight = FontWeight.Medium,
                    modifier = Modifier.width(cellWidth(c)).padding(horizontal = 8.dp),
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
        for (i in 0 until n) {
            val row = arr.optJSONObject(i) ?: continue
            Row(Modifier.padding(vertical = 5.dp)) {
                cols.forEachIndexed { idx, c ->
                    val raw = if (row.isNull(c)) null else row.opt(c)
                    val signed = if (isSignedKey(c)) numericSign(raw) else null
                    val color = when {
                        signed == null -> OnBg
                        signed > 0 -> Bull
                        signed < 0 -> Bear
                        else -> OnBg
                    }
                    Text(
                        text = fmtNum(raw),
                        color = color, fontSize = 12.sp,
                        textAlign = if (idx == 0) TextAlign.Start else TextAlign.End,
                        modifier = Modifier.width(cellWidth(c)).padding(horizontal = 8.dp),
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                        fontFamily = if (idx == 0) FontFamily.Default else FontFamily.Monospace,
                    )
                }
            }
            Divider(color = BorderCol.copy(alpha = 0.4f), thickness = 0.5.dp)
        }
        if (arr.length() > n) {
            Spacer(Modifier.height(6.dp))
            Text("… ${arr.length() - n} more rows", color = Muted, fontSize = 11.sp)
        }
    }
}

private fun cellWidth(key: String): androidx.compose.ui.unit.Dp {
    val k = key.lowercase()
    return when {
        k.contains("symbol") || k.contains("name") || k.contains("ticker") -> 120.dp
        k.contains("sector") || k.contains("industry") || k.contains("reco") -> 130.dp
        else -> 92.dp
    }
}
