package io.qdrn.pingaroo

import android.content.Context
import android.net.ConnectivityManager
import android.net.MacAddress
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiNetworkSpecifier

/**
 * Asks Android for a connection to one specific access point (BSSID), e.g. the
 * gateway's 2.4 GHz radio.  Android shows a consent sheet once; while the request is
 * held the phone stays on that radio and the gateway reads are bound to it.
 */
class ForcedWifi(ctx: Context) {
    private val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    @Volatile var network: Network? = null
        private set
    @Volatile var state: String = "off"      // off | requesting | connected | failed
        private set
    @Volatile var detail: String = ""
    private var cb: ConnectivityManager.NetworkCallback? = null

    fun request(ssid: String, bssid: String, passphrase: String, wpa3: Boolean, onChange: () -> Unit) {
        release()
        val mac = if (bssid.isBlank()) null else try { MacAddress.fromString(bssid.trim()) } catch (e: IllegalArgumentException) {
            state = "failed"; detail = "invalid BSSID '$bssid' in Settings (expected aa:bb:cc:dd:ee:ff)"; onChange(); return
        }
        val spec = WifiNetworkSpecifier.Builder().setSsid(ssid).apply {
            if (mac != null) setBssid(mac)
            if (passphrase.isNotEmpty()) { if (wpa3) setWpa3Passphrase(passphrase) else setWpa2Passphrase(passphrase) }
        }.build()
        val req = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .setNetworkSpecifier(spec)
            .build()
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(n: Network) { network = n; state = "connected"; detail = ""; onChange() }
            override fun onUnavailable() { network = null; state = "failed"; detail = "Android could not connect to that radio (declined, out of range, wrong BSSID or password)"; onChange() }
            override fun onLost(n: Network) { if (network == n) { network = null; state = "failed"; detail = "connection lost"; onChange() } }
        }
        state = "requesting"; detail = ""
        try { cm.requestNetwork(req, callback); cb = callback } catch (e: Exception) { state = "failed"; detail = e.message ?: e.toString(); onChange() }
    }

    fun release() {
        cb?.let { try { cm.unregisterNetworkCallback(it) } catch (_: Exception) { } }
        cb = null; network = null; state = "off"; detail = ""
    }
}
