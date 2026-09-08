package com.portfolio.app.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.portfolio.app.net.Api
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Conversation state that outlives the screen.
 *
 * The chat used to keep its messages in `remember {}` and send from
 * `rememberCoroutineScope()`. Leaving the screen destroyed that composition —
 * the history vanished and an in-flight question was cancelled mid-answer. On a
 * slow model that is most of the wait. Both now live in a process-wide scope,
 * so you can ask something, go and look at your portfolio, and come back to the
 * answer.
 */
object ChatBus {

    data class Message(val fromUser: Boolean, val text: String)

    val messages = mutableStateListOf<Message>()

    var busy by mutableStateOf(false)
        private set

    /** Kept here rather than in the screen so a half-typed question survives. */
    var draft by mutableStateOf("")

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    fun send(question: String) {
        val q = question.trim()
        if (q.isEmpty() || busy) return
        messages.add(Message(true, q))
        draft = ""
        busy = true
        scope.launch {
            val reply = try {
                when (val r = Api.chat(q)) {
                    is Api.Resp.Ok ->
                        if (r.body.optBoolean("ok", false)) r.body.optString("reply")
                        else "⚠ ${r.body.optString("error", "no answer")}"
                    is Api.Resp.Err -> "⚠ Backend: ${r.message}"
                }
            } catch (e: Exception) {
                "⚠ ${e.message ?: e.javaClass.simpleName}"
            }
            messages.add(Message(false, reply))
            busy = false
        }
    }

    fun clear() { messages.clear() }
}
