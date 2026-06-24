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
                if (!r.isSuccessful) return@withContext Resp.Err("HTTP ${r.code}: ${txt.take(180)}")
                Resp.Ok(parse(txt))
            }
        } catch (e: Exception) {
            Resp.Err(e.message ?: e.javaClass.simpleName)
        }
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
                if (!r.isSuccessful) return@withContext Resp.Err("HTTP ${r.code}: ${txt.take(180)}")
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

    // ── Macro Ideas / themes ──
    suspend fun themes(days: Int) = post("/api/themes", JSONObject().put("days", days))

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
    suspend fun umapData() = get("/api/universe-map/data")
    suspend fun umapReport() = get("/api/universe-map/report")

    // ── LLM ──
    suspend fun llmTest() = post("/api/llm/test")
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
