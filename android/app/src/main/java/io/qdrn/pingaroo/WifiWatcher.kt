package io.qdrn.pingaroo

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Build

/** Current Wi-Fi SSID, for the context line ("phone is on Kangaroo Labs"). */
class WifiWatcher(private val ctx: Context) {
    private val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    @Volatile var ssid: String = ""
        private set
    @Volatile var onWifi = false
        private set
    private var cb: ConnectivityManager.NetworkCallback? = null

    fun start() {
        if (cb != null) return
        val callback = if (Build.VERSION.SDK_INT >= 31) {
            object : ConnectivityManager.NetworkCallback(FLAG_INCLUDE_LOCATION_INFO) {
                override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) { update(caps) }
                override fun onLost(network: Network) { onWifi = false; ssid = "" }
            }
        } else {
            object : ConnectivityManager.NetworkCallback() {
                override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) { update(caps) }
                override fun onLost(network: Network) { onWifi = false; ssid = "" }
            }
        }
        try { cm.registerDefaultNetworkCallback(callback); cb = callback } catch (_: Exception) { }
    }

    @Suppress("DEPRECATION")
    private fun update(caps: NetworkCapabilities) {
        onWifi = caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
        if (!onWifi) { ssid = ""; return }
        val info = (caps.transportInfo as? WifiInfo)
            ?: try { (ctx.getSystemService(Context.WIFI_SERVICE) as WifiManager).connectionInfo } catch (_: Exception) { null }
        ssid = info?.ssid?.trim('"')?.takeIf { it != "<unknown ssid>" } ?: ""
    }

    fun stop() { cb?.let { try { cm.unregisterNetworkCallback(it) } catch (_: Exception) { } }; cb = null }
}
