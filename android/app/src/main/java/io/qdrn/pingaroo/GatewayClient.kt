package io.qdrn.pingaroo

import android.net.Network
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.CookieHandler
import java.net.CookieManager
import java.net.CookiePolicy
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Reads cellular status from the AT&T Internet Air (NCM1120) gateway over Wi-Fi.
 * Every request is bound to the Wi-Fi Network when one is known, so it never leaks
 * onto mobile data when Android has demoted the Wi-Fi for lacking internet.
 */
class GatewayClient(
    private val baseUrl: String = "http://192.168.1.1",
    private val wifi: () -> Network? = { null },
    private val username: String = "attadmin",
    private val password: String = "",
) {

    init {
        // The gateway returns its session as a cookie after Login; HttpURLConnection only
        // keeps cookies when a process-wide CookieHandler is installed.
        if (CookieHandler.getDefault() == null) CookieHandler.setDefault(CookieManager(null, CookiePolicy.ACCEPT_ALL))
    }

    private var slowCache: JSONObject? = null
    private var slowCacheAt = 0L
    @Volatile var loggedIn = false
        private set
    @Volatile var loginError = ""
        private set

    private fun sha256Hex(text: String): String =
        MessageDigest.getInstance("SHA-256").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }

    /** Log in exactly as the gateway's own login page does (script/login.js):
     *  POST JSON {UserName, Password: SHA-256("UserName:password") hex, token: ""}.
     *  The session comes back as a cookie, kept by the CookieHandler installed above. */
    private fun login(): Boolean {
        if (password.isEmpty()) { loginError = "no gateway admin password set (Settings)"; return false }
        val url = URL(baseUrl.trimEnd('/') + "/cgi-bin/Login")
        val body = JSONObject().put("UserName", username).put("Password", sha256Hex("$username:$password")).put("token", "").toString()
        val conn = ((wifi()?.openConnection(url) ?: url.openConnection()) as HttpURLConnection).apply {
            connectTimeout = 3000; readTimeout = 3000; requestMethod = "POST"; doOutput = true; useCaches = false
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("X-Requested-With", "XMLHttpRequest")
            setRequestProperty("User-Agent", "pingaroo-mobile/" + BuildConfig.VERSION_NAME)
        }
        try {
            conn.outputStream.use { it.write(body.toByteArray()) }
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)?.bufferedReader()?.readText() ?: ""
            val j = try { JSONObject(text) } catch (_: Exception) { JSONObject() }
            val ok = code < 400 && (j.optBoolean("Success", false) || j.optInt("Login", 0) == 1)
            loggedIn = ok
            loginError = when {
                ok -> ""
                j.optInt("Login", 0) == 2 -> "another device is logged into the gateway admin page; log out there first"
                j.optInt("LoginAttempt", 0) >= 5 -> "gateway locked logins after 5 failed attempts; wait a few minutes"
                else -> "gateway rejected the login (HTTP $code ${text.take(60)})"
            }
            return ok
        } finally { conn.disconnect() }
    }

    private fun needsSession(j: JSONObject): Boolean = j.optBoolean("session_id_error", false)

    /** GET that logs in (once) when the gateway asks for a session. */
    private fun getWithSession(path: String, timeoutMs: Int = 2500): JSONObject {
        var j = get(path, timeoutMs)
        if (needsSession(j)) {
            if (login()) j = get(path, timeoutMs)
            if (needsSession(j)) throw IllegalStateException(if (password.isEmpty()) "gateway needs a login: set the admin password in Settings" else "login failed: $loginError")
        }
        return j
    }

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
                var body = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.readText() ?: ""
                c.disconnect()
                if (body.contains("session_id_error")) {
                    body += "\n-> session required; login " + (if (login()) "OK, retrying:\n" + getWithSession(p).toString() else "FAILED: $loginError")
                }
                sb.append("HTTP $code\n").append(body.take(1400)).append("\n\n")
            } catch (e: Exception) { sb.append("FAIL ${e.javaClass.simpleName}: ${e.message}\n\n") }
        }
        sb.toString()
    }

    private fun JSONObject.intOrNull(key: String): Int? =
        optString(key, "").trim().removeSuffix("dBm").removeSuffix("dB").trim().let { it.toIntOrNull() ?: it.toDoubleOrNull()?.let { d -> Math.round(d).toInt() } }

    suspend fun fetch(label: String): Sample = withContext(Dispatchers.IO) {
        val t0 = System.currentTimeMillis()
        try {
            val endc = getWithSession("/cgi-bin/GetENDCInfo")
            val now = System.currentTimeMillis()
            if (slowCache == null || now - slowCacheAt > 30_000) {
                slowCacheAt = now                       // back off even if these fail
                val merged = JSONObject()
                for (p in listOf("/cgi-bin/GetLTEStatus", "/cgi-bin/GetLTEBasicSetting", "/cgi-bin/GetWanIp")) {
                    try { val j = getWithSession(p, 2000); for (k in j.keys()) merged.put(k, j.get(k)) } catch (_: Exception) { }
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
