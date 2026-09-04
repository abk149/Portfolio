package com.portfolio.app.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.portfolio.app.net.Api
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Process-wide registry for long-running backend jobs.
 *
 * Why this exists: screens used to keep job state in `remember {}` and poll from
 * `rememberCoroutineScope()`. Navigating away destroyed that composition, so the
 * poll was cancelled and the result was lost — and re-running spawned a SECOND
 * backend job doing the same work.
 *
 * JobBus fixes both:
 *  - state and polling live in a singleton scope that outlives navigation, so a
 *    run keeps going (and completes) while you're on another screen;
 *  - a job key can only have ONE run in flight, so tapping again is a no-op
 *    instead of duplicating work.
 *
 * Screens become stateless views over `JobBus.state(key)`.
 */
object JobBus {

    class State {
        var running by mutableStateOf(false)
            internal set
        var status by mutableStateOf<String?>(null)
            internal set
        var result by mutableStateOf<JSONObject?>(null)
            internal set
        var finishedAt by mutableStateOf(0L)
            internal set
    }

    // Survives composition/navigation for the life of the process.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val states = mutableStateMapOf<String, State>()

    fun state(key: String): State = states.getOrPut(key) { State() }

    fun isRunning(key: String): Boolean = states[key]?.running == true

    /** Pre-populate a key from a cached backend result (no job run). */
    fun seed(key: String, result: JSONObject) {
        val s = state(key)
        if (!s.running && s.result == null) { s.result = result; s.status = null }
    }

    fun clear(key: String) {
        state(key).apply { result = null; status = null }
    }

    /** Poll a backend job id until it leaves "running". */
    private suspend fun poll(jobId: String, maxSecs: Int): JSONObject {
        val deadline = System.currentTimeMillis() + maxSecs * 1000L
        while (System.currentTimeMillis() < deadline) {
            val o = Api.job(jobId).objOrNull()
            if (o != null && o.optString("status") != "running") return o
            delay(2000)
        }
        return JSONObject().put("status", "error").put("error", "timed out")
    }

    /**
     * Submit a job-based endpoint (one that returns {"job_id": ...}) and poll it
     * to completion. Ignored if a run for [key] is already in flight.
     */
    fun run(
        key: String,
        status: String,
        maxSecs: Int = 600,
        submit: suspend () -> Api.Resp,
    ) {
        val s = state(key)
        if (s.running) return                     // dedupe: never double-submit
        s.running = true; s.status = status; s.result = null
        scope.launch {
            try {
                when (val sub = submit()) {
                    is Api.Resp.Err -> s.status = "Backend error: ${sub.message}"
                    is Api.Resp.Ok -> {
                        val jobId = sub.body.optString("job_id")
                        if (jobId.isBlank()) {
                            s.status = sub.body.optString("error", "Failed to start.")
                        } else {
                            val fin = poll(jobId, maxSecs)
                            if (fin.optString("status") == "done") {
                                val r = fin.optJSONObject("result")
                                if (r != null && r.has("error")) s.status = r.optString("error")
                                else { s.result = r; s.status = null }
                            } else {
                                s.status = "Failed: ${fin.optString("error", "unknown error")}"
                            }
                        }
                    }
                }
            } catch (e: Exception) {
                s.status = "Error: ${e.message}"
            } finally {
                s.running = false
                s.finishedAt = System.currentTimeMillis()
            }
        }
    }

    /** Same guarantees, for endpoints that answer synchronously. */
    fun runSync(key: String, status: String, call: suspend () -> Api.Resp) {
        val s = state(key)
        if (s.running) return
        s.running = true; s.status = status; s.result = null
        scope.launch {
            try {
                when (val r = call()) {
                    is Api.Resp.Err -> s.status = "Backend error: ${r.message}"
                    is Api.Resp.Ok ->
                        if (r.body.has("error")) s.status = r.body.optString("error")
                        else { s.result = r.body; s.status = null }
                }
            } catch (e: Exception) {
                s.status = "Error: ${e.message}"
            } finally {
                s.running = false
                s.finishedAt = System.currentTimeMillis()
            }
        }
    }
}
