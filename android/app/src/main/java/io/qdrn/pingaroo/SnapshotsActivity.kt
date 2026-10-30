package io.qdrn.pingaroo

import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton

/** Saved spots this session, ranked by SINR, with the best highlighted. */
class SnapshotsActivity : AppCompatActivity() {
    private lateinit var list: LinearLayout
    private val d by lazy { resources.displayMetrics.density }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(0xFF0A0D14.toInt()) }
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(8))
            addView(MaterialButton(this@SnapshotsActivity).apply { text = "Back"; setOnClickListener { finish() } })
            addView(TextView(this@SnapshotsActivity).apply {
                text = "Snapshots"; textSize = 20f; setTextColor(Thresholds.TEXT); setPadding(dp(14), 0, 0, 0)
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            })
            addView(MaterialButton(this@SnapshotsActivity).apply {
                text = "Share CSV"
                setOnClickListener { CsvLogger.lastShareIntent?.let { startActivity(Intent.createChooser(it, "Share CSV")) } }
            })
        }
        root.addView(header)
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), 0, dp(14), dp(14)) }
        root.addView(ScrollView(this).apply { addView(list) })
        setContentView(root)
        SnapshotStore.init(this)
        render()
    }

    private fun dp(v: Int) = (v * d).toInt()

    private fun render() {
        list.removeAllViews()
        val items = SnapshotStore.items.sortedByDescending { it.sinr ?: it.lteRssnr ?: -99 }
        val best = SnapshotStore.best()
        if (items.isEmpty()) {
            list.addView(TextView(this).apply { text = "No snapshots yet. On the main screen tap Save Snapshot at each spot."; setTextColor(Thresholds.MUTED); setPadding(0, dp(20), 0, 0) })
            return
        }
        list.addView(TextView(this).apply { text = "${items.size} spots · ranked by SINR · best marked ★"; setTextColor(Thresholds.MUTED); textSize = 13f; setPadding(0, dp(6), 0, dp(6)) })
        for (s in items) {
            val sinr = s.sinr ?: s.lteRssnr; val rsrp = s.rsrp ?: s.lteRsrp
            val (v, col) = Thresholds.verdict(rsrp, sinr)
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
                setBackgroundColor(0xFF1A212E.toInt()); setPadding(dp(12), dp(10), dp(12), dp(10))
                layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(6) }
            }
            row.addView(View(this).apply { setBackgroundColor(col); layoutParams = LinearLayout.LayoutParams(dp(5), dp(44)) })
            val text = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(10), 0, 0, 0); layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f) }
            text.addView(TextView(this).apply {
                this.text = (if (s === best) "★ " else "") + s.label.ifEmpty { "(no label)" } + "   $v"; textSize = 16f; setTextColor(Thresholds.TEXT)
            })
            text.addView(TextView(this).apply {
                this.text = "RSRP ${rsrp ?: "-"}  SINR ${sinr ?: "-"}  RSRQ ${s.rsrq ?: s.lteRsrq ?: "-"}  ${s.band.ifEmpty { s.lteBand }}  PCI ${s.pci ?: s.ltePci ?: "-"}  ·  ${s.time.substring(11, 19)}  ·  ${s.source}"
                textSize = 12f; setTextColor(Thresholds.MUTED)
            })
            row.addView(text)
            row.addView(MaterialButton(this).apply {
                this.text = "✕"; minWidth = 0; setPadding(dp(10), 0, dp(10), 0)
                setOnClickListener {
                    AlertDialog.Builder(this@SnapshotsActivity).setMessage("Remove snapshot '${s.label}' from this list? (The CSV row stays.)")
                        .setPositiveButton("Remove") { _, _ -> SnapshotStore.remove(s); render() }.setNegativeButton("Cancel", null).show()
                }
            })
            row.setOnClickListener {
                AlertDialog.Builder(this).setTitle(s.label.ifEmpty { "Snapshot" })
                    .setMessage(detail(s)).setPositiveButton("OK", null).show()
            }
            list.addView(row)
        }
    }

    private fun detail(s: Sample): String = listOf(
        "Time ${s.time}", "Source ${s.source}", "Tech ${s.tech}  Operator ${s.operator}",
        "Band ${s.band}  ARFCN ${s.arfcn ?: "-"}  PCI ${s.pci ?: "-"}  TAC ${s.tac ?: "-"}  Cell ${s.cellId ?: "-"}",
        "RSRP ${s.rsrp ?: "-"} dBm  RSRQ ${s.rsrq ?: "-"} dB  SINR ${s.sinr ?: "-"} dB  RSSI ${s.rssi ?: "-"} dBm",
        "LTE ${s.lteBand}  PCI ${s.ltePci ?: "-"}  RSRP ${s.lteRsrp ?: "-"}  RSRQ ${s.lteRsrq ?: "-"}  SNR ${s.lteRssnr ?: "-"}",
        if (s.apn.isNotEmpty()) "APN ${s.apn}  WAN ${s.wanIp}" else "",
        if (s.lat != null) "GPS %.5f, %.5f (±%.0f m)".format(s.lat, s.lon, s.accuracyM ?: 0f) else "GPS: none",
    ).filter { it.isNotEmpty() }.joinToString("\n")
}
