package io.qdrn.pingaroo

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Build

/**
 * Tracks the Wi-Fi network itself (not Android's "default" network, which flips to
 * mobile data when the Wi-Fi has no validated internet).  Exposes the Network so
 * gateway requests can be bound to it explicitly.
 */
class WifiWatcher(private val ctx: Context) {
    private val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    @Volatile var network: Network? = null
        private set
    @Volatile var ssid: String = ""
        private set
    @Volatile var validated = false
        private set
    val onWifi: Boolean get() = network != null

    /** True when a VPN network is active on the phone (it captures LAN-bound traffic). */
    fun vpnActive(): Boolean = try {
        cm.allNetworks.any { n -> cm.getNetworkCapabilities(n)?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true }
    } catch (_: Exception) { false }
    private var cb: ConnectivityManager.NetworkCallback? = null

    fun start() {
        if (cb != null) return
        val request = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build()
        val callback = if (Build.VERSION.SDK_INT >= 31) {
            object : ConnectivityManager.NetworkCallback(FLAG_INCLUDE_LOCATION_INFO) {
                override fun onAvailable(n: Network) { network = n }
                override fun onCapabilitiesChanged(n: Network, caps: NetworkCapabilities) { network = n; update(caps) }
                override fun onLost(n: Network) { if (network == n) { network = null; ssid = ""; validated = false } }
            }
        } else {
            object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(n: Network) { network = n }
                override fun onCapabilitiesChanged(n: Network, caps: NetworkCapabilities) { network = n; update(caps) }
                override fun onLost(n: Network) { if (network == n) { network = null; ssid = ""; validated = false } }
            }
        }
        try { cm.registerNetworkCallback(request, callback); cb = callback } catch (_: Exception) { }
        // seed from whatever is up right now
        try {
            cm.activeNetwork?.let { n -> cm.getNetworkCapabilities(n)?.let { c -> if (c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) { network = n; update(c) } } }
        } catch (_: Exception) { }
    }

    @Suppress("DEPRECATION")
    private fun update(caps: NetworkCapabilities) {
        validated = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        val info = (caps.transportInfo as? WifiInfo)
            ?: try { (ctx.getSystemService(Context.WIFI_SERVICE) as WifiManager).connectionInfo } catch (_: Exception) { null }
        ssid = info?.ssid?.trim('"')?.takeIf { it != "<unknown ssid>" } ?: ""
    }

    fun stop() { cb?.let { try { cm.unregisterNetworkCallback(it) } catch (_: Exception) { } }; cb = null }
}
