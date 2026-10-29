package io.qdrn.pingaroo

import android.content.Context

/** Persisted settings and session state. */
class Prefs(ctx: Context) {
    private val p = ctx.getSharedPreferences("pingaroo", Context.MODE_PRIVATE)

    var source: String
        get() = p.getString("source", "gateway") ?: "gateway"
        set(v) = p.edit().putString("source", v).apply()
    var label: String
        get() = p.getString("label", "") ?: ""
        set(v) = p.edit().putString("label", v).apply()
    var gatewayUrl: String
        get() = p.getString("gateway_url", "http://192.168.1.1") ?: "http://192.168.1.1"
        set(v) = p.edit().putString("gateway_url", v.trim().ifEmpty { "http://192.168.1.1" }).apply()
    var pollMs: Long
        get() = p.getLong("poll_ms", 2000L)
        set(v) = p.edit().putLong("poll_ms", v).apply()
    var tone: Boolean
        get() = p.getBoolean("tone", false)
        set(v) = p.edit().putBoolean("tone", v).apply()
    var keepScreenOn: Boolean
        get() = p.getBoolean("keep_on", true)
        set(v) = p.edit().putBoolean("keep_on", v).apply()
    var autoUpdate: Boolean
        get() = p.getBoolean("auto_update", true)
        set(v) = p.edit().putBoolean("auto_update", v).apply()
    var logging: Boolean
        get() = p.getBoolean("logging", false)
        set(v) = p.edit().putBoolean("logging", v).apply()
}
