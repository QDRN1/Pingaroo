package io.qdrn.pingaroo

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider

class SplashActivity : AppCompatActivity() {
    private var launched = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val crash = PingarooApp.crashFile(application)
        if (crash.exists()) {
            val text = crash.readText()
            AlertDialog.Builder(this)
                .setTitle("Pingaroo Mobile crashed last time")
                .setMessage(text.take(3000))
                .setPositiveButton("Share") { _, _ ->
                    val uri = FileProvider.getUriForFile(this, "io.qdrn.pingaroo.mobile.files", crash)
                    startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
                        type = "text/plain"
                        putExtra(Intent.EXTRA_STREAM, uri)
                        putExtra(Intent.EXTRA_TEXT, text)
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    }, "Share crash report"))
                    crash.delete()
                    showSplash()
                }
                .setNegativeButton("Dismiss") { _, _ -> crash.delete(); showSplash() }
                .setCancelable(false)
                .show()
        } else {
            showSplash()
        }
    }

    private fun showSplash() {
        val view = SplashView(this)
        view.onDone = { launchMain() }
        view.setOnClickListener { view.finish() }
        setContentView(view)
    }

    private fun launchMain() {
        if (launched) return
        launched = true
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }
}
