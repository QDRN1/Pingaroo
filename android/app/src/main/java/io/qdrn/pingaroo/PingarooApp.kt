package io.qdrn.pingaroo

import android.app.Application
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Writes any uncaught exception to crash.txt so the next launch can show and share it. */
class PingarooApp : Application() {
    override fun onCreate() {
        super.onCreate()
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, e ->
            try {
                val sw = StringWriter()
                e.printStackTrace(PrintWriter(sw))
                val stamp = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())
                crashFile(this).writeText("Pingaroo Mobile crash $stamp on thread ${thread.name}\n" +
                    "Device ${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL} Android ${android.os.Build.VERSION.RELEASE}\n\n" + sw)
            } catch (_: Exception) { }
            previous?.uncaughtException(thread, e)
        }
    }

    companion object {
        fun crashFile(app: Application): File = File(app.getExternalFilesDir(null) ?: app.filesDir, "crash.txt")
    }
}
