package com.portfolio.app

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.net.Uri
import android.os.Bundle
import android.os.IBinder
import android.provider.OpenableColumns
import android.view.View
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import java.io.File
import java.io.FileOutputStream

class MainActivity : AppCompatActivity() {

    private val tag = "MainActivity"
    private var service: PortfolioService? = null
    private var isBound = false

    // UI elements
    private lateinit var webView: WebView
    private lateinit var webProgressBar: ProgressBar
    private lateinit var loadingLayout: LinearLayout
    private lateinit var tvLoadingStatus: TextView

    private lateinit var tvPythonStatus: TextView
    private lateinit var tvLlamaStatus: TextView
    private lateinit var btnStartServers: Button
    private lateinit var btnStopServers: Button
    private lateinit var consoleText: TextView
    private lateinit var consoleScroll: ScrollView
    private lateinit var btnClearConsole: ImageButton

    private lateinit var tvActiveModel: TextView
    private lateinit var btnSelectLocalModel: Button
    private lateinit var btnDownloadModel: Button
    private lateinit var downloadProgressLayout: LinearLayout
    private lateinit var downloadProgressBar: ProgressBar
    private lateinit var tvDownloadProgressPct: TextView
    private lateinit var tvDownloadSpeed: TextView

    private lateinit var etUpstoxApiKey: EditText
    private lateinit var etUpstoxSecret: EditText
    private lateinit var etRedirectUri: EditText
    private lateinit var btnSaveConfig: Button

    private lateinit var btnTabDashboard: TextView
    private lateinit var btnTabConsole: TextView
    private lateinit var btnTabSettings: TextView
    private lateinit var tabDashboardLayout: View
    private lateinit var tabConsoleLayout: View
    private lateinit var tabSettingsLayout: View

    private lateinit var modelDownloader: ModelDownloader
    private var selectedModelPath: String? = null
    private val modelPickRequestCode = 42

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val localBinder = binder as PortfolioService.LocalBinder
            service = localBinder.getService()
            isBound = true

            // Set up state listener
            service?.setStateListener(stateListener)

