package com.portfolio.app

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.io.BufferedInputStream
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

class ModelDownloader(private val context: Context) {

    private var downloadThread: Thread? = null
    private var isDownloading = false
    private val mainHandler = Handler(Looper.getMainLooper())
    private val tag = "ModelDownloader"

    interface DownloadCallback {
        fun onProgress(progress: Int, speed: String)
        fun onSuccess(file: File)
        fun onFailure(error: String)
    }

    fun startDownload(urlStr: String, fileName: String, callback: DownloadCallback) {
        if (isDownloading) {
            callback.onFailure("Download already in progress")
            return
        }

        isDownloading = true
        downloadThread = Thread {
            val modelsDir = File(context.getExternalFilesDir(null), "models")
            if (!modelsDir.exists()) modelsDir.mkdirs()

            val tempFile = File(modelsDir, "$fileName.tmp")
            val targetFile = File(modelsDir, fileName)

            if (tempFile.exists()) tempFile.delete()

            var connection: HttpURLConnection? = null
            try {
                Log.d(tag, "Connecting to $urlStr...")
                val url = URL(urlStr)
                connection = url.openConnection() as HttpURLConnection
                connection.connectTimeout = 15000
                connection.readTimeout = 15000
                connection.instanceFollowRedirects = true
                connection.connect()

                if (connection.responseCode !in 200..299) {
                    throw Exception("Server returned HTTP ${connection.responseCode}: ${connection.responseMessage}")
                }

                val fileLength = connection.contentLengthLong
                Log.d(tag, "Downloading $fileName, length: $fileLength bytes")

                val input = BufferedInputStream(connection.inputStream)
                val output = FileOutputStream(tempFile)

                val data = ByteArray(8 * 1024)
                var total: Long = 0
                var count: Int
                var lastUpdateTime = System.currentTimeMillis()
                var bytesSinceLastUpdate: Long = 0

                while (input.read(data).also { count = it } != -1) {
                    if (Thread.currentThread().isInterrupted) {
                        output.close()
                        input.close()
                        tempFile.delete()
                        throw Exception("Download cancelled")
                    }

                    output.write(data, 0, count)
                    total += count
                    bytesSinceLastUpdate += count

                    val now = System.currentTimeMillis()
                    val timeDiff = now - lastUpdateTime
                    if (timeDiff >= 500) {
                        val progress = if (fileLength > 0) ((total * 100) / fileLength).toInt() else -1
                        val speedMb = (bytesSinceLastUpdate.toDouble() / (1024.0 * 1024.0)) / (timeDiff.toDouble() / 1000.0)
                        val speedStr = String.format("%.2f MB/s", speedMb)

                        mainHandler.post {
                            callback.onProgress(progress, speedStr)
                        }

                        lastUpdateTime = now
                        bytesSinceLastUpdate = 0
                    }
                }

                output.flush()
                output.close()
                input.close()

                if (targetFile.exists()) targetFile.delete()
                if (tempFile.renameTo(targetFile)) {
                    mainHandler.post {
                        callback.onSuccess(targetFile)
                    }
                } else {
                    throw Exception("Failed to rename temp file")
                }

            } catch (e: Exception) {
                Log.e(tag, "Download failed", e)
                if (tempFile.exists()) tempFile.delete()
                mainHandler.post {
                    callback.onFailure(e.message ?: "Unknown error")
                }
            } finally {
                connection?.disconnect()
                isDownloading = false
            }
        }
        downloadThread?.start()
    }

    fun cancelDownload() {
        downloadThread?.interrupt()
        downloadThread = null
        isDownloading = false
    }

    fun isDownloading(): Boolean = isDownloading
}
