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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
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
    is Double -> if (v == v.toLong().toDouble()) "%,d".format(v.toLong()) else "%,.2f".format(v)
    is Number -> v.toString()
    else -> v.toString()
}

/** Compact money/large-number formatting so table cells stay narrow. */
fun fmtCompact(v: Any?): String {
    val d = (v as? Number)?.toDouble() ?: return fmtNum(v)
    val a = abs(d)
    return when {
        a >= 1e7 -> "%.2fCr".format(d / 1e7)
        a >= 1e5 -> "%.2fL".format(d / 1e5)
        a >= 1e3 -> "%,.0f".format(d)
        a == a.toLong().toDouble() -> "%,d".format(d.toLong())
        else -> "%,.2f".format(d)
    }
}

private fun numeric(v: Any?): Double? = when (v) {
    is Number -> v.toDouble()
    is String -> v.replace(",", "").replace("%", "").replace("₹", "").trim().toDoubleOrNull()
    else -> null
}

private fun isSignedKey(k: String): Boolean {
    val s = k.lowercase()
    return s.contains("pnl") || s.contains("change") || s.contains("pct") ||
        s.contains("return") || s.contains("gain") || s == "day"
}

private fun isMoneyKey(k: String): Boolean {
    val s = k.lowercase()
    return s.contains("value") || s.contains("invested") || s.contains("price") ||
        s.contains("pnl") || s.contains("cap") || s.contains("amount")
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
            .padding(horizontal = 14.dp, vertical = 7.dp)
            .clip(RoundedCornerShape(14.dp))
            .background(Panel)
            .border(1.dp, BorderCol, RoundedCornerShape(14.dp))
            .padding(16.dp)
    ) {
        if (title != null) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(3.dp, 14.dp).clip(RoundedCornerShape(2.dp)).background(accent))
                Spacer(Modifier.width(8.dp))
                Text(
                    title.uppercase(), color = OnBg.copy(alpha = 0.92f), fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold, letterSpacing = 0.7.sp,
                    modifier = Modifier.weight(1f),
                )
                if (trailing != null) trailing()
            }
            Spacer(Modifier.height(12.dp))
        }
        content()
    }
}

@Composable
fun Pill(text: String, color: Color) {
    Box(
        Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(color.copy(alpha = 0.14f))
            .border(1.dp, color.copy(alpha = 0.35f), RoundedCornerShape(999.dp))
            .padding(horizontal = 10.dp, vertical = 4.dp)
    ) { Text(text, color = color, fontSize = 11.sp, fontWeight = FontWeight.SemiBold) }
}

@Composable
fun StatusBanner(text: String, color: Color = Muted) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(color.copy(alpha = 0.10f))
            .border(1.dp, color.copy(alpha = 0.25f), RoundedCornerShape(10.dp))
            .padding(12.dp)
    ) { Text(text, color = color.copy(alpha = 0.95f), fontSize = 12.sp, lineHeight = 17.sp) }
}

