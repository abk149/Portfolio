package com.portfolio.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import java.util.Collections
import java.util.LinkedList

class PortfolioService : LifecycleService() {

    private val binder = LocalBinder()
    private val tag = "PortfolioService"
    private val notificationId = 101
    private val channelId = "PortfolioServiceChannel"

    // The on-device llama-server has been removed — analysis now runs on
    // NVIDIA NIM over the internet. We keep `llamaState` only as a cosmetic
    // "Cloud LLM" indicator in the UI.
    private lateinit var pythonManager: PythonServerManager

    val logs: MutableList<String> = Collections.synchronizedList(LinkedList<String>())
    private val maxLogLines = 500

    private var stateListener: ServiceStateListener? = null

    enum class ServerState { STOPPED, STARTING, RUNNING, ERROR }

    var llamaState = ServerState.STOPPED
        private set(value) {
            field = value
            notifyStateChange()
        }

    var pythonState = ServerState.STOPPED
        private set(value) {
            field = value
            notifyStateChange()
        }

    interface ServiceStateListener {
        fun onStateChanged(llamaState: ServerState, pythonState: ServerState)
        fun onLogReceived(line: String)
    }

    inner class LocalBinder : Binder() {
        fun getService(): PortfolioService = this@PortfolioService
    }

    override fun onCreate() {
        super.onCreate()
        Log.d(tag, "Service onCreate")
        createNotificationChannel()

        pythonManager = PythonServerManager(this) { line ->
            appendLog(line)
            if (line.contains("Uvicorn running on") || line.contains("Application startup complete")) {
                pythonState = ServerState.RUNNING
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        Log.d(tag, "Service onStartCommand")
        startForeground(notificationId, createNotification("Backend is stopped"))
        return START_STICKY
    }

    override fun onBind(intent: Intent): IBinder {
        super.onBind(intent)
        Log.d(tag, "Service onBind")
        return binder
    }

    fun setStateListener(listener: ServiceStateListener?) {
        stateListener = listener
        listener?.onStateChanged(llamaState, pythonState)
    }

    fun startServers() {
        // Load credentials from preferences (passed into Python's os.environ).
        val prefs = getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE)
        val provider = prefs.getString("llm_provider", "nvidia") ?: "nvidia"
        
        appendLog("[Service] Starting backend (LLM provider: $provider)...")
        startForeground(notificationId, createNotification("Backend booting..."))

        // Update UI indicator
        if (provider == "nvidia" || provider == "anthropic") {
            llamaState = ServerState.RUNNING // Acts as "Cloud Active"
        } else {
            llamaState = ServerState.STOPPED
        }
        
        pythonState = ServerState.STARTING

        val config = mutableMapOf(
            "UPSTOX_API_KEY" to (prefs.getString("upstox_api_key", "") ?: ""),
            "UPSTOX_API_SECRET" to (prefs.getString("upstox_secret", "") ?: ""),
            "UPSTOX_REDIRECT_URI" to (prefs.getString("redirect_uri", "http://localhost:8765/callback") ?: ""),
            "UPSTOX_BASE_URL" to (prefs.getString("upstox_base_url", "https://api.upstox.com/v2") ?: ""),
            "UPSTOX_BEARER_TOKEN" to (prefs.getString("upstox_bearer_token", "") ?: ""),
            "LLM_PROVIDER" to provider,
            "OLLAMA_HOST" to (prefs.getString("ollama_host", "http://127.0.0.1:11434") ?: ""),
            "TELE_TOKEN" to (prefs.getString("tele_token", "") ?: ""),
            "CHAT_ID" to (prefs.getString("tele_chat_id", "") ?: "")
        )
        
        val llmKey = prefs.getString("llm_api_key", "") ?: ""
        if (llmKey.isNotBlank()) {
            config["NVIDIA_API_KEY"] = llmKey
            config["ANTHROPIC_API_KEY"] = llmKey
        }

        // Active broker (either/or). Only inject if the user explicitly set it
        // in prefs; otherwise leave it unset so the dashboard's persisted
        // choice (.cache/active_broker.txt) wins and survives restarts.
        val broker = prefs.getString("broker", "") ?: ""
        if (broker.isNotBlank()) config["BROKER"] = broker
        val growwToken = prefs.getString("groww_access_token", "") ?: ""
        if (growwToken.isNotBlank()) config["GROWW_ACCESS_TOKEN"] = growwToken
        val growwKey = prefs.getString("groww_api_key", "") ?: ""
        if (growwKey.isNotBlank()) config["GROWW_API_KEY"] = growwKey
        val growwSecret = prefs.getString("groww_api_secret", "") ?: ""
        if (growwSecret.isNotBlank()) config["GROWW_API_SECRET"] = growwSecret

        appendLog("[Service] Injecting ${config.size} environment variables into Python...")
        val pythonSuccess = pythonManager.start(config)
        if (!pythonSuccess) {
            pythonState = ServerState.ERROR
            appendLog("[Service] ERROR: Python server failed to start.")
            startForeground(notificationId, createNotification("Backend encountered an error"))
        } else {
            startForeground(notificationId, createNotification("Backend running (cloud LLM)"))
        }
    }

    fun stopServers() {
        appendLog("[Service] Stopping backend...")
        startForeground(notificationId, createNotification("Backend stopping..."))

        pythonManager.stop()

        llamaState = ServerState.STOPPED
        pythonState = ServerState.STOPPED

        appendLog("[Service] All servers stopped.")
        startForeground(notificationId, createNotification("Backend is stopped"))
        stopForeground(true)
        stopSelf()
    }

    private fun appendLog(line: String) {
        synchronized(logs) {
            if (logs.size >= maxLogLines) {
                logs.removeAt(0)
            }
            logs.add(line)
        }
        ContextCompat.getMainExecutor(this).execute {
            stateListener?.onLogReceived(line)
        }
    }

    private fun notifyStateChange() {
        ContextCompat.getMainExecutor(this).execute {
            stateListener?.onStateChanged(llamaState, pythonState)
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val serviceChannel = NotificationChannel(
                channelId,
                "Portfolio Quant Service Channel",
                NotificationManager.IMPORTANCE_LOW
            )
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(serviceChannel)
        }
    }

    private fun createNotification(statusText: String): Notification {
        val notificationIntent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this, 0, notificationIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, channelId)
            .setContentTitle("Portfolio Quant Backend")
            .setContentText(statusText)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    override fun onDestroy() {
        super.onDestroy()
        Log.d(tag, "Service onDestroy")
        pythonManager.stop()
    }
}
