package com.portfolio.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.PieChart
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.activity.compose.BackHandler

// Settings lives in the top bar rather than the bottom nav: adding Calendar to
// a six-item bar would have squeezed every label, and Settings is the one
// destination you visit rarely.
private enum class Dest(val label: String, val icon: ImageVector) {
    HOME("Portfolio", Icons.Filled.PieChart),
    IDEAS("Ideas", Icons.Filled.AutoAwesome),
    CALENDAR("Calendar", Icons.Filled.Event),
    QUANT("DR-Quant", Icons.Filled.Insights),
    MAP("U-Map", Icons.Filled.Map),
    ANALYSIS("Analysis", Icons.Filled.ShowChart),
}

/**
 * The app shell.
 *
 * Everything that used to be a modal popup — deep dive, chat, terminal,
 * settings, login, event impact — is now an ordinary destination rendered in
 * this Scaffold's content area. Consequences that matter: the bottom navigation
 * and the activity bar stay live, long analyses no longer lock the UI, and you
 * can leave a running task and come back to it.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppRoot() {
    var dest by remember { mutableStateOf(Dest.HOME) }
    val overlay = UiNav.overlay

    // Hardware/gesture back closes a secondary screen instead of the app.
    BackHandler(enabled = overlay != null) { UiNav.close() }

    Scaffold(
        containerColor = Bg,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Panel, titleContentColor = OnBg),
                navigationIcon = {
                    if (overlay != null) {
                        IconButton(onClick = { UiNav.close() }) {
                            Icon(Icons.Filled.ArrowBack, contentDescription = "Back", tint = OnBg)
                        }
                    }
                },
                title = {
                    if (overlay != null) {
                        Text(UiNav.title, fontSize = 16.sp, fontWeight = FontWeight.Bold,
                            maxLines = 1)
                    } else {
                        val (label, col) = when (BackendBus.state.value) {
                            BackendBus.State.RUNNING -> "Engine live" to Bull
                            BackendBus.State.STARTING -> "Starting…" to Warn
                            BackendBus.State.ERROR -> "Engine error" to Bear
                            else -> "Engine off" to Muted
                        }
                        Column {
                            Text("Portfolio Quant", fontSize = 18.sp, fontWeight = FontWeight.Bold)
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("●", color = col, fontSize = 10.sp)
                                Spacer(Modifier.width(5.dp))
                                Text(label, color = Muted, fontSize = 11.sp)
                            }
                        }
                    }
                },
                actions = {
                    IconButton(onClick = { UiNav.open(UiNav.Screen.Settings) }) {
                        Icon(Icons.Filled.Settings, contentDescription = "Settings", tint = Muted)
                    }
                    IconButton(onClick = { UiNav.open(UiNav.Screen.Chat) }) {
                        Icon(Icons.Filled.Chat, contentDescription = "AI Assistant", tint = AccentHi)
                    }
                    IconButton(onClick = { UiNav.open(UiNav.Screen.Terminal) }) {
                        Icon(Icons.Filled.List, contentDescription = "Terminal", tint = AccentHi)
                    }
                    IconButton(onClick = { UiNav.open(UiNav.Screen.Login) }) {
                        Icon(Icons.Filled.Lock, contentDescription = "Login", tint = Bull)
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar(containerColor = Panel) {
                Dest.values().forEach { d ->
                    NavigationBarItem(
                        selected = dest == d && overlay == null,
                        // Tapping a tab also leaves whatever secondary screen is
                        // open — the nav bar stays usable at all times.
                        onClick = { UiNav.close(); dest = d },
                        icon = { Icon(d.icon, contentDescription = d.label) },
                        label = { Text(d.label, maxLines = 1) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = AccentHi, selectedTextColor = AccentHi,
                            indicatorColor = Panel2, unselectedIconColor = Muted,
                            unselectedTextColor = Muted,
                        ),
                    )
                }
            }
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            // Persistent, embedded progress for everything in flight.
            ActivityBar()
            Box(Modifier.fillMaxSize()) {
                when (overlay) {
                    null -> when (dest) {
                        Dest.HOME -> HomeScreen()
                        Dest.IDEAS -> ThemesScreen()
                        Dest.CALENDAR -> CalendarScreen()
                        Dest.QUANT -> QuantScreen()
                        Dest.MAP -> MapScreen()
                        Dest.ANALYSIS -> AnalysisScreen()
                    }
                    is UiNav.Screen.DeepDive -> DeepDiveScreen(overlay.symbol)
                    is UiNav.Screen.EventImpact -> EventImpactScreen(overlay.event)
                    UiNav.Screen.Chat -> ChatScreen()
                    UiNav.Screen.Terminal -> TerminalScreen()
                    UiNav.Screen.Settings -> SettingsScreen(
                        openLogin = { UiNav.open(UiNav.Screen.Login) })
                    UiNav.Screen.Login -> LoginScreen()
                }
            }
        }
    }
}
