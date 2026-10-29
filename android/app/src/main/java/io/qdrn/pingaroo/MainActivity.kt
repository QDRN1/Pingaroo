package io.qdrn.pingaroo

import android.Manifest
import android.content.Intent
import android.content.res.Configuration
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var gateway: GatewayClient
    private lateinit var phone: PhoneRadio
    private lateinit var location: LocationHelper
    private lateinit var logger: CsvLogger

    private var source = "gateway"
    private var logging = false
    private var label = ""
    private var last: Sample? = null

    private lateinit var verdict: VerdictView
    private lateinit var spark: SparklineView
    private lateinit var status: TextView
    private lateinit var logInfo: TextView
    private lateinit var clock: TextView
    private lateinit var gatewayUrl: TextInputEditText
    private lateinit var tilesSignal: LinearLayout
    private lateinit var tilesCell: LinearLayout
    private lateinit var tilesLte: LinearLayout
    private lateinit var sectionLte: TextView

    // tiles by key so we can update in place
    private val tiles = HashMap<String, TileView>()
    private var columns = 3

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        location.start()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        verdict = findViewById(R.id.verdict)
        spark = findViewById(R.id.spark)
        status = findViewById(R.id.status)
        logInfo = findViewById(R.id.logInfo)
        clock = findViewById(R.id.clock)
        gatewayUrl = findViewById(R.id.gatewayUrl)
        tilesSignal = findViewById(R.id.tilesSignal)
        tilesCell = findViewById(R.id.tilesCell)
        tilesLte = findViewById(R.id.tilesLte)
        sectionLte = findViewById(R.id.sectionLte)

        phone = PhoneRadio(this)
        location = LocationHelper(this)
        logger = CsvLogger(this)
        gateway = GatewayClient(gatewayUrl.text?.toString()?.trim().orEmpty().ifEmpty { "http://192.168.1.1" })

        buildTiles()

        findViewById<MaterialButtonToggleGroup>(R.id.sourceGroup).addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (isChecked) {
                source = if (checkedId == R.id.btnPhone) "phone" else "gateway"
                spark.clear()
            }
        }
        findViewById<MaterialSwitch>(R.id.logSwitch).setOnCheckedChangeListener { _, on ->
            logging = on
            updateLogInfo()
        }
        findViewById<MaterialButton>(R.id.shareBtn).setOnClickListener {
            logger.shareIntent()?.let { startActivity(Intent.createChooser(it, "Share CSV")) }
                ?: run { status.text = "Nothing logged yet. Save a snapshot or turn on Log To CSV." }
        }
        findViewById<MaterialButton>(R.id.snapshotBtn).setOnClickListener { askSnapshot() }
        gatewayUrl.setOnEditorActionListener { v, _, _ ->
            gateway = GatewayClient(v.text.toString().trim().ifEmpty { "http://192.168.1.1" })
            false
        }

        permissions.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.READ_PHONE_STATE))

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                location.start()
                try {
                    while (isActive) {
                        val raw = if (source == "gateway") gateway.fetch(label) else phone.fetch(label)
                        val loc = location.last
                        val sample = raw.copy(lat = loc?.latitude, lon = loc?.longitude, accuracyM = loc?.accuracy)
                        last = sample
                        render(sample)
                        if (logging && sample.error.isEmpty()) {
                            logger.append(sample)
                            updateLogInfo()
                        }
                        delay(2000)
                    }
                } finally {
                    location.stop()
                }
            }
        }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        buildTiles()
        last?.let { render(it) }
    }

    // ------------------------------------------------------------------ tiles
    private fun tile(key: String): TileView = tiles.getOrPut(key) { TileView(this) }

    private fun grid(container: LinearLayout, keys: List<String>) {
        container.removeAllViews()
        val d = resources.displayMetrics.density
        var row: LinearLayout? = null
        for ((i, key) in keys.withIndex()) {
            if (i % columns == 0) {
                row = LinearLayout(this).apply {
                    orientation = LinearLayout.HORIZONTAL
                    layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = (6 * d).toInt() }
                }
                container.addView(row)
            }
            val t = tile(key)
            (t.parent as? ViewGroup)?.removeView(t)
            t.layoutParams = LinearLayout.LayoutParams(0, (76 * d).toInt(), 1f).apply { marginEnd = if ((i + 1) % columns == 0) 0 else (6 * d).toInt() }
            row!!.addView(t)
        }
        // pad the last row so tiles keep equal width
        val rem = keys.size % columns
        if (rem != 0) for (k in rem until columns) row!!.addView(View(this).apply { layoutParams = LinearLayout.LayoutParams(0, 1, 1f) })
    }

    private fun buildTiles() {
        columns = if (resources.configuration.screenWidthDp >= 600) 4 else 3
        grid(tilesSignal, listOf("rsrp", "sinr", "rsrq", "rssi"))
        grid(tilesCell, listOf("tech", "band", "pci", "cell", "tac", "arfcn", "bw", "operator", "apn", "wan"))
        grid(tilesLte, listOf("lband", "lpci", "lcell", "lrsrp", "lrsrq", "lsnr", "learfcn"))
    }

    private fun render(s: Sample) {
        clock.text = SimpleDateFormat("EEE MMM d  HH:mm:ss", Locale.US).format(Date())
        if (s.error.isNotEmpty()) {
            verdict.set(if (s.source == "gateway") "GATEWAY OFFLINE" else "NO RADIO DATA", Thresholds.RED, 0,
                s.error + if (s.source == "gateway") "  (phone on Kangaroo Labs?)" else "")
            status.text = ""
            return
        }
        val (v, col) = Thresholds.verdict(s.rsrp, s.sinr)
        val bars = when (v) { "EXCELLENT" -> 5; "GOOD" -> 4; "FAIR" -> 3; "POOR" -> 2; "INTERFERENCE" -> 1; else -> 0 }
        val where = if (label.isNotEmpty()) "at $label" else "no location label"
        verdict.set(v, col, bars, "${s.source.replaceFirstChar { it.uppercase() }} · ${s.tech.ifEmpty { "-" }} · $where")

        tile("rsrp").set("RSRP", s.rsrp?.toString() ?: "-", "dBm", Thresholds.rsrp(s.rsrp), Thresholds.frac(s.rsrp, -120, -60))
        tile("sinr").set("SINR", s.sinr?.toString() ?: "-", "dB", Thresholds.sinr(s.sinr), Thresholds.frac(s.sinr, -5, 30))
        tile("rsrq").set("RSRQ", s.rsrq?.toString() ?: "-", "dB", Thresholds.rsrq(s.rsrq), Thresholds.frac(s.rsrq, -20, -3))
        tile("rssi").set("RSSI", s.rssi?.toString() ?: "-", "dBm", Thresholds.rssi(s.rssi), Thresholds.frac(s.rssi, -100, -50))

        tile("tech").set("TECH", s.tech.ifEmpty { "-" }, "", Thresholds.CYAN, big = false)
        tile("band").set("BAND", s.band.ifEmpty { "-" }, "", Thresholds.CYAN, big = false)
        tile("pci").set("PCI", s.pci?.toString() ?: "-", "", Thresholds.BLUE, big = false)
        tile("cell").set("CELL ID", s.cellId?.toString() ?: "-", "", Thresholds.PURPLE, big = false)
        tile("tac").set("TAC", s.tac?.toString() ?: "-", "", Thresholds.BLUE, big = false)
        tile("arfcn").set("ARFCN", s.arfcn?.toString() ?: "-", "", Thresholds.MUTED, big = false)
        tile("bw").set("BANDWIDTH", s.bandwidth.ifEmpty { "-" }, "", Thresholds.MUTED, big = false)
        tile("operator").set("OPERATOR", s.operator.ifEmpty { "-" }, "", Thresholds.TEXT, big = false)
        tile("apn").set("APN", s.apn.ifEmpty { "-" }, "", Thresholds.AMBER, big = false)
        tile("wan").set("WAN IP", s.wanIp.ifEmpty { "-" }, "", Thresholds.MUTED, big = false)

        val hasLte = s.lteBand.isNotEmpty() || s.lteRsrp != null || s.ltePci != null
        sectionLte.visibility = if (hasLte) View.VISIBLE else View.GONE
        tilesLte.visibility = if (hasLte) View.VISIBLE else View.GONE
        if (hasLte) {
            tile("lband").set("LTE BAND", s.lteBand.ifEmpty { "-" }, "", Thresholds.CYAN, big = false)
            tile("lpci").set("LTE PCI", s.ltePci?.toString() ?: "-", "", Thresholds.BLUE, big = false)
            tile("lcell").set("LTE CELL", s.lteCellId?.toString() ?: "-", "", Thresholds.PURPLE, big = false)
            tile("lrsrp").set("LTE RSRP", s.lteRsrp?.toString() ?: "-", "dBm", Thresholds.rsrp(s.lteRsrp), big = false)
            tile("lrsrq").set("LTE RSRQ", s.lteRsrq?.toString() ?: "-", "dB", Thresholds.rsrq(s.lteRsrq), big = false)
            tile("lsnr").set("LTE SNR", s.lteRssnr?.toString() ?: "-", "dB", Thresholds.sinr(s.lteRssnr), big = false)
            tile("learfcn").set("EARFCN", s.lteEarfcn?.toString() ?: "-", "", Thresholds.MUTED, big = false)
        }

        spark.push(s.rsrp, s.sinr)
        val gps = if (s.lat != null) "GPS %.5f, %.5f (±%.0f m)".format(s.lat, s.lon, s.accuracyM ?: 0f) else "GPS: no fix yet"
        status.text = "$gps   ·   ${s.time.substring(11, 19)}"
    }

    // --------------------------------------------------------------- snapshot
    private fun askSnapshot() {
        val input = EditText(this).apply {
            hint = getString(R.string.snapshot_hint)
            setText(label)
            setSelection(text.length)
            val d = resources.displayMetrics.density
            setPadding((20 * d).toInt(), (16 * d).toInt(), (20 * d).toInt(), (16 * d).toInt())
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.snapshot_title)
            .setView(input)
            .setPositiveButton("Save") { _, _ ->
                label = input.text.toString().trim()
                val s = last
                if (s == null || s.error.isNotEmpty()) {
                    Toast.makeText(this, "No reading to save yet", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                val snap = s.copy(label = label, event = "snapshot")
                logger.append(snap)
                updateLogInfo()
                last?.let { render(it.copy(label = label)) }
                Toast.makeText(this, "Saved $label: RSRP ${snap.rsrp ?: "-"}  SINR ${snap.sinr ?: "-"}", Toast.LENGTH_LONG).show()
            }
            .setNegativeButton("Cancel", null)
            .show()
        input.requestFocus()
    }

    private fun updateLogInfo() {
        logInfo.text = (if (logging) "Logging every 2 s" else "Continuous log off") +
            "  ·  ${logger.count} rows in ${logger.path().substringAfterLast('/')}"
    }
}
