package com.portfolio.app

import android.content.Context
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.io.BufferedReader
import java.io.InputStreamReader

class LlamaServerManager(private val context: Context, private val logCallback: (String) -> Unit) {

    private var process: Process? = null
    private var isRunning = false
    private val tag = "LlamaServer"

    fun start(modelPath: String, port: Int = 8080): Boolean {
        if (isRunning) return true

        val binDir = File(context.filesDir, "bin")
        if (!binDir.exists()) binDir.mkdirs()

        val execFile = File(binDir, "llama-server")
        if (!execFile.exists()) {
            logCallback("[Llama] Extracting llama-server binary from assets...")
            try {
                copyAssetToFile("llama-server", execFile)
                execFile.setExecutable(true, true)
                logCallback("[Llama] Extraction complete.")
            } catch (e: Exception) {
                Log.e(tag, "Failed to extract llama-server", e)
                logCallback("[Llama] ERROR: Extraction failed: ${e.message}")
                return false
            }
        }

        if (!File(modelPath).exists()) {
            logCallback("[Llama] ERROR: Model file not found at $modelPath")
            return false
        }

        logCallback("[Llama] Starting llama-server on port $port with model $modelPath...")
        try {
            val pb = ProcessBuilder(
                execFile.absolutePath,
                "-m", modelPath,
                "-c", "8192",
                "--port", port.toString(),
                "--embedding",
                "--host", "127.0.0.1",
                "--threads", getOptimalThreadCount().toString()
            )
            pb.redirectErrorStream(true)
            val proc = pb.start()
            process = proc
            isRunning = true

            // Read log in background thread
            Thread {
                val reader = BufferedReader(InputStreamReader(proc.inputStream))
                var line: String?
                try {
                    while (reader.readLine().also { line = it } != null) {
                        line?.let { logCallback("[Llama] $it") }
                    }
                } catch (e: Exception) {
                    Log.d(tag, "Reader finished or interrupted")
                } finally {
                    reader.close()
                    isRunning = false
                    logCallback("[Llama] Server stopped.")
                }
            }.start()

            return true
        } catch (e: Exception) {
            Log.e(tag, "Failed to start llama-server", e)
            logCallback("[Llama] ERROR: Start failed: ${e.message}")
            isRunning = false
            return false
        }
    }

    fun stop() {
        process?.destroy()
        process = null
        isRunning = false
    }

    fun isServerRunning(): Boolean = isRunning

    private fun copyAssetToFile(assetName: String, outFile: File) {
        context.assets.open(assetName).use { input ->
            FileOutputStream(outFile).use { output ->
                val buffer = ByteArray(4 * 1024)
                var read: Int
                while (input.read(buffer).also { read = it } != -1) {
                    output.write(buffer, 0, read)
                }
                output.flush()
            }
        }
    }

    private fun getOptimalThreadCount(): Int {
        val cores = Runtime.getRuntime().availableProcessors()
        return if (cores > 4) cores - 2 else Math.max(1, cores - 1)
    }
}
