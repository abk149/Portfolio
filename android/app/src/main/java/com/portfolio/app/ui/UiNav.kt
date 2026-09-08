package com.portfolio.app.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONObject

/**
 * Which secondary screen is showing, if any.
 *
 * These used to be `Dialog` / `AlertDialog` popups. A dialog is modal: while a
 * deep dive or a chat was open, nothing else in the app could be touched, even
 * though the work itself was running happily in the background. They are now
 * ordinary destinations rendered inside the Scaffold, so the navigation bar and
 * the activity bar stay live and you can walk away from a running analysis and
 * come back to it.
 */
object UiNav {

    sealed interface Screen {
        data class DeepDive(val symbol: String) : Screen
        data class EventImpact(val event: JSONObject) : Screen
        object Chat : Screen
        object Terminal : Screen
        object Settings : Screen
        object Login : Screen
    }

    var overlay by mutableStateOf<Screen?>(null)
        private set

    fun open(screen: Screen) { overlay = screen }

    fun close() { overlay = null }

    val title: String
        get() = when (val o = overlay) {
            is Screen.DeepDive -> "Deep dive · ${o.symbol}"
            is Screen.EventImpact -> "Event impact"
            Screen.Chat -> "AI assistant"
            Screen.Terminal -> "System terminal"
            Screen.Settings -> "Settings"
            Screen.Login -> "Broker login"
            null -> ""
        }
}
