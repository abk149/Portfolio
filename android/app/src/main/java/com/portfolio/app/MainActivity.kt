package com.portfolio.app

import android.content.ComponentName
import android.content.Intent
import android.content.ServiceConnection
import android.os.Bundle
import android.os.IBinder
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.portfolio.app.ui.AppRoot
import com.portfolio.app.ui.BackendBus
import com.portfolio.app.ui.PortfolioTheme

/**
 * Native (Jetpack Compose) host. The heavy lifting — the Chaquopy FastAPI
 * backend and its log stream — still lives in [PortfolioService]; this activity
 * binds to it and bridges state/logs/controls into Compose via [BackendBus].
 */
class MainActivity : ComponentActivity() {

    private var service: PortfolioService? = null

    private val stateListener = object : PortfolioService.ServiceStateListener {
        override fun onStateChanged(
            llamaState: PortfolioService.ServerState,
            pythonState: PortfolioService.ServerState,
        ) {
            BackendBus.state.value = when (pythonState) {
                PortfolioService.ServerState.RUNNING -> BackendBus.State.RUNNING
                PortfolioService.ServerState.STARTING -> BackendBus.State.STARTING
                PortfolioService.ServerState.ERROR -> BackendBus.State.ERROR
                else -> BackendBus.State.STOPPED
            }
        }
        override fun onLogReceived(line: String) {
            runOnUiThread { BackendBus.pushLog(line) }
        }
    }

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val svc = (binder as PortfolioService.LocalBinder).getService()
            service = svc
            svc.setStateListener(stateListener)
            BackendBus.setLogs(svc.logs.toList())
            BackendBus.onStart = { svc.startServers() }
            BackendBus.onStop = { svc.stopServers() }
        }
        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            BackendBus.onStart = {}
            BackendBus.onStop = {}
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val intent = Intent(this, PortfolioService::class.java)
        startService(intent)
        bindService(intent, connection, BIND_AUTO_CREATE)

        setContent { PortfolioTheme { AppRoot() } }
    }

    override fun onDestroy() {
        super.onDestroy()
        service?.setStateListener(null)
        runCatching { unbindService(connection) }
    }
}