/** KPI tiles in a flowing 2-up grid. */
@Composable
fun KpiGrid(pairs: List<Triple<String, String, Color>>) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        pairs.chunked(2).forEach { row ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                row.forEach { (label, value, color) ->
                    Column(
                        Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(12.dp))
                            .background(Panel2)
                            .border(1.dp, BorderCol.copy(alpha = 0.6f), RoundedCornerShape(12.dp))
                            .padding(horizontal = 13.dp, vertical = 11.dp)
                    ) {
                        Text(label.uppercase(), color = Muted, fontSize = 10.sp, letterSpacing = 0.6.sp,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Spacer(Modifier.height(5.dp))
                        Text(value, color = color, fontSize = 19.sp, fontWeight = FontWeight.Bold,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

/**
 * Generic native table from a JSON array of objects. Columns inferred from the
 * first row. Money columns use compact (L/Cr) formatting to stay narrow; P&L-ish
 * columns are colored; rows are zebra-striped. Horizontally scrollable, with a
 * swipe hint when wide.
 */
@Composable
fun DataTable(arr: JSONArray?, maxRows: Int = 60) {
    if (arr == null || arr.length() == 0) {
        Text("No data.", color = Muted, fontSize = 12.sp); return
    }
    val first = arr.optJSONObject(0) ?: run { Text("No data.", color = Muted, fontSize = 12.sp); return }
    val cols = ArrayList<String>(); first.keys().forEach { cols.add(it) }
    val n = minOf(arr.length(), maxRows)

    val rowH = 38.dp
    val headerH = 32.dp
    fun zebra(i: Int) = if (i % 2 == 1) Color.White.copy(alpha = 0.025f) else Color.Transparent

    // Pick a human-readable identifier for the sticky column (company/symbol),
    // never ISIN / instrument token.
    val idCol = cols.firstOrNull { val k = it.lowercase(); k.contains("company") || k == "name" || k.endsWith("_name") }
        ?: cols.firstOrNull { val k = it.lowercase(); k.contains("symbol") || k.contains("ticker") }
        ?: cols.firstOrNull { val k = it.lowercase(); !k.contains("isin") && !k.contains("token") && !k.contains("instrument") }
        ?: cols.first()
    val rest = cols.filter { it != idCol }

    if (cols.size > 4) {
        Text("Swipe values sideways →", color = Muted, fontSize = 10.sp)
        Spacer(Modifier.height(4.dp))
    }
    // Sticky first column (the identifier) + horizontally-scrollable values, so
    // the symbol stays visible while you scan the numbers.
    Row(Modifier.clip(RoundedCornerShape(8.dp)).border(1.dp, BorderCol, RoundedCornerShape(8.dp))) {
        Column(Modifier.background(Panel2.copy(alpha = 0.5f))) {
            Box(Modifier.width(cellWidth(idCol)).height(headerH).background(Panel2)
                .padding(horizontal = 10.dp), contentAlignment = Alignment.CenterStart) {
                Text(prettyHeader(idCol), color = Muted, fontSize = 11.sp,
                    fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            for (i in 0 until n) {
                val row = arr.optJSONObject(i)
                Box(Modifier.width(cellWidth(idCol)).height(rowH).background(zebra(i))
                    .padding(horizontal = 10.dp), contentAlignment = Alignment.CenterStart) {
                    Text(fmtNum(row?.opt(idCol)), color = OnBg, fontSize = 12.sp,
                        fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
        Column(Modifier.horizontalScroll(rememberScrollState())) {
            Row(Modifier.height(headerH).background(Panel2)) {
                rest.forEach { c ->
                    Box(Modifier.width(cellWidth(c)).height(headerH).padding(horizontal = 8.dp),
                        contentAlignment = Alignment.CenterEnd) {
                        Text(prettyHeader(c), color = Muted, fontSize = 11.sp,
                            fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            for (i in 0 until n) {
                val row = arr.optJSONObject(i)
                Row(Modifier.height(rowH).background(zebra(i))) {
                    rest.forEach { c ->
                        val raw = if (row == null || row.isNull(c)) null else row.opt(c)
                        val signed = if (isSignedKey(c)) numeric(raw) else null
                        val color = when {
                            signed == null -> OnBg.copy(alpha = 0.9f)
                            signed > 0 -> Bull; signed < 0 -> Bear; else -> OnBg
                        }
                        val text = if (numeric(raw) != null && isMoneyKey(c)) fmtCompact(raw) else fmtNum(raw)
                        Box(Modifier.width(cellWidth(c)).height(rowH).padding(horizontal = 8.dp),
                            contentAlignment = Alignment.CenterEnd) {
                            Text(text, color = color, fontSize = 12.sp, maxLines = 1,
                                overflow = TextOverflow.Ellipsis, fontFamily = FontFamily.Monospace)
                        }
                    }
                }
            }
        }
    }
    if (arr.length() > n) {
        Spacer(Modifier.height(6.dp))
        Text("… ${arr.length() - n} more rows", color = Muted, fontSize = 11.sp)
    }
}

private fun prettyHeader(key: String): String =
    key.replace("_", " ").replace("pct", "%").trim()
        .replaceFirstChar { it.uppercase() }

private fun cellWidth(key: String): Dp {
    val k = key.lowercase()
    return when {
        k.contains("symbol") || k.contains("name") || k.contains("ticker") -> 108.dp
        k.contains("sector") || k.contains("industry") || k.contains("reco") -> 124.dp
        k.contains("date") -> 96.dp
        else -> 78.dp
    }
}
