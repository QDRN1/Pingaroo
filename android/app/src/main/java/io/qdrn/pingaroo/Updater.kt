package io.qdrn.pingaroo

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Self-update from the Pingaroo GitHub repo.
 *
 * Reads android/releases/version.json, compares versionCode with this build, downloads
 * the APK into the cache and hands it to the system installer.  Works unauthenticated
 * if the repo is public (raw.githubusercontent.com) and with a GitHub token if it is
 * private (api.github.com contents API with raw media type).
 */
class Updater(private val ctx: Context) {
    data class Info(val versionCode: Int, val versionName: String, val apkPath: String, val notes: String)

    private val prefs = ctx.getSharedPreferences("pingaroo", Context.MODE_PRIVATE)
    var token: String
        get() = prefs.getString("gh_token", "") ?: ""
        set(v) { prefs.edit().putString("gh_token", v.trim()).apply() }

    private val owner = "QDRN1"
    private val repo = "Pingaroo"
    private val branch = "main"
    private val manifestPath = "android/releases/version.json"

    class AuthRequired : Exception("GitHub token required (repo is private)")

    private fun open(path: String, raw: Boolean): HttpURLConnection {
        val url = if (token.isNotEmpty())
            URL("https://api.github.com/repos/$owner/$repo/contents/$path?ref=$branch")
        else
            URL("https://raw.githubusercontent.com/$owner/$repo/$branch/$path")
        val c = (url.openConnection() as HttpURLConnection).apply {
            connectTimeout = 10000
            readTimeout = 60000
            setRequestProperty("User-Agent", "pingaroo-mobile")
            if (token.isNotEmpty()) {
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Accept", if (raw) "application/vnd.github.raw+json" else "application/vnd.github.raw+json")
                setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
            }
        }
        val code = c.responseCode
        if (code == 401 || code == 403 || code == 404) { c.disconnect(); throw AuthRequired() }
        if (code >= 400) { c.disconnect(); throw IllegalStateException("HTTP $code fetching $path") }
        return c
    }

    suspend fun check(): Info = withContext(Dispatchers.IO) {
        val c = open(manifestPath, raw = true)
        val text = try { c.inputStream.bufferedReader().readText() } finally { c.disconnect() }
        val j = JSONObject(text)
        Info(j.getInt("versionCode"), j.getString("versionName"), j.getString("apk"), j.optString("notes", ""))
    }

    fun isNewer(info: Info): Boolean = info.versionCode > BuildConfig.VERSION_CODE

    suspend fun download(info: Info, onProgressRaw: (Int) -> Unit): File = withContext(Dispatchers.IO) {
        val main = Handler(Looper.getMainLooper())
        var lastPct = -1
        val onProgress: (Int) -> Unit = { pct -> if (pct != lastPct) { lastPct = pct; main.post { onProgressRaw(pct) } } }
        val dir = File(ctx.cacheDir, "updates").apply { mkdirs() }
        val out = File(dir, "pingaroo-mobile-${info.versionCode}.apk")
        val c = open(info.apkPath, raw = true)
        try {
            val total = c.contentLengthLong
            c.inputStream.use { inp ->
                out.outputStream().use { o ->
                    val buf = ByteArray(64 * 1024)
                    var done = 0L
                    while (true) {
                        val n = inp.read(buf)
                        if (n < 0) break
                        o.write(buf, 0, n)
                        done += n
                        if (total > 0) onProgress((done * 100 / total).toInt())
                    }
                }
            }
        } finally { c.disconnect() }
        if (out.length() < 100_000) throw IllegalStateException("download too small (${out.length()} bytes)")
        out
    }

    fun installIntent(apk: File): Intent {
        val uri: Uri = FileProvider.getUriForFile(ctx, "io.qdrn.pingaroo.mobile.files", apk)
        return Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
    }
}
