package io.qdrn.pingaroo

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Self-update from the public Pingaroo GitHub repo: reads android/releases/version.json,
 * compares versionCode with this build, downloads the APK into the cache and hands it to
 * the system installer.
 */
class Updater(private val ctx: Context) {
    data class Info(val versionCode: Int, val versionName: String, val apkPath: String, val notes: String)

    private val base = "https://raw.githubusercontent.com/QDRN1/Pingaroo/main/"
    private val manifestPath = "android/releases/version.json"

    private fun open(path: String): HttpURLConnection {
        val c = (URL(base + path).openConnection() as HttpURLConnection).apply {
            connectTimeout = 10000
            readTimeout = 60000
            useCaches = false
            setRequestProperty("User-Agent", "pingaroo-mobile/" + BuildConfig.VERSION_NAME)
            setRequestProperty("Cache-Control", "no-cache")
        }
        val code = c.responseCode
        if (code == 404) { c.disconnect(); throw IllegalStateException("$path not found on GitHub") }
        if (code >= 400) { c.disconnect(); throw IllegalStateException("HTTP $code fetching $path") }
        return c
    }

    suspend fun check(): Info = withContext(Dispatchers.IO) {
        val c = open(manifestPath)
        val text = try { c.inputStream.bufferedReader().readText() } finally { c.disconnect() }
        val j = JSONObject(text)
        Info(j.getInt("versionCode"), j.getString("versionName"), j.getString("apk"), j.optString("notes", ""))
    }

    fun isNewer(info: Info): Boolean = info.versionCode > BuildConfig.VERSION_CODE

    /** onProgress gets 0..100 when the size is known, else -1 per megabyte; always on the main thread. */
    suspend fun download(info: Info, onProgressRaw: (Int) -> Unit): File = withContext(Dispatchers.IO) {
        val main = Handler(Looper.getMainLooper())
        var lastPct = -2
        val onProgress: (Int) -> Unit = { pct -> if (pct != lastPct || pct == -1) { lastPct = pct; main.post { onProgressRaw(pct) } } }
        val dir = File(ctx.cacheDir, "updates").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }
        val out = File(dir, "pingaroo-mobile-${info.versionCode}.apk")
        val c = open(info.apkPath)
        try {
            val total = c.contentLengthLong
            c.inputStream.use { inp ->
                out.outputStream().use { o ->
                    val buf = ByteArray(64 * 1024)
                    var done = 0L
                    var lastMb = -1L
                    while (true) {
                        val n = inp.read(buf)
                        if (n < 0) break
                        o.write(buf, 0, n)
                        done += n
                        if (total > 0) onProgress((done * 100 / total).toInt())
                        else { val mb = done / 1_000_000; if (mb != lastMb) { lastMb = mb; onProgress(-1) } }
                    }
                }
            }
        } finally { c.disconnect() }
        if (out.length() < 100_000) throw IllegalStateException("download too small (${out.length()} bytes)")
        out
    }

    /** Opens the APK in the browser; the browser downloads it and acts as the installer,
     *  so this app needs no install permission and Play Protect has nothing to flag. */
    fun browserIntent(info: Info): Intent =
        Intent(Intent.ACTION_VIEW, Uri.parse(base + info.apkPath)).apply { addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) }
}
