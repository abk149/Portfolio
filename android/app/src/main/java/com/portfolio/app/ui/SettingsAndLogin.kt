package com.portfolio.app.ui

import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.portfolio.app.net.Api
import kotlinx.coroutines.launch

// ─────────────────────────────────────────────────────────────────────────────
// SETTINGS
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun SettingsScreen(openLogin: () -> Unit) {
    val ctx = LocalContext.current
    val prefs = remember { Prefs(ctx) }
    val scope = rememberCoroutineScope()

    var broker by remember { mutableStateOf(prefs.get("broker", "upstox")) }
    var apiKey by remember { mutableStateOf(prefs.get("upstox_api_key")) }
    var secret by remember { mutableStateOf(prefs.get("upstox_secret")) }
    var redirect by remember { mutableStateOf(prefs.get("redirect_uri", "http://localhost:8000/callback")) }
    var baseUrl by remember { mutableStateOf(prefs.get("upstox_base_url", "https://api.upstox.com/v2")) }
    var growwTok by remember { mutableStateOf(prefs.get("groww_access_token")) }
    var growwKey by remember { mutableStateOf(prefs.get("groww_api_key")) }
    var growwSec by remember { mutableStateOf(prefs.get("groww_api_secret")) }
    var teleTok by remember { mutableStateOf(prefs.get("tele_token")) }
    var teleChat by remember { mutableStateOf(prefs.get("tele_chat_id")) }
    var llmProvider by remember { mutableStateOf(prefs.get("llm_provider", "nvidia")) }
    var llmKey by remember { mutableStateOf(prefs.get("llm_api_key")) }

    fun save() {
        prefs.put(
            "broker" to broker, "upstox_api_key" to apiKey, "upstox_secret" to secret,
            "redirect_uri" to redirect, "upstox_base_url" to baseUrl,
            "groww_access_token" to growwTok, "groww_api_key" to growwKey, "groww_api_secret" to growwSec,
            "tele_token" to teleTok, "tele_chat_id" to teleChat,
            "llm_provider" to llmProvider, "llm_api_key" to llmKey,
        )
        Toast.makeText(ctx, "Saved on device", Toast.LENGTH_SHORT).show()
        if (BackendBus.running) scope.launch { Api.setBroker(broker) }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        Text("Settings", color = OnBg, fontSize = 20.sp,
            fontWeight = androidx.compose.ui.text.font.FontWeight.Bold,
            modifier = Modifier.padding(start = 16.dp, top = 12.dp))

        SectionCard("Broker (either / or)", AccentHi) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("upstox", "groww").forEach {
                    FilterChip(selected = broker == it, onClick = { broker = it }, label = { Text(it) })
                }
            }
            Spacer(Modifier.height(10.dp))
            Button(onClick = openLogin, modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Bull)) {
                Text("🔐 Login to ${broker.replaceFirstChar { it.uppercase() }}")
            }
        }

        if (broker == "upstox") {
            SectionCard("Upstox credentials", AccentHi) {
                Field("Client ID", apiKey) { apiKey = it }
                Field("Client Secret", secret, password = true) { secret = it }
                Field("Redirect URI", redirect) { redirect = it }
                Field("Base URL", baseUrl) { baseUrl = it }
                Text("Redirect URI must be registered verbatim in your Upstox app " +
                    "(127.0.0.1 ≠ localhost).", color = Muted, fontSize = 11.sp,
                    modifier = Modifier.padding(top = 6.dp))
            }
        } else {
            SectionCard("Groww credentials", AccentHi) {
                Field("Access token (daily)", growwTok) { growwTok = it }
                Field("API key (optional, TOTP)", growwKey) { growwKey = it }
                Field("API secret (optional, TOTP)", growwSec, password = true) { growwSec = it }
            }
        }

        SectionCard("Analysis LLM", Warn) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("nvidia", "anthropic").forEach {
                    FilterChip(selected = llmProvider == it, onClick = { llmProvider = it }, label = { Text(it) })
                }
            }
            Spacer(Modifier.height(8.dp))
            Field("LLM API key", llmKey, password = true) { llmKey = it }
        }

        SectionCard("Notifications", AccentHi) {
            Field("Telegram bot token", teleTok) { teleTok = it }
            Field("Chat ID", teleChat) { teleChat = it }
        }

        SectionCard("Backend", if (BackendBus.running) Bull else Bear) {
            val (lbl, col) = when (BackendBus.state.value) {
                BackendBus.State.RUNNING -> "● RUNNING" to Bull
                BackendBus.State.STARTING -> "● STARTING" to Warn
                BackendBus.State.ERROR -> "● ERROR" to Bear
                else -> "● STOPPED" to Muted
            }
            Pill(lbl, col)
            Spacer(Modifier.height(10.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { BackendBus.onStart() }, enabled = !BackendBus.running,
                    colors = ButtonDefaults.buttonColors(containerColor = Bull),
                    modifier = Modifier.weight(1f)) { Text("▶ Start") }
                Button(onClick = { BackendBus.onStop() }, enabled = BackendBus.running,
                    colors = ButtonDefaults.buttonColors(containerColor = Bear),
                    modifier = Modifier.weight(1f)) { Text("⏹ Stop") }
            }
            Text("Credential changes apply on the next backend start.",
                color = Muted, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp))
        }

        Box(Modifier.padding(12.dp)) {
            Button(onClick = { save() }, modifier = Modifier.fillMaxWidth()) {
                Text("Save settings")
            }
        }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun Field(label: String, value: String, password: Boolean = false, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value, onValueChange = onChange,
        label = { Text(label) }, singleLine = true,
        visualTransformation = if (password) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
    )
}