            // Populate existing logs
            service?.logs?.toList()?.forEach { line ->
                appendConsoleLog(line)
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            isBound = false
        }
    }

    private val stateListener = object : PortfolioService.ServiceStateListener {
        override fun onStateChanged(llamaState: PortfolioService.ServerState, pythonState: PortfolioService.ServerState) {
            updateStatusUI(llamaState, pythonState)
        }

        override fun onLogReceived(line: String) {
            appendConsoleLog(line)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        modelDownloader = ModelDownloader(this)
        initViews()
        setupWebView()
        loadSavedConfig()
        setupListeners()
        configureCloudLlmUi()   // hide on-device model UI; analysis is cloud
        selectTab(0) // Start with Dashboard tab

        // Start background service so it runs even if Activity is destroyed
        val intent = Intent(this, PortfolioService::class.java)
        startService(intent)
        bindService(intent, serviceConnection, Context.BIND_AUTO_CREATE)
    }

    override fun onDestroy() {
        super.onDestroy()
        if (isBound) {
            service?.setStateListener(null)
            unbindService(serviceConnection)
            isBound = false
        }
    }

    private fun initViews() {
        // Content Containers
        tabDashboardLayout = findViewById(R.id.tabDashboard)
        tabConsoleLayout = findViewById(R.id.tabConsole)
        tabSettingsLayout = findViewById(R.id.tabSettings)

        // Bottom Navigation Buttons
        btnTabDashboard = findViewById(R.id.btnTabDashboard)
        btnTabConsole = findViewById(R.id.btnTabConsole)
        btnTabSettings = findViewById(R.id.btnTabSettings)

        // Web views
        webView = findViewById(R.id.webView)
        webProgressBar = findViewById(R.id.webProgressBar)
        loadingLayout = findViewById(R.id.loadingLayout)
        tvLoadingStatus = findViewById(R.id.tvLoadingStatus)

        // Console views
        tvPythonStatus = findViewById(R.id.tvPythonStatus)
        tvLlamaStatus = findViewById(R.id.tvLlamaStatus)
        btnStartServers = findViewById(R.id.btnStartServers)
        btnStopServers = findViewById(R.id.btnStopServers)
        consoleText = findViewById(R.id.consoleText)
        consoleScroll = findViewById(R.id.consoleScroll)
        btnClearConsole = findViewById(R.id.btnClearConsole)

        // Settings views
        tvActiveModel = findViewById(R.id.tvActiveModel)
        btnSelectLocalModel = findViewById(R.id.btnSelectLocalModel)
        btnDownloadModel = findViewById(R.id.btnDownloadModel)
        downloadProgressLayout = findViewById(R.id.downloadProgressLayout)
        downloadProgressBar = findViewById(R.id.downloadProgressBar)
        tvDownloadProgressPct = findViewById(R.id.tvDownloadProgressPct)
        tvDownloadSpeed = findViewById(R.id.tvDownloadSpeed)

        etUpstoxApiKey = findViewById(R.id.etUpstoxApiKey)
        etUpstoxSecret = findViewById(R.id.etUpstoxSecret)
        etRedirectUri = findViewById(R.id.etRedirectUri)
        btnSaveConfig = findViewById(R.id.btnSaveConfig)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.cacheMode = WebSettings.LOAD_DEFAULT
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                webProgressBar.visibility = View.GONE
            }
        }
    }

    private fun loadSavedConfig() {
        val prefs = getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE)
        etUpstoxApiKey.setText(prefs.getString("upstox_api_key", ""))
        etUpstoxSecret.setText(prefs.getString("upstox_secret", ""))
        etRedirectUri.setText(prefs.getString("redirect_uri", "http://localhost:8765/callback"))

        // Restore active model path if saved
        val savedModel = prefs.getString("active_model_path", null)
        if (savedModel != null && File(savedModel).exists()) {
            selectedModelPath = savedModel
            tvActiveModel.text = "Active Model: ${File(savedModel).name}"
        } else {
            // Check default download dir
            val modelsDir = File(getExternalFilesDir(null), "models")
            val defaultModel = File(modelsDir, "deepseek-r1-1.5b-q4_k_m.gguf")
            if (defaultModel.exists()) {
                selectedModelPath = defaultModel.absolutePath
                tvActiveModel.text = "Active Model: ${defaultModel.name}"
            }
        }
    }

    private fun setupListeners() {
        // Tab switching
        btnTabDashboard.setOnClickListener { selectTab(0) }
        btnTabConsole.setOnClickListener { selectTab(1) }
        btnTabSettings.setOnClickListener { selectTab(2) }

        // Start/Stop servers — no local model needed; analysis runs on NVIDIA NIM.
        btnStartServers.setOnClickListener {
            saveConfigToPreferences()
            service?.startServers()
        }

        btnStopServers.setOnClickListener {
            service?.stopServers()
        }

        btnClearConsole.setOnClickListener {
            consoleText.text = ""
        }

        // Save credentials
        btnSaveConfig.setOnClickListener {
            saveConfigToPreferences()
            Toast.makeText(this, "Credentials saved!", Toast.LENGTH_SHORT).show()
        }

        // Select custom model file picker
        btnSelectLocalModel.setOnClickListener {
            val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
                type = "*/*"
                addCategory(Intent.CATEGORY_OPENABLE)
            }
            startActivityForResult(Intent.createChooser(intent, "Select GGUF Model File"), modelPickRequestCode)
        }

        // Download model button
        btnDownloadModel.setOnClickListener {
            if (modelDownloader.isDownloading()) {
                modelDownloader.cancelDownload()
                btnDownloadModel.text = "Download Distill-Qwen-1.5B (1.1GB)"
                downloadProgressLayout.visibility = View.GONE
                Toast.makeText(this, "Download cancelled", Toast.LENGTH_SHORT).show()
            } else {
                val url = "https://huggingface.co/lmstudio-community/DeepSeek-R1-Distill-Qwen-1.5B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-1.5B-q4_k_m.gguf"
                val name = "deepseek-r1-1.5b-q4_k_m.gguf"
                btnDownloadModel.text = "Cancel Download"
                downloadProgressLayout.visibility = View.VISIBLE

                modelDownloader.startDownload(url, name, object : ModelDownloader.DownloadCallback {
                    override fun onProgress(progress: Int, speed: String) {
                        if (progress >= 0) {
                            downloadProgressBar.isIndeterminate = false
                            downloadProgressBar.progress = progress
                            tvDownloadProgressPct.text = "$progress%"
                        } else {
                            downloadProgressBar.isIndeterminate = true
                            tvDownloadProgressPct.text = "Downloading..."
                        }
                        tvDownloadSpeed.text = speed
                    }

                    override fun onSuccess(file: File) {
                        btnDownloadModel.text = "Download Distill-Qwen-1.5B (1.1GB)"
                        downloadProgressLayout.visibility = View.GONE
                        selectedModelPath = file.absolutePath
                        tvActiveModel.text = "Active Model: ${file.name}"
                        
                        // Save model path
                        getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE)
                            .edit()
                            .putString("active_model_path", file.absolutePath)
                            .apply()

                        Toast.makeText(this@MainActivity, "Model downloaded and active!", Toast.LENGTH_LONG).show()
                    }

                    override fun onFailure(error: String) {
                        btnDownloadModel.text = "Download Distill-Qwen-1.5B (1.1GB)"
                        downloadProgressLayout.visibility = View.GONE
                        Toast.makeText(this@MainActivity, "Download failed: $error", Toast.LENGTH_LONG).show()
                    }
                })
            }
        }
    }

    /** On-device model is gone — analysis runs on NVIDIA NIM (internet).
     *  Hide the download/select-model controls and relabel the row. */
    private fun configureCloudLlmUi() {
        tvActiveModel.text = "🧠 Analysis: NVIDIA NIM (cloud) — no local model needed"
        btnSelectLocalModel.visibility = View.GONE
        btnDownloadModel.visibility = View.GONE
        downloadProgressLayout.visibility = View.GONE
    }

    private fun selectTab(index: Int) {
        // Toggle tab layouts
        tabDashboardLayout.visibility = if (index == 0) View.VISIBLE else View.GONE
        tabConsoleLayout.visibility = if (index == 1) View.VISIBLE else View.GONE
        tabSettingsLayout.visibility = if (index == 2) View.VISIBLE else View.GONE

        // Update active tab buttons color
        btnTabDashboard.setTextColor(if (index == 0) 0xFF58A6FF.toInt() else 0xFF8B949E.toInt())
        btnTabConsole.setTextColor(if (index == 1) 0xFF58A6FF.toInt() else 0xFF8B949E.toInt())
        btnTabSettings.setTextColor(if (index == 2) 0xFF58A6FF.toInt() else 0xFF8B949E.toInt())
    }

    private fun saveConfigToPreferences() {
        val apiKey = etUpstoxApiKey.text.toString().trim()
        val secret = etUpstoxSecret.text.toString().trim()
        val redirectUri = etRedirectUri.text.toString().trim()

        getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE)
            .edit()
            .putString("upstox_api_key", apiKey)
            .putString("upstox_secret", secret)
            .putString("redirect_uri", redirectUri)
            .apply()
    }

    private fun updateStatusUI(llamaState: PortfolioService.ServerState, pythonState: PortfolioService.ServerState) {
        // "Llama" row now reflects the cloud LLM (NVIDIA NIM).
        tvLlamaStatus.text = if (llamaState == PortfolioService.ServerState.RUNNING)
            "NVIDIA NIM (cloud)" else llamaState.name
        when (llamaState) {
            PortfolioService.ServerState.RUNNING -> tvLlamaStatus.setTextColor(0xFF3FB950.toInt()) // Green
            PortfolioService.ServerState.STARTING -> tvLlamaStatus.setTextColor(0xFFD29922.toInt()) // Yellow
            PortfolioService.ServerState.ERROR -> tvLlamaStatus.setTextColor(0xFFF85149.toInt()) // Red
            PortfolioService.ServerState.STOPPED -> tvLlamaStatus.setTextColor(0xFF8B949E.toInt()) // Gray
        }

        tvPythonStatus.text = pythonState.name
        when (pythonState) {
            PortfolioService.ServerState.RUNNING -> {
                tvPythonStatus.setTextColor(0xFF3FB950.toInt()) // Green
                tvLoadingStatus.text = "Loading dashboard..."
                
                // load WebView with custom mobile header
                val headers = HashMap<String, String>()
                headers["x-client"] = "portfolio-mobile"
                webView.loadUrl("http://127.0.0.1:8000", headers)
                loadingLayout.visibility = View.GONE
            }
            PortfolioService.ServerState.STARTING -> {
                tvPythonStatus.setTextColor(0xFFD29922.toInt())
                loadingLayout.visibility = View.VISIBLE
                tvLoadingStatus.text = "FastAPI Backend Booting..."
            }
            PortfolioService.ServerState.ERROR -> {
                tvPythonStatus.setTextColor(0xFFF85149.toInt())
                loadingLayout.visibility = View.VISIBLE
                tvLoadingStatus.text = "Python Server failed to boot."
            }
            PortfolioService.ServerState.STOPPED -> {
                tvPythonStatus.setTextColor(0xFF8B949E.toInt())
                loadingLayout.visibility = View.VISIBLE
                tvLoadingStatus.text = "Servers are offline."
            }
        }
    }

    private fun appendConsoleLog(line: String) {
        consoleText.append("$line\n")
        consoleScroll.post {
            consoleScroll.fullScroll(View.FOCUS_DOWN)
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == modelPickRequestCode && resultCode == Activity.RESULT_OK) {
            data?.data?.let { uri ->
                val modelFile = copyUriToInternalStorage(uri)
                if (modelFile != null) {
                    selectedModelPath = modelFile.absolutePath
                    tvActiveModel.text = "Active Model: ${modelFile.name}"
                    
                    // Save model path
                    getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE)
                        .edit()
                        .putString("active_model_path", modelFile.absolutePath)
                        .apply()
                } else {
                    Toast.makeText(this, "Failed to load model file", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }

    private fun copyUriToInternalStorage(uri: Uri): File? {
        var fileName = "custom_model.gguf"
        contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (nameIndex != -1 && cursor.moveToFirst()) {
                fileName = cursor.getString(nameIndex)
            }
        }

        val modelsDir = File(getExternalFilesDir(null), "models")
        if (!modelsDir.exists()) modelsDir.mkdirs()

        val outFile = File(modelsDir, fileName)
        try {
            contentResolver.openInputStream(uri).use { input ->
                FileOutputStream(outFile).use { output ->
                    if (input == null) return null
                    val buffer = ByteArray(16 * 1024)
                    var read: Int
                    while (input.read(buffer).also { read = it } != -1) {
                        output.write(buffer, 0, read)
                    }
                    output.flush()
                }
            }
            return outFile
        } catch (e: Exception) {
            e.printStackTrace()
            return null
        }
    }
}
