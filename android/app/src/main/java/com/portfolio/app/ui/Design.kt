package com.portfolio.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * The design system.
 *
 * The UI had grown 18 distinct font sizes, 134 of them at 10-11sp. A section
 * heading, a body sentence and a footnote were all within a point of each
 * other, so nothing read as more important than anything else and the whole
 * app felt like one undifferentiated wall of grey text. Below about 11sp text
 * also stops being comfortably readable on a phone held at arm's length.
 *
 * So: six type roles, one spacing scale, and a small set of primitives every
 * screen is built from. Fewer decisions per screen, and consistency by
 * construction rather than by discipline.
 */
object Space {
    val xs: Dp = 4.dp
    val sm: Dp = 8.dp
    val md: Dp = 12.dp
    val lg: Dp = 16.dp
    val xl: Dp = 24.dp
    /** Gutter between the screen edge and content. */
    val edge: Dp = 16.dp
}

object Radius {
    val card = 16.dp
    val control = 10.dp
    val pill = 999.dp
}

/**
 * Type roles. Deliberately few — if a new size seems necessary, the layout
 * usually needs rethinking rather than another size.
 */
object Type {
    // Big numbers: portfolio value, a headline P&L.
    val display = 30.sp
    val displayWeight = FontWeight.Bold
    // Screen titles.
    val title = 21.sp
    // Card headings.
    val heading = 15.sp
    // Default reading size. Nothing load-bearing should be smaller.
    val body = 14.sp
    // Secondary text: supporting sentences, metadata.
    val secondary = 12.5.sp
    // Labels above values, table headers, timestamps.
    val label = 11.sp
}

// ── Primitives ──────────────────────────────────────────────────────────────

/**
 * A content card. One visual unit per idea.
 *
 * `accent` is a thin leading bar rather than a tinted background: colour reads
 * as meaning (bull/bear/warning) and a filled card would make every surface
 * compete for attention.
 */
@Composable
fun Card(
    title: String? = null,
    subtitle: String? = null,
    accent: Color = AccentHi,
    trailing: @Composable (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = Space.edge, vertical = Space.sm)
            .clip(RoundedCornerShape(Radius.card))
            .background(Panel)
            .border(1.dp, BorderCol, RoundedCornerShape(Radius.card))
            .padding(Space.lg)
    ) {
        if (title != null) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(3.dp, 18.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(accent)
                )
                Spacer(Modifier.width(Space.md))
                Column(Modifier.weight(1f)) {
                    Text(title, color = OnBg, fontSize = Type.heading,
                        fontWeight = FontWeight.SemiBold, maxLines = 2,
                        overflow = TextOverflow.Ellipsis)
                    if (subtitle != null) {
                        Text(subtitle, color = Muted, fontSize = Type.label,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                if (trailing != null) trailing()
            }
            Spacer(Modifier.height(Space.md))
        }
        content()
    }
}

/** Explanatory text under a card heading. The default voice of the app. */
@Composable
fun Lede(text: String, modifier: Modifier = Modifier) {
    Text(text, color = Muted, fontSize = Type.secondary, lineHeight = 18.sp,
        modifier = modifier)
}

/** A quiet footnote: provenance, caveats, timestamps. */
@Composable
fun Note(text: String, color: Color = Muted, modifier: Modifier = Modifier) {
    Text(text, color = color, fontSize = Type.label, lineHeight = 15.sp,
        modifier = modifier)
}

/**
 * One number with its label. The label sits ABOVE in small caps and the value
 * is large — so the eye lands on the number, which is what people came for.
 */
