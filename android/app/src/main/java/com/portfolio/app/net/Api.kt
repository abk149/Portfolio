package com.portfolio.app.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Thin OkHttp wrapper for the on-device FastAPI backend (127.0.0.1:8000).
 * Everything stays on localhost, so it's fast and never leaves the phone.
 *
 * Calls are suspend functions on Dispatchers.IO and return a [Resp]:
 *  - Ok(obj)  — parsed JSON object (arrays are wrapped as {"array": [...]})
 *  - Err(msg) — network / HTTP / parse failure, surfaced to the UI
 *
 * Wire-level is left untyped (org.json) so a backend schema tweak degrades
 * gracefully instead of throwing.
 */
object Api {
    const val BASE = "http://127.0.0.1:8000"

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)   // quant/umap builds can be long
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val JSON = "application/json".toMediaType()

    sealed class Resp {
        data class Ok(val body: JSONObject) : Resp()
        data class Err(val message: String) : Resp()

        val ok: Boolean get() = this is Ok
        fun objOrNull(): JSONObject? = (this as? Ok)?.body
    }

    private fun parse(text: String): JSONObject = when {
        text.isBlank() -> JSONObject()
        text.trimStart().startsWith("[") -> JSONObject().put("array", JSONArray(text))
        else -> JSONObject(text)
    }

    suspend fun get(path: String): Resp = withContext(Dispatchers.IO) {
        try {
            val req = Request.Builder()
                .url(BASE + path)
                .header("x-client", "portfolio-mobile")
                .get().build()
            client.newCall(req).execute().use { r ->
                val txt = r.body?.string().orEmpty()
                if (!r.isSuccessful) return@withContext Resp.Err(errorMessage(r.code, txt))
                Resp.Ok(parse(txt))
            }
        } catch (e: Exception) {
            Resp.Err(e.message ?: e.javaClass.simpleName)
        }
    }

    /**
     * Readable message from a failed response.
     *
     * The backend now answers errors with `{"error": "..."}`, so pull that out
     * rather than showing the first 180 characters of whatever came back —
     * which, for a traceback, was fifty frames of framework internals with the
     * actual cause cut off the end.
     */
    private fun errorMessage(code: Int, body: String): String {
        val fromJson = runCatching {
            val o = JSONObject(body)
            listOfNotNull(
                o.optString("error").takeIf { it.isNotBlank() },
                o.optString("message").takeIf { it.isNotBlank() },
            ).firstOrNull()
        }.getOrNull()
        return fromJson ?: "HTTP $code: ${body.take(180)}"
    }

    suspend fun post(path: String, body: JSONObject = JSONObject()): Resp = withContext(Dispatchers.IO) {
        try {
            val req = Request.Builder()
                .url(BASE + path)
                .header("x-client", "portfolio-mobile")
                .post(body.toString().toRequestBody(JSON))
                .build()
            client.newCall(req).execute().use { r ->
                val txt = r.body?.string().orEmpty()
                if (!r.isSuccessful) return@withContext Resp.Err(errorMessage(r.code, txt))
                Resp.Ok(parse(txt))
            }
        } catch (e: Exception) {
            Resp.Err(e.message ?: e.javaClass.simpleName)
        }
    }

    // ── Health ──
    suspend fun status() = get("/api/status")

    // ── Portfolio ──
    suspend fun portfolio() = get("/api/portfolio")
    suspend fun portfolioRisk() = get("/api/portfolio/risk")
    suspend fun performanceCached() = get("/api/portfolio/performance/cached")
    suspend fun performance() = post("/api/portfolio/performance")
    suspend fun optimize(mode: String, maxWeight: Double) =
        post("/api/portfolio/optimize",
            JSONObject().put("mode", mode).put("max_weight", maxWeight))
    suspend fun deployCash(cash: Double, includeUniverse: Boolean = true) =
        post("/api/portfolio/deploy-cash",
            JSONObject().put("cash", cash).put("include_universe", includeUniverse))
    suspend fun deployCashTickers(cash: Double, tickers: List<String>) =
        post("/api/portfolio/deploy-cash", JSONObject().put("cash", cash)
            .put("include_universe", false).put("tickers", org.json.JSONArray(tickers)))

    // ── Benchmark (vs index) ──
    suspend fun benchmark(index: String = "^NSEI", windowDays: Int = 365) =
        post("/api/portfolio/benchmark",
            JSONObject().put("index", index).put("window_days", windowDays))

    // ── Market calendar ──
    suspend fun calendar(daysAhead: Int = 60, daysBack: Int = 7, refresh: Boolean = false) =
        post("/api/calendar", JSONObject()
            .put("days_ahead", daysAhead).put("days_back", daysBack).put("refresh", refresh))
    suspend fun calendarCached() = get("/api/calendar/cached")
    suspend fun newsHealth(refresh: Boolean = false) =
        get("/api/news/health" + if (refresh) "?refresh=true" else "")

    // ── AI applications (all job-based; poll /api/jobs/{id}) ──
    suspend fun aiBrief() = post("/api/ai/brief")
    suspend fun aiPerformanceReview() = post("/api/ai/performance-review")
    suspend fun aiRiskReview() = post("/api/ai/risk-review")
    suspend fun aiEventImpact(event: JSONObject) =
        post("/api/ai/event-impact", JSONObject().put("event", event))

    // ── Ghost (paper) portfolio ──
    suspend fun ghost() = get("/api/ghost")
    suspend fun ghostBuy(symbol: String, amount: Double, source: String, note: String = "") =
        post("/api/ghost/buy", JSONObject()
            .put("symbol", symbol).put("amount", amount)
            .put("source", source).put("note", note))
    suspend fun ghostSell(id: String) = post("/api/ghost/sell", JSONObject().put("id", id))
    suspend fun ghostReset() = post("/api/ghost/reset")
    suspend fun ghostCurve() = post("/api/ghost/curve")
    suspend fun ghostReview() = post("/api/ghost/review", JSONObject().put("with_ai", true))

    // ── Macro Ideas / themes ──
    suspend fun themes(days: Int) = post("/api/themes", JSONObject().put("days", days))
    suspend fun deepDive(symbol: String) =
        post("/api/deep-dive", JSONObject().put("symbol", symbol))

    // ── Analysis ──
    suspend fun screener(universe: String, minScore: Int) =
        post("/api/screener/scan", JSONObject().put("universe", universe).put("min_score", minScore))
    suspend fun intradayAnalyze(days: Int) =
        post("/api/intraday/analyze", JSONObject().put("days", days))
    suspend fun intradayScan(universe: String, minScore: Int) =
        post("/api/intraday/scan", JSONObject().put("universe", universe).put("min_score", minScore))
    suspend fun macro() = get("/api/quant/macro")

    // ── DR-Quant funnel (job) ──
    suspend fun quantRun(universe: String) =
        post("/api/quant/run", JSONObject().put("universe", universe))
    suspend fun job(jobId: String) = get("/api/jobs/$jobId")

    // ── Universe map ──
    suspend fun umapBuild(universe: String, maxAgeDays: Double = 7.0) =
        post("/api/universe-map/build",
            JSONObject().put("universe", universe).put("max_age_days", maxAgeDays))
    // NOTE: /api/universe-map/report returns an .xlsx FILE, not JSON — it is a
    // download endpoint and must not be parsed as a response body. The stats the
    // UI needs (count / tech_total / fund_scanned / fund_reused) all come back
    // from /data alongside the stocks.
    suspend fun umapData(universe: String = "all_nse") =
        get("/api/universe-map/data?universe=$universe")
    suspend fun umapProgress(jobId: String) = get("/api/universe-map/progress/$jobId")

    // ── LLM ──
    suspend fun llmTest() = post("/api/llm/test")
    suspend fun llmModels() = get("/api/llm/models")
    suspend fun llmConfig(apiKey: String, model: String) =
        post("/api/llm/config", JSONObject().put("api_key", apiKey).put("model", model))
    suspend fun chat(message: String) = post("/api/chat", JSONObject().put("message", message))

    // ── Knowledge base ──
    suspend fun kbStats() = get("/api/kb/stats")
    suspend fun kbSearch(query: String) =
        post("/api/kb/search", JSONObject().put("query", query))

    // ── Broker / auth ──
    suspend fun broker() = get("/api/broker")
    suspend fun setBroker(name: String) = post("/api/broker", JSONObject().put("broker", name))
    suspend fun brokerTest() = post("/api/broker/test")
    suspend fun upstoxConfig(apiKey: String, secret: String, redirect: String) =
        post("/api/upstox/config", JSONObject()
            .put("api_key", apiKey).put("api_secret", secret).put("redirect_uri", redirect))
    suspend fun upstoxAuthUrl() = get("/api/upstox/auth-url")
    suspend fun upstoxExchange(codeOrUrl: String) =
        post("/api/upstox/exchange-code", JSONObject().put("code_or_url", codeOrUrl))
    suspend fun upstoxTestToken() = post("/api/upstox/test-token")
    suspend fun growwSaveToken(token: String) =
        post("/api/groww/save-token", JSONObject().put("token", token))
    suspend fun growwLogin(apiKey: String, totpSecret: String, secret: String) =
        post("/api/groww/login", JSONObject()
            .put("api_key", apiKey).put("totp_secret", totpSecret).put("secret", secret))
}
