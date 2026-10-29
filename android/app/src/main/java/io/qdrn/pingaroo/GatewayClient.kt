package io.qdrn.pingaroo

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Reads cellular status from the AT&T Internet Air (NCM1120) gateway over its Wi-Fi.
 * The status endpoints answer without a session; only the LAN address is contacted.
 */
class GatewayClient(private val baseUrl: String = "http://192.168.1.1") {

    private var slowCache: JSONObject? = null
    private var slowCacheAt = 0L

    private fun get(path: String, timeoutMs: Int = 3000): JSONObject {
        val url = URL(baseUrl.trimEnd('/') + path)
        val conn = (url.openConnection() as HttpURLConnection).apply {
            connectTimeout = timeoutMs
            readTimeout = timeoutMs
            requestMethod = "GET"
            setRequestProperty("Accept", "application/json, */*")
            setRequestProperty("X-Requested-With", "XMLHttpRequest")
            setRequestProperty("User-Agent", "pingaroo-mobile/1.0")
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

    private fun JSONObject.intOrNull(key: String): Int? =
        optString(key, "").trim().removeSuffix("dBm").removeSuffix("dB").trim().toIntOrNull()

    suspend fun fetch(label: String): Sample = withContext(Dispatchers.IO) {
        try {
            val endc = get("/cgi-bin/GetENDCInfo")
            val now = System.currentTimeMillis()
            if (slowCache == null || now - slowCacheAt > 30_000) {
                val merged = JSONObject()
                for (p in listOf("/cgi-bin/GetLTEStatus", "/cgi-bin/GetLTEBasicSetting", "/cgi-bin/GetWanIp")) {
                    try {
                        val j = get(p)
                        for (k in j.keys()) merged.put(k, j.get(k))
                    } catch (_: Exception) { }
                }
                slowCache = merged
                slowCacheAt = now
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
                lteRsrp = anchor?.intOrNull("rsrp"),
                lteRsrq = anchor?.intOrNull("rsrq"),
                lteRssnr = anchor?.intOrNull("sinr"),
                apn = slow.optString("APN_Name", ""),
                wanIp = slow.optString("ConnIP", ""),
            )
        } catch (e: Exception) {
            Sample(source = "gateway", label = label, error = (e.message ?: e.javaClass.simpleName).take(80))
        }
    }
}