@Composable
fun Metric(
    label: String,
    value: String,
    color: Color = OnBg,
    sub: String? = null,
    modifier: Modifier = Modifier,
) {
    Column(modifier) {
        Text(label.uppercase(), color = Muted, fontSize = 9.5.sp,
            letterSpacing = 0.8.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Spacer(Modifier.height(3.dp))
        Text(value, color = color, fontSize = 19.sp, fontWeight = FontWeight.Bold,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (sub != null) {
            Text(sub, color = Muted, fontSize = 10.5.sp, maxLines = 1,
                overflow = TextOverflow.Ellipsis)
        }
    }
}

/** Metrics in a tile grid. Two per row reads better than three on a phone. */
@Composable
fun MetricGrid(items: List<Triple<String, String, Color>>, perRow: Int = 2) {
    Column(verticalArrangement = Arrangement.spacedBy(Space.sm)) {
        items.chunked(perRow).forEach { row ->
            Row(Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(Space.sm)) {
                row.forEach { (label, value, color) ->
                    Box(
                        Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(Radius.control))
                            .background(Panel2)
                            .padding(horizontal = Space.md, vertical = Space.md)
                    ) { Metric(label, value, color) }
                }
                repeat(perRow - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}

/** Status / inline message. Tinted to its meaning, never shouty. */
@Composable
fun Banner(text: String, color: Color = Muted, modifier: Modifier = Modifier) {
    Row(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(Radius.control))
            .background(color.copy(alpha = 0.10f))
            .border(1.dp, color.copy(alpha = 0.28f), RoundedCornerShape(Radius.control))
            .padding(Space.md)
    ) {
        Text(text, color = color.copy(alpha = 0.95f), fontSize = Type.secondary,
            lineHeight = 18.sp)
    }
}

/** Small rounded tag. */
@Composable
fun Tag(text: String, color: Color) {
    Box(
        Modifier
            .clip(RoundedCornerShape(Radius.pill))
            .background(color.copy(alpha = 0.14f))
            .border(1.dp, color.copy(alpha = 0.35f), RoundedCornerShape(Radius.pill))
            .padding(horizontal = 10.dp, vertical = 3.dp)
    ) {
        Text(text, color = color, fontSize = Type.label, fontWeight = FontWeight.SemiBold)
    }
}

/**
 * The main action of a card. One per card — if a card needs three equal
 * buttons it is really three cards.
 */
@Composable
fun PrimaryAction(
    text: String,
    onClick: () -> Unit,
    enabled: Boolean = true,
    busy: Boolean = false,
    color: Color = Accent,
) {
    Button(
        onClick = onClick,
        enabled = enabled && !busy,
        modifier = Modifier.fillMaxWidth().height(46.dp),
        shape = RoundedCornerShape(Radius.control),
        colors = ButtonDefaults.buttonColors(containerColor = color),
    ) {
        if (busy) {
            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp,
                color = Color.White)
            Spacer(Modifier.width(Space.sm))
        }
        Text(text, fontSize = Type.body, fontWeight = FontWeight.SemiBold)
    }
}

/** Secondary action — same height, lower weight. */
@Composable
fun SecondaryAction(
    text: String,
    onClick: () -> Unit,
    enabled: Boolean = true,
    modifier: Modifier = Modifier,
) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.height(40.dp),
        shape = RoundedCornerShape(Radius.control),
        contentPadding = PaddingValues(horizontal = Space.md),
    ) { Text(text, fontSize = Type.secondary) }
}

/**
 * Nothing here yet.
 *
 * An empty screen should say what it is for and what to press — a blank panel
 * reads as a broken app, and this one has a lot of screens that are empty until
 * something long has been run.
 */
@Composable
fun EmptyState(
    icon: String,
    title: String,
    body: String,
    actionText: String? = null,
    onAction: (() -> Unit)? = null,
) {
    Column(
        Modifier.fillMaxWidth().padding(horizontal = Space.lg, vertical = Space.xl),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(icon, fontSize = 30.sp)
        Spacer(Modifier.height(Space.md))
        Text(title, color = OnBg, fontSize = Type.heading,
            fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(Space.sm))
        Text(body, color = Muted, fontSize = Type.secondary, lineHeight = 19.sp,
            modifier = Modifier.fillMaxWidth(0.9f),
            textAlign = androidx.compose.ui.text.style.TextAlign.Center)
        if (actionText != null && onAction != null) {
            Spacer(Modifier.height(Space.lg))
            SecondaryAction(actionText, onAction)
        }
    }
}

/** Row of selectable chips — the standard way to switch a view's scope. */
@Composable
fun <T> ChipRow(
    options: List<Pair<T, String>>,
    selected: T,
    onSelect: (T) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(Space.sm)) {
        options.forEach { (value, label) ->
            FilterChip(
                selected = selected == value,
                onClick = { onSelect(value) },
                label = { Text(label, fontSize = Type.secondary) },
                shape = RoundedCornerShape(Radius.pill),
            )
        }
    }
}

/** A tappable row that discloses more. Used instead of nesting everything. */
@Composable
fun DisclosureRow(
    label: String,
    value: String? = null,
    expanded: Boolean,
    onToggle: () -> Unit,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().clickable { onToggle() }
                .padding(vertical = Space.sm),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(label, color = OnBg, fontSize = Type.body,
                modifier = Modifier.weight(1f))
            if (value != null) {
                Text(value, color = Muted, fontSize = Type.secondary)
                Spacer(Modifier.width(Space.sm))
            }
            Text(if (expanded) "▲" else "▼", color = Muted, fontSize = 10.sp)
        }
        if (expanded) content()
    }
}
