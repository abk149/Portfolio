package com.portfolio.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Trading-desk palette — mirrors the web dashboard so the phone feels like
// the same product (GitHub-dark base + bull/bear accents).
val Bg        = Color(0xFF0B0E14)
val Panel     = Color(0xFF161B22)
val Panel2    = Color(0xFF1C2330)
val BorderCol = Color(0xFF2A3140)
val OnBg      = Color(0xFFE6EDF3)
val Muted     = Color(0xFF8B949E)
val Accent    = Color(0xFF2F81F7)
val AccentHi  = Color(0xFF58A6FF)
val Bull      = Color(0xFF3FB950)
val Bear      = Color(0xFFF85149)
val Warn      = Color(0xFFD29922)

private val DarkColors = darkColorScheme(
    primary        = AccentHi,
    onPrimary      = Color.White,
    secondary      = Bull,
    onSecondary    = Color.Black,
    background      = Bg,
    onBackground    = OnBg,
    surface         = Panel,
    onSurface       = OnBg,
    surfaceVariant  = Panel2,
    onSurfaceVariant = Muted,
    outline         = BorderCol,
    error           = Bear,
)

@Composable
fun PortfolioTheme(content: @Composable () -> Unit) {
    // Always dark — a trading app shouldn't blind you in a dark room.
    MaterialTheme(colorScheme = DarkColors, content = content)
}
