package io.qdrn.pingaroo

import android.net.Network
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Reads cellular status from the AT&T Internet Air (NCM1120) gateway over Wi-Fi.
 * Every request is bound to the Wi-Fi Network when one is known, so it never leaks
 * onto mobile data when Android has demoted the Wi-Fi for lacking internet.
 */
class GatewayClient(private val baseUrl: String = "http://192.168.1.1", private val wifi: () -> Network? = { null }) {

    private var slowCache: JSONObject? = null
    private var slowCacheAt = 0L

    private fun get(path: String, timeoutMs: Int = 2500): JSONObject {
        val url = URL(baseUrl.trimEnd('/') + path)
        val raw = wifi()?.openConnection(url) ?: url.openConnection()
        val conn = (raw as HttpURLConnection).apply {
            connectTimeout = timeoutMs
            readTimeout = timeoutMs
            requestMethod = "GET"
            useCaches = false
            setRequestProperty("Accept", "application/json, */*")
            setRequestProperty("X-Requested-With", "XMLHttpRequest")
            setRequestProperty("User-Agent", "pingaroo-mobile/" + BuildConfig.VERSION_NAME)
        }
        try {
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)?.bufferedReader()?.readText() ?: ""
            if (code >= 400) throw IllegalStateException("HTTP $code")
            return JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }

    /** Verbatim responses of the endpoints the app reads, for diagnosing parse gaps. */
    suspend fun rawDump(): String = withContext(Dispatchers.IO) {
        val sb = StringBuilder()
        for (p in listOf("/cgi-bin/GetENDCInfo", "/cgi-bin/GetLTEStatus", "/cgi-bin/GetLTEBasicSetting", "/cgi-bin/GetWanIp")) {
            sb.append("== $p ==\n")
            try {
                val url = URL(baseUrl.trimEnd('/') + p)
                val c = ((wifi()?.openConnection(url) ?: url.openConnection()) as HttpURLConnection).apply {
                    connectTimeout = 2500; readTimeout = 2500; useCaches = false
                    setRequestProperty("Accept", "application/json, */*"); setRequestProperty("X-Requested-With", "XMLHttpRequest")
                }
                val code = c.responseCode
                val body = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.readText() ?: ""
                c.disconnect()
                sb.append("HTTP $code\n").append(body.take(1200)).append("\n\n")
            } catch (e: Exception) { sb.append("FAIL ${e.javaClass.simpleName}: ${e.message}\n\n") }
        }
        sb.toString()
    }

    private fun JSONObject.intOrNull(key: String): Int? =
        optString(key, "").trim().removeSuffix("dBm").removeSuffix("dB").trim().toIntOrNull()

    suspend fun fetch(label: String): Sample = withContext(Dispatchers.IO) {
        val t0 = System.currentTimeMillis()
        try {
            val endc = get("/cgi-bin/GetENDCInfo")
            val now = System.currentTimeMillis()
            if (slowCache == null || now - slowCacheAt > 30_000) {
                slowCacheAt = now                       // back off even if these fail
                val merged = JSONObject()
                for (p in listOf("/cgi-bin/GetLTEStatus", "/cgi-bin/GetLTEBasicSetting", "/cgi-bin/GetWanIp")) {
                    try { val j = get(p, 2000); for (k in j.keys()) merged.put(k, j.get(k)) } catch (_: Exception) { }
                }
                if (merged.length() > 0) slowCache = merged
            }
            val slow = slowCache ?: JSONObject()
            val anchor = endc.optJSONArray("data")?.optJSONObject(0)
            Sample(
                source = "gateway",
                label = label,
                tech = slow.optString("net_type", ""),
                operator = slow.optString("operator", ""),
                band = endc.optString("nrband", ""),
                arfcn = endc.intOrNull("nrchannel"),
                pci = endc.intOrNull("nr5g_pci"),
                tac = endc.intOrNull("nr5g_tac"),
                cellId = endc.optString("nr5g_gci", "").toLongOrNull(),
                rsrp = endc.intOrNull("nr5g_rsrp"),
                rsrq = endc.intOrNull("nr5g_rsrq"),
                sinr = endc.intOrNull("nr5g_snr"),
                rssi = endc.intOrNull("nr5g_rssi"),
                bandwidth = endc.optString("nrbandw", ""),
                lteBand = anchor?.optString("band", "") ?: "",
                ltePci = anchor?.intOrNull("pci"),
                lteCellId = anchor?.optString("gci", "")?.toLongOrNull(),
                lteTac = anchor?.intOrNull("tac"),
                lteRsrp = anchor?.intOrNull("rsrp"),
                lteRsrq = anchor?.intOrNull("rsrq"),
                lteRssnr = anchor?.intOrNull("sinr"),
                apn = slow.optString("APN_Name", ""),
                wanIp = slow.optString("ConnIP", ""),
                fetchMs = System.currentTimeMillis() - t0,
            )
        } catch (e: Exception) {
            Sample(source = "gateway", label = label, error = (e.message ?: e.javaClass.simpleName).take(80))
        }
    }
}
