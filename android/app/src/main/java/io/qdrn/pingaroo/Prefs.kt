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
    var wifiSsid: String
        get() = p.getString("wifi_ssid", "Kangaroo Labs") ?: "Kangaroo Labs"
        set(v) = p.edit().putString("wifi_ssid", v.trim()).apply()
    var wifiPassword: String
        get() = p.getString("wifi_pw", "") ?: ""
        set(v) = p.edit().putString("wifi_pw", v).apply()
    var bssid24: String
        get() = p.getString("bssid24", "dc:08:da:6d:43:18") ?: "dc:08:da:6d:43:18"
        set(v) = p.edit().putString("bssid24", v.trim().lowercase()).apply()
    var wpa3: Boolean
        get() = p.getBoolean("wpa3", false)
        set(v) = p.edit().putBoolean("wpa3", v).apply()
    var gatewayUser: String
        get() = p.getString("gw_user", "attadmin") ?: "attadmin"
        set(v) = p.edit().putString("gw_user", v.trim().ifEmpty { "attadmin" }).apply()
    var gatewayPassword: String
        get() = p.getString("gw_pw", "") ?: ""
        set(v) = p.edit().putString("gw_pw", v).apply()
    var logging: Boolean
        get() = p.getBoolean("logging", false)
        set(v) = p.edit().putBoolean("logging", v).apply()
}
