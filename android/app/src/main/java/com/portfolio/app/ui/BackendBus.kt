package com.portfolio.app.ui

import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf

/**
 * Compose-observable bridge between the Android Service (which actually owns
 * the Chaquopy backend + log stream) and the native UI.
 *
 * The Activity owns the ServiceConnection; it pushes state/logs in here, and
 * sets [onStart]/[onStop] so any screen can control the backend without
 * needing a direct service reference.
 */
object BackendBus {
    enum class State { STOPPED, STARTING, RUNNING, ERROR }

    val state = mutableStateOf(State.STOPPED)
    val logs = mutableStateListOf<String>()
    private const val MAX_LOGS = 600

    // Wired by MainActivity to the bound PortfolioService.
    var onStart: () -> Unit = {}
    var onStop: () -> Unit = {}

    val running get() = state.value == State.RUNNING

    fun pushLog(line: String) {
        logs.add(line)
        if (logs.size > MAX_LOGS) logs.removeAt(0)
    }

    fun setLogs(lines: List<String>) {
        logs.clear()
        logs.addAll(lines.takeLast(MAX_LOGS))
    }
}
