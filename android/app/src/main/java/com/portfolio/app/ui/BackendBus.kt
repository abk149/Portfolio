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

    /**
     * Report in-flight work to the foreground-service notification.
     *
     * Two reasons this matters: the user can see that an analysis is still
     * progressing after minimising the app, and Android has a visible,
     * user-facing reason to keep the process alive rather than reclaiming it.
     */
    var onActivity: (String?) -> Unit = {}

    /**
     * Build identity of the PYTHON backend actually running, read from
     * /api/status. The APK version alone doesn't prove which engine code is
     * live, and telling "regression" from "stale install" apart used to cost a
     * full rebuild-and-ask cycle.
     */
    var backendBuild = mutableStateOf<String?>(null)

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
