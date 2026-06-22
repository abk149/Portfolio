package com.portfolio.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.PieChart
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties

private enum class Dest(val label: String, val icon: ImageVector) {
    HOME("Portfolio", Icons.Filled.PieChart),
    QUANT("DR-Quant", Icons.Filled.Insights),
    MAP("U-Map", Icons.Filled.Map),
    ANALYSIS("Analysis", Icons.Filled.ShowChart),
    SETTINGS("Settings", Icons.Filled.Settings),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppRoot() {
    var dest by remember { mutableStateOf(Dest.HOME) }
    var showLogin by remember { mutableStateOf(false) }
    var showTerminal by remember { mutableStateOf(false) }
    var showChat by remember { mutableStateOf(false) }

    Scaffold(
        containerColor = Bg,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Panel, titleContentColor = OnBg),
                title = { Text("Portfolio Quant") },
                actions = {
                    val (dot, col) = when (BackendBus.state.value) {
                        BackendBus.State.RUNNING -> "●" to Bull
                        BackendBus.State.STARTING -> "●" to Warn
                        BackendBus.State.ERROR -> "●" to Bear
                        else -> "●" to Muted
                    }
                    Text(dot, color = col, modifier = Modifier.padding(end = 4.dp))
                    IconButton(onClick = { showChat = true }) {
                        Icon(Icons.Filled.Chat, contentDescription = "AI Assistant", tint = AccentHi)
                    }
                    IconButton(onClick = { showTerminal = true }) {
                        Icon(Icons.Filled.List, contentDescription = "Terminal", tint = AccentHi)
                    }
                    IconButton(onClick = { showLogin = true }) {
                        Icon(Icons.Filled.Lock, contentDescription = "Login", tint = Bull)
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar(containerColor = Panel) {
                Dest.values().forEach { d ->
                    NavigationBarItem(
                        selected = dest == d,
                        onClick = { dest = d },
                        icon = { Icon(d.icon, contentDescription = d.label) },
                        label = { Text(d.label, maxLines = 1) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = AccentHi, selectedTextColor = AccentHi,
                            indicatorColor = Panel2, unselectedIconColor = Muted, unselectedTextColor = Muted,
                        ),
                    )
                }
            }
        },
    ) { pad ->
        Box(Modifier.padding(pad)) {
            when (dest) {
                Dest.HOME -> HomeScreen()
                Dest.QUANT -> QuantScreen()
                Dest.MAP -> MapScreen()
                Dest.ANALYSIS -> AnalysisScreen()
                Dest.SETTINGS -> SettingsScreen(openLogin = { showLogin = true })
            }
        }
    }

    if (showLogin) LoginDialog(onDismiss = { showLogin = false })

    if (showTerminal) {
        Dialog(onDismissRequest = { showTerminal = false },
            properties = DialogProperties(usePlatformDefaultWidth = false)) {
            Surface(color = Bg, modifier = Modifier.fillMaxSize()) {
                Column(Modifier.fillMaxSize()) {
                    Row(Modifier.fillMaxWidth().padding(8.dp),
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        Spacer(Modifier.weight(1f))
                        TextButton(onClick = { showTerminal = false }) { Text("✕ Close") }
                    }
                    Box(Modifier.weight(1f)) { TerminalScreen() }
                }
            }
        }
    }

    if (showChat) {
        Dialog(onDismissRequest = { showChat = false },
            properties = DialogProperties(usePlatformDefaultWidth = false)) {
            Surface(color = Bg, modifier = Modifier.fillMaxSize()) {
                Column(Modifier.fillMaxSize()) {
                    Row(Modifier.fillMaxWidth().padding(8.dp),
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        Spacer(Modifier.weight(1f))
                        TextButton(onClick = { showChat = false }) { Text("✕ Close") }
                    }
                    Box(Modifier.weight(1f)) { ChatScreen() }
                }
            }
        }
    }
}
