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
        /** 0f..1f for a determinate bar, or null while the total is unknown. */
        var progress by mutableStateOf<Float?>(null)
            internal set
        /** Human-readable detail line under the status ("420 of 1900 stocks"). */
        var detail by mutableStateOf<String?>(null)
            internal set
        /** Id of the backend job currently in flight, for re-attaching. */
        var jobId by mutableStateOf<String?>(null)
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

    /**
     * Poll a backend job id until it leaves "running".
     *
     * [maxSecs] is an IDLE timeout, not a total run time. As long as the
     * backend keeps confirming the job is still running we keep waiting, however
     * long that takes — a first full-universe crawl legitimately runs for an
     * hour, and the old hard deadline declared failure while the backend was
     * still working, threw away the result it went on to produce, and left the
     * user re-running it into a duplicate crawl.
     *
     * We only give up if the job stops being reachable — the backend died or
     * forgot it — which is a real failure worth surfacing.
     */
    private suspend fun poll(
        jobId: String,
        maxSecs: Int,
        onTick: (suspend (String) -> Unit)? = null,
    ): JSONObject {
        var lastSeenAlive = System.currentTimeMillis()
        while (true) {
            val o = Api.job(jobId).objOrNull()
            val status = o?.optString("status")
            if (o != null && status != "running") return o
            if (status == "running") lastSeenAlive = System.currentTimeMillis()
            if (System.currentTimeMillis() - lastSeenAlive > maxSecs * 1000L) {
                return JSONObject().put("status", "error").put("error",
                    "Lost contact with the backend job — it stopped reporting. " +
                    "Any work it finished is saved; run it again to continue.")
            }
            onTick?.invoke(jobId)
            delay(2000)
        }
    }

    /**
     * Submit a job-based endpoint (one that returns {"job_id": ...}) and poll it
     * to completion. Ignored if a run for [key] is already in flight.
     */
    fun run(
        key: String,
        status: String,
        maxSecs: Int = 600,
        onTick: (suspend (String, State) -> Unit)? = null,
        submit: suspend () -> Api.Resp,
    ) {
        val s = state(key)
        if (s.running) return                     // dedupe: never double-submit
        s.running = true; s.status = status; s.result = null
        s.progress = null; s.detail = null; s.jobId = null
        scope.launch {
            try {
                when (val sub = submit()) {
                    is Api.Resp.Err -> s.status = "Backend error: ${sub.message}"
                    is Api.Resp.Ok -> {
                        val jobId = sub.body.optString("job_id")
                        if (jobId.isBlank()) {
                            s.status = sub.body.optString("error", "Failed to start.")
                        } else {
                            s.jobId = jobId
                            val fin = poll(jobId, maxSecs,
                                onTick?.let { cb -> { id -> cb(id, s) } })
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
                s.progress = null
                s.jobId = null
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
