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
        appendLog("[Service] Starting backend (analysis runs on NVIDIA NIM cloud)...")
        startForeground(notificationId, createNotification("Backend booting..."))

        // No on-device model — the LLM is NVIDIA NIM over the internet.
        llamaState = ServerState.RUNNING
        pythonState = ServerState.STARTING

        // Load credentials from preferences (passed into Python's os.environ).
        // NVIDIA_API_KEY is read by Python from the bundled .env; if the user
        // also pastes one in Settings we forward it here so it overrides.
        val prefs = getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE)
        val config = mutableMapOf(
            "UPSTOX_API_KEY" to (prefs.getString("upstox_api_key", "") ?: ""),
            "UPSTOX_API_SECRET" to (prefs.getString("upstox_secret", "") ?: ""),
            "UPSTOX_REDIRECT_URI" to (prefs.getString("redirect_uri", "http://localhost:8765/callback") ?: ""),
            "LLM_PROVIDER" to "nvidia"
        )
        val nvKey = prefs.getString("nvidia_api_key", "") ?: ""
        if (nvKey.isNotBlank()) config["NVIDIA_API_KEY"] = nvKey

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