// ─────────────────────────────────────────────────────────────────────────────
// LOGIN DIALOG — ported from the Upstox bot's Android flow:
//   get link → open in the real browser → paste the redirect URL back → exchange
//   (plus a direct access-token path for both brokers)
// ─────────────────────────────────────────────────────────────────────────────
@Composable
fun LoginDialog(onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    val prefs = remember { Prefs(ctx) }
    val scope = rememberCoroutineScope()
    val broker = prefs.broker

    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    var loginUrl by remember { mutableStateOf<String?>(null) }
    var redirectPaste by remember { mutableStateOf("") }
    // Editable here so the client_id can never be empty when we build the link.
    var ak by remember { mutableStateOf(prefs.get("upstox_api_key")) }
    var sk by remember { mutableStateOf(prefs.get("upstox_secret")) }
    var ru by remember { mutableStateOf(prefs.get("redirect_uri", "http://localhost:8000/callback")) }
    var token by remember {
        mutableStateOf(prefs.get(if (broker == "groww") "groww_access_token" else "upstox_bearer_token"))
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
        title = { Text("Login · ${broker.replaceFirstChar { it.uppercase() }}") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (!BackendBus.running) {
                    StatusBanner("Start the backend first (Terminal ▶).", Warn)
                    Spacer(Modifier.height(10.dp))
                }
                msg?.let { StatusBanner(it, if (it.startsWith("✅")) Bull else Bear); Spacer(Modifier.height(10.dp)) }

                if (broker == "upstox") {
                    Text("A · Login link (recommended)", color = AccentHi, fontSize = 12.sp,
                        fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold)
                    Spacer(Modifier.height(6.dp))
                    OutlinedTextField(ak, { ak = it }, label = { Text("Client ID (API key)") },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(6.dp))
                    OutlinedTextField(sk, { sk = it }, label = { Text("Client Secret") },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(6.dp))
                    OutlinedTextField(ru, { ru = it }, label = { Text("Redirect URI") },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(6.dp))
                    Button(
                        onClick = {
                            scope.launch {
                                busy = true; msg = null; loginUrl = null
                                if (ak.isBlank() || sk.isBlank()) {
                                    msg = "❌ Enter Client ID and Secret first."; busy = false; return@launch
                                }
                                // Persist + push live so the backend builds the link with these creds.
                                prefs.put("upstox_api_key" to ak.trim(), "upstox_secret" to sk.trim(),
                                    "redirect_uri" to ru.trim())
                                val cfg = Api.upstoxConfig(ak.trim(), sk.trim(), ru.trim())
                                if (cfg is Api.Resp.Err) {
                                    msg = "❌ Backend unreachable: ${cfg.message}. Is it running (Terminal ▶)?"
                                    busy = false; return@launch
                                }
                                when (val resp = Api.upstoxAuthUrl()) {
                                    is Api.Resp.Ok ->
                                        if (resp.body.optBoolean("ok", false)) loginUrl = resp.body.optString("url")
                                        else msg = "❌ ${resp.body.optString("error", "could not build login URL")}"
                                    is Api.Resp.Err -> msg = "❌ Backend error: ${resp.message}"
                                }
                                busy = false
                            }
                        },
                        enabled = !busy && BackendBus.running, modifier = Modifier.fillMaxWidth(),
                    ) { Text("1 · Get login link") }

                    loginUrl?.let { u ->
                        Spacer(Modifier.height(6.dp))
                        Text(u, color = Muted, fontSize = 10.sp)
                        Spacer(Modifier.height(6.dp))
                        Button(onClick = {
                            runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(u))) }
                        }, modifier = Modifier.fillMaxWidth()) { Text("2 · Open Upstox login in browser") }
                        Spacer(Modifier.height(4.dp))
                        Text("Log in; the browser auto-completes (shows ✅) since the redirect " +
                            "lands back on this device. Then tap below.",
                            color = Muted, fontSize = 11.sp)
                        Spacer(Modifier.height(6.dp))
                        Button(
                            onClick = {
                                scope.launch {
                                    busy = true
                                    val r = Api.brokerTest().objOrNull()
                                    msg = if (r?.optBoolean("ok", false) == true)
                                        "✅ ${r.optString("message", "logged in")}"
                                    else "Not logged in yet: ${r?.optString("message", "—")}. " +
                                        "Finish login in the browser, or use the paste fallback below."
                                    busy = false
                                }
                            },
                            enabled = !busy && BackendBus.running, modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(containerColor = Bull),
                        ) { Text("3 · ✓ I've logged in — verify") }

                        Spacer(Modifier.height(10.dp))
                        Text("Fallback — if the browser didn't auto-complete, paste the full " +
                            "redirect URL here:", color = Muted, fontSize = 11.sp)
                        Spacer(Modifier.height(4.dp))
                        OutlinedTextField(redirectPaste, { redirectPaste = it },
                            label = { Text("Paste redirect URL") }, singleLine = true,
                            modifier = Modifier.fillMaxWidth())
                        Spacer(Modifier.height(6.dp))
                        OutlinedButton(
                            onClick = {
                                scope.launch {
                                    busy = true
                                    val r = Api.upstoxExchange(redirectPaste).objOrNull()
                                    msg = if (r?.optBoolean("ok", false) == true)
                                        "✅ Logged in as ${r.optString("user", "you")}"
                                    else "❌ ${r?.optString("error", "exchange failed")}"
                                    redirectPaste = ""; busy = false
                                }
                            },
                            enabled = !busy && redirectPaste.isNotBlank(), modifier = Modifier.fillMaxWidth(),
                        ) { Text("Submit pasted URL") }
                    }
                    Spacer(Modifier.height(14.dp))
                    Divider(color = BorderCol)
                    Spacer(Modifier.height(14.dp))
                }

                Text(if (broker == "groww") "Access token" else "B · Direct access token",
                    color = AccentHi, fontSize = 12.sp,
                    fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold)
                Spacer(Modifier.height(6.dp))
                OutlinedTextField(token, { token = it }, label = { Text("Paste token") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(6.dp))
                Button(
                    onClick = {
                        scope.launch {
                            busy = true; msg = null
                            if (broker == "groww") {
                                prefs.put("groww_access_token" to token)
                                Api.growwSaveToken(token)
                            } else {
                                prefs.put("upstox_bearer_token" to token)
                            }
                            val r = Api.brokerTest().objOrNull()
                            msg = if (r?.optBoolean("ok", false) == true)
                                "✅ ${r.optString("message", "authenticated")}"
                            else "❌ ${r?.optString("message", "token rejected")}"
                            busy = false
                        }
                    },
                    enabled = !busy && token.isNotBlank() && BackendBus.running,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Apply & test token") }
            }
        },
    )
}
