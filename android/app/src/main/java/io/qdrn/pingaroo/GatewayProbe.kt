package io.qdrn.pingaroo

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL

/** Step-by-step reachability test of the gateway over the Wi-Fi network. */
object GatewayProbe {
    @Suppress("DEPRECATION")
    suspend fun run(ctx: Context, baseUrl: String, wifiNet: Network?, ssid: String): String = withContext(Dispatchers.IO) {
        val sb = StringBuilder()
        val host = baseUrl.removePrefix("http://").removePrefix("https://").trimEnd('/')
        sb.append("SSID: ${ssid.ifEmpty { "?" }}   bound network: ${if (wifiNet != null) "Wi-Fi" else "NONE (default)"}\n")
        if (wifiNet != null) {
            try {
                val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
                val lp = cm.getLinkProperties(wifiNet)
                val caps = cm.getNetworkCapabilities(wifiNet)
                sb.append("Bound net addresses: ${lp?.linkAddresses?.joinToString() ?: "none"}\n")
                sb.append("Bound net routes: ${lp?.routes?.filter { it.isDefaultRoute }?.joinToString { it.gateway?.hostAddress ?: "?" } ?: "none"}  dns: ${lp?.dnsServers?.joinToString { it.hostAddress ?: "?" }}\n")
                sb.append("Bound net validated: ${caps?.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_VALIDATED)}\n")
            } catch (e: Exception) { sb.append("link properties: ${e.message}\n") }
        }
        try {
            val wm = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            val info = wm.connectionInfo
            val freq = info.frequency
            val band = when { freq in 2400..2500 -> "2.4 GHz"; freq in 5000..5900 -> "5 GHz"; freq > 5900 -> "6 GHz"; else -> "?" }
            sb.append("Band: $band ($freq MHz)  BSSID ${info.bssid}  link ${info.linkSpeed} Mbps  RSSI ${info.rssi} dBm\n")
            val d = wm.dhcpInfo
            fun ip(v: Int) = "${v and 0xff}.${v shr 8 and 0xff}.${v shr 16 and 0xff}.${v shr 24 and 0xff}"
            sb.append("DHCP: ip ${ip(d.ipAddress)}  gateway ${ip(d.gateway)}  dns ${ip(d.dns1)}\n")
            sb.append("MAC reported to app: ${info.macAddress} (02:00:.. means hidden; check MAC type in Wi-Fi settings)\n")
        } catch (e: Exception) { sb.append("wifi info: ${e.message}\n") }
        sb.append("\n")
        // 1. TCP connect
        val t0 = System.currentTimeMillis()
        try {
            val s = wifiNet?.socketFactory?.createSocket() ?: Socket()
            s.connect(InetSocketAddress(host, 80), 3000); s.close()
            sb.append("TCP $host:80  OK (${System.currentTimeMillis() - t0} ms)\n")
        } catch (e: Exception) { sb.append("TCP $host:80  FAIL ${e.javaClass.simpleName}: ${e.message}\n") }
        // 2. HTTP root and the API, bound and unbound
        for ((label, path) in listOf("GET /" to "/", "GET /cgi-bin/GetENDCInfo" to "/cgi-bin/GetENDCInfo")) {
            for ((how, net) in listOf("bound" to wifiNet, "default" to null)) {
                if (how == "bound" && wifiNet == null) continue
                val t1 = System.currentTimeMillis()
                try {
                    val url = URL("http://$host$path")
                    val c = (net?.openConnection(url) ?: url.openConnection()) as HttpURLConnection
                    c.connectTimeout = 3000; c.readTimeout = 3000; c.useCaches = false
                    c.setRequestProperty("X-Requested-With", "XMLHttpRequest")
                    val code = c.responseCode
                    val body = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.readText() ?: ""
                    c.disconnect()
                    sb.append("$label [$how]  HTTP $code, ${body.length} bytes (${System.currentTimeMillis() - t1} ms)  ${body.take(60).replace("\n", " ")}\n")
                } catch (e: Exception) { sb.append("$label [$how]  FAIL ${e.javaClass.simpleName}: ${e.message}\n") }
            }
        }
        sb.toString()
    }
}
