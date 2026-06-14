package com.portfolio.app

import android.annotation.SuppressLint
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.net.Uri
import android.os.Bundle
import android.os.IBinder
import android.view.LayoutInflater
import android.view.View
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.bottomnavigation.BottomNavigationView
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    private var service: PortfolioService? = null
    private var isBound = false

    // Core UI elements
    private lateinit var webView: WebView
    private lateinit var loadingLayout: View
    private lateinit var tvLoadingStatus: TextView
    private lateinit var consoleOverlay: View
    private lateinit var consoleText: TextView
    private lateinit var consoleScroll: ScrollView
    private lateinit var bottomNav: BottomNavigationView
    private lateinit var nativeOverlayContainer: View
    private lateinit var nativeContentLayout: LinearLayout
    private lateinit var toolbar: androidx.appcompat.widget.Toolbar

    // Global Settings elements
    private lateinit var tvActiveModel: TextView
    private lateinit var btnSelectLocalModel: Button
    private lateinit var btnDownloadModel: Button
    private lateinit var spnLlmProvider: Spinner
    private lateinit var etLlmApiKey: EditText
    private lateinit var etOllamaHost: EditText
    private lateinit var btnSaveGlobal: Button

    // App Settings elements
    private lateinit var etUpstoxApiKey: EditText
    private lateinit var etUpstoxSecret: EditText
    private lateinit var etRedirectUri: EditText
    private var oauthRedirectUri: String? = null   // set during in-app Upstox OAuth
    private lateinit var etUpstoxBaseUrl: EditText
    private lateinit var etUpstoxBearerToken: EditText
    private lateinit var btnTestToken: Button
    private lateinit var etTeleToken: EditText
    private lateinit var etTeleChatId: EditText
    private lateinit var btnSaveApp: Button
    private lateinit var spnBroker: Spinner
    private lateinit var etGrowwToken: EditText
    private lateinit var etGrowwApiKey: EditText
    private lateinit var etGrowwApiSecret: EditText

    private lateinit var modelDownloader: ModelDownloader
    private val modelPickRequestCode = 42

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val localBinder = binder as PortfolioService.LocalBinder
            service = localBinder.getService()
            isBound = true
            service?.setStateListener(stateListener)
            service?.logs?.toList()?.forEach { appendConsoleLog(it) }
        }
        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            isBound = false
        }
    }

    private val stateListener = object : PortfolioService.ServiceStateListener {
        override fun onStateChanged(llamaState: PortfolioService.ServerState, pythonState: PortfolioService.ServerState) {
            runOnUiThread {
                if (pythonState == PortfolioService.ServerState.RUNNING) {
                    loadingLayout.visibility = View.GONE
                    webView.reload()
                } else if (pythonState == PortfolioService.ServerState.STOPPED || pythonState == PortfolioService.ServerState.ERROR) {
                    loadingLayout.visibility = View.VISIBLE
                    tvLoadingStatus.text = if (pythonState == PortfolioService.ServerState.ERROR) 
                        "Backend Error. Check Console." else "Python Server Offline\nStart via Console (top middle)"
                }
            }
        }
        override fun onLogReceived(line: String) {
            runOnUiThread { appendConsoleLog(line) }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        initViews()
        setupWebView()
        setupNavigation()
        
        modelDownloader = ModelDownloader(this)

        val intent = Intent(this, PortfolioService::class.java)
        startService(intent)
        bindService(intent, serviceConnection, BIND_AUTO_CREATE)
    }

    private fun initViews() {
        toolbar = findViewById(R.id.toolbar)
        webView = findViewById(R.id.webView)
        loadingLayout = findViewById(R.id.loadingLayout)
        tvLoadingStatus = findViewById(R.id.tvLoadingStatus)
        consoleOverlay = findViewById(R.id.consoleOverlay)
        consoleText = findViewById(R.id.consoleText)
        consoleScroll = findViewById(R.id.consoleScroll)
        bottomNav = findViewById(R.id.bottom_navigation)
        nativeOverlayContainer = findViewById(R.id.nativeOverlayContainer)
        nativeContentLayout = findViewById(R.id.nativeContentLayout)

        // Top Action Buttons
        findViewById<ImageButton>(R.id.btnTopGlobalSettings).setOnClickListener { showNativePage("global") }
        findViewById<ImageButton>(R.id.btnTopConsole).setOnClickListener { consoleOverlay.visibility = View.VISIBLE }
        findViewById<ImageButton>(R.id.btnTopKB).setOnClickListener { 
            nativeOverlayContainer.visibility = View.GONE
            webView.visibility = View.VISIBLE
            toolbar.title = "Knowledge Base"
            webView.evaluateJavascript("document.querySelector('[data-tab=\"kb\"]').click();", null)
        }
        findViewById<ImageButton>(R.id.btnTopAppSettings).setOnClickListener { showNativePage("app") }
        
        // Console Actions
        findViewById<ImageButton>(R.id.btnCopyLogs).setOnClickListener {
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
            val clip = android.content.ClipData.newPlainText("Logs", consoleText.text.toString())
            clipboard.setPrimaryClip(clip)
            Toast.makeText(this, "Logs copied to clipboard", Toast.LENGTH_SHORT).show()
        }
        
        findViewById<ImageButton>(R.id.btnDownloadLogs).setOnClickListener {
            try {
                val file = File(getExternalFilesDir(null), "portfolio_logs.txt")
                file.writeText(consoleText.text.toString())
                Toast.makeText(this, "Logs saved to: ${file.absolutePath}", Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                Toast.makeText(this, "Failed to save logs", Toast.LENGTH_SHORT).show()
            }
        }
        
        consoleText.setTextIsSelectable(true)

        findViewById<ImageButton>(R.id.btnCloseConsole).setOnClickListener { consoleOverlay.visibility = View.GONE }
        findViewById<Button>(R.id.btnStartServers).setOnClickListener {
            saveAllToPrefs()
            service?.startServers()
            consoleOverlay.visibility = View.GONE
        }
        findViewById<Button>(R.id.btnStopServers).setOnClickListener { service?.stopServers() }
    }

    private fun showNativePage(type: String) {
        nativeOverlayContainer.visibility = View.VISIBLE
        nativeContentLayout.removeAllViews()
        val inflater = LayoutInflater.from(this)
        
        if (type == "global") {
            val v = inflater.inflate(R.layout.layout_global_settings, nativeContentLayout, true)
            tvActiveModel = v.findViewById(R.id.tvActiveModel)
            btnSelectLocalModel = v.findViewById(R.id.btnSelectLocalModel)
            btnDownloadModel = v.findViewById(R.id.btnDownloadModel)
            spnLlmProvider = v.findViewById(R.id.spnLlmProvider)
            etLlmApiKey = v.findViewById(R.id.etLlmApiKey)
            etOllamaHost = v.findViewById(R.id.etOllamaHost)
            btnSaveGlobal = v.findViewById(R.id.btnSaveGlobal)
            
            btnSaveGlobal.setOnClickListener { saveAllToPrefs(); nativeOverlayContainer.visibility = View.GONE }
            btnSelectLocalModel.setOnClickListener {
                val intent = Intent(Intent.ACTION_GET_CONTENT)
                intent.type = "*/*"
                startActivityForResult(Intent.createChooser(intent, "Select GGUF"), modelPickRequestCode)
            }
            loadGlobalUI()
        } else {
            val v = inflater.inflate(R.layout.layout_app_settings, nativeContentLayout, true)
            etUpstoxApiKey = v.findViewById(R.id.etUpstoxApiKey)
            etUpstoxSecret = v.findViewById(R.id.etUpstoxSecret)
            etRedirectUri = v.findViewById(R.id.etRedirectUri)
            etUpstoxBaseUrl = v.findViewById(R.id.etUpstoxBaseUrl)
            etUpstoxBearerToken = v.findViewById(R.id.etUpstoxBearerToken)
            btnTestToken = v.findViewById(R.id.btnTestToken)
            etTeleToken = v.findViewById(R.id.etTeleToken)
            etTeleChatId = v.findViewById(R.id.etTeleChatId)
            btnSaveApp = v.findViewById(R.id.btnSaveApp)
            spnBroker = v.findViewById(R.id.spnBroker)
            etGrowwToken = v.findViewById(R.id.etGrowwToken)
            etGrowwApiKey = v.findViewById(R.id.etGrowwApiKey)
            etGrowwApiSecret = v.findViewById(R.id.etGrowwApiSecret)
            val brokers = arrayOf("upstox", "groww")
            spnBroker.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, brokers).apply {
                setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
            }

            btnSaveApp.setOnClickListener { saveAllToPrefs(); nativeOverlayContainer.visibility = View.GONE }
            btnTestToken.setOnClickListener { testUpstoxToken() }
            loadAppUI()
        }
    }

    private fun testUpstoxToken() {
        if (service?.pythonState != PortfolioService.ServerState.RUNNING) {
            Toast.makeText(this, "Start Servers first via Terminal", Toast.LENGTH_LONG).show()
            return
        }
        
        saveAllToPrefs() // Save to disk
        
        val token = etUpstoxBearerToken.text.toString().trim()
        
        // Push the new token directly into the running Python environment for immediate test
        try {
            val py = com.chaquo.python.Python.getInstance()
            val os = py.getModule("os")
            val environ = os["environ"]
            environ?.callAttr("__setitem__", "UPSTOX_BEARER_TOKEN", token)
        } catch (e: Exception) {
            // fallback to disk if JNI fails
        }
        
        btnTestToken.text = "Testing..."
        btnTestToken.isEnabled = false
        
        // Execute JS in background to call our new API endpoint
        val js = """
            fetch('/api/upstox/test-token', {method: 'POST'})
                .then(r => r.json())
                .then(d => {
                    if (d.ok) { 
                        alert('SUCCESS: ' + d.message);
                        // Force dashboard to reload portfolio data immediately
                        if (typeof loadPortfolio === 'function') { loadPortfolio(); }
                    }
                    else { alert('FAILED: ' + d.message); }
                })
                .catch(e => alert('Error: ' + e));
        """.trimIndent()
        
        webView.evaluateJavascript(js) {
            btnTestToken.text = "Test Authentication"
            btnTestToken.isEnabled = true
        }
    }

    private fun loadGlobalUI() {
        val prefs = getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE)
        val providers = arrayOf("nvidia", "anthropic", "llamacpp")
        spnLlmProvider.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, providers).apply {
            setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        }
        spnLlmProvider.setSelection(providers.indexOf(prefs.getString("llm_provider", "nvidia")))
        etLlmApiKey.setText(prefs.getString("llm_api_key", ""))
        etOllamaHost.setText(prefs.getString("ollama_host", "http://127.0.0.1:11434"))
        
        val savedModel = prefs.getString("active_model_path", null)
        tvActiveModel.text = if (savedModel != null) File(savedModel).name else "None selected"
    }

    private fun loadAppUI() {
        val prefs = getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE)
        etUpstoxApiKey.setText(prefs.getString("upstox_api_key", ""))
        etUpstoxSecret.setText(prefs.getString("upstox_secret", ""))
        etRedirectUri.setText(prefs.getString("redirect_uri", "http://127.0.0.1:8765/callback"))
        etUpstoxBaseUrl.setText(prefs.getString("upstox_base_url", "https://api.upstox.com/v2"))
        etUpstoxBearerToken.setText(prefs.getString("upstox_bearer_token", ""))
        etTeleToken.setText(prefs.getString("tele_token", ""))
        etTeleChatId.setText(prefs.getString("tele_chat_id", ""))
        if (::spnBroker.isInitialized) {
            val b = prefs.getString("broker", "upstox") ?: "upstox"
            spnBroker.setSelection(if (b == "groww") 1 else 0)
            etGrowwToken.setText(prefs.getString("groww_access_token", ""))
            etGrowwApiKey.setText(prefs.getString("groww_api_key", ""))
            etGrowwApiSecret.setText(prefs.getString("groww_api_secret", ""))
        }
    }

    private fun saveAllToPrefs() {
        val prefs = getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE).edit()
        
        // We only save if the views are currently inflated/accessible
        if (::etLlmApiKey.isInitialized) {
            prefs.putString("llm_api_key", etLlmApiKey.text.toString().trim())
            prefs.putString("llm_provider", spnLlmProvider.selectedItem.toString())
            prefs.putString("ollama_host", etOllamaHost.text.toString().trim())
        }
        if (::etUpstoxApiKey.isInitialized) {
            prefs.putString("upstox_api_key", etUpstoxApiKey.text.toString().trim())
            prefs.putString("upstox_secret", etUpstoxSecret.text.toString().trim())
            prefs.putString("redirect_uri", etRedirectUri.text.toString().trim())
            prefs.putString("upstox_base_url", etUpstoxBaseUrl.text.toString().trim())
            prefs.putString("upstox_bearer_token", etUpstoxBearerToken.text.toString().trim())
            prefs.putString("tele_token", etTeleToken.text.toString().trim())
            prefs.putString("tele_chat_id", etTeleChatId.text.toString().trim())
        }
        if (::spnBroker.isInitialized) {
            prefs.putString("broker", spnBroker.selectedItem.toString())
            prefs.putString("groww_access_token", etGrowwToken.text.toString().trim())
            prefs.putString("groww_api_key", etGrowwApiKey.text.toString().trim())
            prefs.putString("groww_api_secret", etGrowwApiSecret.text.toString().trim())
        }
        prefs.apply()
        Toast.makeText(this, "Settings Saved Locally", Toast.LENGTH_SHORT).show()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            cacheMode = WebSettings.LOAD_DEFAULT
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }
        webView.addJavascriptInterface(object {
            @android.webkit.JavascriptInterface
            fun openBrowser(url: String) { runOnUiThread { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } }

            // In-app Upstox OAuth: load the auth URL in THIS webview and
            // auto-capture the ?code= when Upstox redirects to the callback.
            @android.webkit.JavascriptInterface
            fun startUpstoxLogin(authUrl: String, redirectUri: String) {
                oauthRedirectUri = redirectUri
                runOnUiThread { webView.loadUrl(authUrl) }
            }
        }, "AndroidApp")

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: android.webkit.WebResourceRequest?): Boolean {
                val url = request?.url?.toString() ?: return false
                val redir = oauthRedirectUri
                val looksLikeCallback = (redir != null && url.startsWith(redir)) ||
                    (url.contains("/callback") && url.contains("code="))
                if (looksLikeCallback) {
                    val code = request?.url?.getQueryParameter("code")
                    if (!code.isNullOrBlank()) {
                        oauthRedirectUri = null
                        exchangeUpstoxCode(url)
                        return true   // don't load the dead localhost page
                    }
                }
                return false
            }
            override fun onPageFinished(view: WebView?, url: String?) {
                // Only restyle OUR dashboard — never Upstox's login page.
                if (url != null && url.contains("127.0.0.1:8000")) injectMobileMode()
            }
        }
        
        webView.webChromeClient = object : android.webkit.WebChromeClient() {
            override fun onJsAlert(view: WebView?, url: String?, message: String?, result: android.webkit.JsResult?): Boolean {
                Toast.makeText(this@MainActivity, message, Toast.LENGTH_LONG).show()
                result?.confirm()
                return true
            }
        }

        webView.loadUrl("http://127.0.0.1:8000")
    }

    /** POST the captured OAuth redirect URL to the local backend, which
     *  exchanges it for an access token. Runs off the UI thread. */
    private fun exchangeUpstoxCode(fullUrl: String) {
        runOnUiThread { Toast.makeText(this, "Completing Upstox login…", Toast.LENGTH_SHORT).show() }
        Thread {
            var ok = false
            var msg = "exchange failed"
            try {
                val conn = (URL("http://127.0.0.1:8000/api/upstox/exchange-code")
                    .openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 15000; readTimeout = 30000
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                    setRequestProperty("x-client", "portfolio-mobile")
                }
                conn.outputStream.use {
                    it.write(JSONObject().put("code_or_url", fullUrl).toString().toByteArray())
                }
                val body = (if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream)
                    ?.bufferedReader()?.readText() ?: ""
                val j = JSONObject(body)
                ok = j.optBoolean("ok", false)
                msg = if (ok) "Logged in as ${j.optString("user", "you")}" else j.optString("error", msg)
            } catch (e: Exception) {
                msg = e.message ?: "network error"
            }
            runOnUiThread {
                Toast.makeText(this, if (ok) "✅ $msg" else "❌ $msg", Toast.LENGTH_LONG).show()
                // Back to the dashboard either way
                webView.loadUrl("http://127.0.0.1:8000")
            }
        }.start()
    }

    private fun injectMobileMode() {
        // Aggressively hide all web headers, navs, and titles to reclaim screen space
        val css = """
            header, nav, footer, .tab-nav, h1, .tab-header { display: none !important; }
            main { padding-top: 0 !important; margin-top: 0 !important; margin-bottom: 20px !important; }
            body { background-color: #0B0E14 !important; }
            .card { border-radius: 12px !important; background: #161B22 !important; border: 1px solid #30363D !important; }
        """.trimIndent().replace("\n", "")
        webView.evaluateJavascript("const style = document.createElement('style'); style.innerHTML = '$css'; document.head.appendChild(style);", null)
        webView.evaluateJavascript("document.body.classList.add('native-mode');", null)
    }

    private fun setupNavigation() {
        bottomNav.setOnItemSelectedListener { item ->
            nativeOverlayContainer.visibility = View.GONE
            webView.visibility = View.VISIBLE
            
            val (tabId, title) = when (item.itemId) {
                R.id.nav_portfolio -> "portfolio" to "Portfolio"
                R.id.nav_performance -> "performance" to "Performance"
                R.id.nav_optimize -> "optimize" to "MPT Optimization"
                R.id.nav_quant -> "quant" to "D-R1-Quant"
                R.id.nav_umap -> "umap" to "Universe Map"
                else -> "portfolio" to "Portfolio"
            }
            toolbar.title = title
            // Click the hidden web tab button to switch data source
            webView.evaluateJavascript("document.querySelector('[data-tab=\"$tabId\"]').click();", null)
            
            // If switching to portfolio/performance, force a data fetch
            if (tabId == "portfolio" || tabId == "performance") {
                webView.evaluateJavascript("if(typeof loadPortfolio === 'function') loadPortfolio();", null)
                webView.evaluateJavascript("if(typeof loadPerformance === 'function') loadPerformance();", null)
            }
            true
        }
    }

    private fun appendConsoleLog(line: String) {
        consoleText.append("$line\n")
        consoleScroll.post { consoleScroll.fullScroll(View.FOCUS_DOWN) }
    }

    override fun onBackPressed() {
        if (consoleOverlay.visibility == View.VISIBLE) {
            consoleOverlay.visibility = View.GONE
        } else if (nativeOverlayContainer.visibility == View.VISIBLE) {
            nativeOverlayContainer.visibility = View.GONE
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        unbindService(serviceConnection)
    }
}
