package com.portfolio.app.ui

import android.content.Context

/** Typed accessor over the same SharedPreferences the Service reads at boot. */
class Prefs(ctx: Context) {
    private val sp = ctx.getSharedPreferences("PortfolioQuantPrefs", Context.MODE_PRIVATE)

    fun get(key: String, def: String = ""): String = sp.getString(key, def) ?: def
    fun put(vararg pairs: Pair<String, String>) {
        sp.edit().apply { pairs.forEach { (k, v) -> putString(k, v) } }.apply()
    }

    var broker: String
        get() = get("broker", "upstox")
        set(v) = put("broker" to v)
}
