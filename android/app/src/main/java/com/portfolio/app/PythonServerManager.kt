package com.portfolio.app

import android.content.Context
import android.util.Log
import com.chaquo.python.Python

class PythonServerManager(private val context: Context, private val logCallback: (String) -> Unit) {

    private var thread: Thread? = null
    private var isRunning = false
    private val tag = "PythonServer"

    fun start(config: Map<String, String>): Boolean {
        if (isRunning) return true

        logCallback("[Python] Initializing Python runtime...")
        try {
            if (!Python.isStarted()) {
                val platform = com.chaquo.python.android.AndroidPlatform(context)
                Python.start(platform)
            }
            
            val py = Python.getInstance()
            isRunning = true

            // Set configuration credentials in os.environ
            val os = py.getModule("os")
            val environ = os["environ"]
            for ((key, value) in config) {
                environ?.callAttr("__setitem__", key, value)
            }

            logCallback("[Python] Python runtime ready. Booting FastAPI...")
            
            thread = Thread {
                try {
                    val runner = py.getModule("src.android_runner")
                    val receiver = LogReceiver(logCallback)
                    runner.callAttr("start_dashboard", context.filesDir.absolutePath, receiver)
                } catch (e: Exception) {
                    Log.e(tag, "Python server crashed/stopped", e)
                    logCallback("[Python] Stopped: ${e.message}")
                } finally {
                    isRunning = false
                }
            }
            thread?.start()
            return true
        } catch (e: Exception) {
            Log.e(tag, "Failed to start Python server", e)
            logCallback("[Python] ERROR: Start failed: ${e.message}")
            isRunning = false
            return false
        }
    }

    @Suppress("DEPRECATION")
    fun stop() {
        try {
            thread?.interrupt()
            // In cases where blocking network sockets ignore thread interrupt, we can force-stop the thread,
            // or let the service lifecycle destroy it when the process exits.
            // Under Android services, stopping uvicorn via thread interrupt is typically fine.
        } catch (e: Exception) {
            Log.e(tag, "Failed to stop Python server thread", e)
        }
        thread = null
        isRunning = false
    }

    fun isServerRunning(): Boolean = isRunning
}

// Dedicated public class for Python callback to ensure clean JNI bridging
class LogReceiver(private val callback: (String) -> Unit) {
    fun log(msg: String) {
        callback("[Python] $msg")
    }
}
