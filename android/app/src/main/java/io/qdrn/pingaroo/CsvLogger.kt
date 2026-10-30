package io.qdrn.pingaroo

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Appends samples to a CSV in the app's external files dir and shares it. */
class CsvLogger(private val ctx: Context) {
    private var file: File? = null
    var count = 0
        private set

    companion object {
        /** Set by the main screen so other screens can share the current file. */
        var lastShareIntent: Intent? = null
    }

    private fun ensureFile(): File {
        file?.let { return it }
        val dir = ctx.getExternalFilesDir(null) ?: ctx.filesDir
        val name = "pingaroo-mobile-" + SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date()) + ".csv"
        val f = File(dir, name)
        if (!f.exists()) f.writeText(Sample.CSV_COLUMNS.joinToString(",") + "\n")
        file = f
        return f
    }

    private fun esc(v: Any?): String {
        val s = v?.toString() ?: ""
        return if (s.any { it == ',' || it == '"' || it == '\n' }) "\"" + s.replace("\"", "\"\"") + "\"" else s
    }

    fun append(sample: Sample) {
        ensureFile().appendText(sample.csvRow().joinToString(",") { esc(it) } + "\n")
        count++
    }

    fun path(): String = file?.absolutePath ?: "(no log yet)"

    fun newFile() { file = null; count = 0 }

    fun shareIntent(): Intent? {
        val f = file ?: return null
        val uri = FileProvider.getUriForFile(ctx, "io.qdrn.pingaroo.mobile.files", f)
        return Intent(Intent.ACTION_SEND).apply {
            type = "text/csv"
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_SUBJECT, f.name)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
    }
}
