package io.qdrn.pingaroo

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.location.LocationManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.HapticFeedbackConstants
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.materialswitch.MaterialSwitch
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var gateway: GatewayClient
    private lateinit var phone: PhoneRadio
    private lateinit var location: LocationHelper
    private lateinit var wifi: WifiWatcher
    private lateinit var logger: CsvLogger
    private lateinit var updater: Updater
    private var tone: ToneGenerator? = null

    private var source = "gateway"
    private var label = ""
    private var paused = false
    private var last: Sample? = null
    private var lastAt = 0L
    private var pendingUpdate: Updater.Info? = null
    private var gatewayMisses = 0
    private val stats = SessionStats()
    private var loopScope: CoroutineScope? = null
    private var loopJob: Job? = null

    private lateinit var verdict: VerdictView
    private lateinit var spark: SparklineView
    private lateinit var status: TextView
    private lateinit var footer: TextView
    private lateinit var clock: TextView
    private lateinit var contextLine: TextView
    private lateinit var freshness: TextView
    private lateinit var updateBanner: TextView
    private lateinit var tilesSignal: LinearLayout
    private lateinit var tilesStats: LinearLayout
    private lateinit var tilesCell: LinearLayout
    private lateinit var tilesLte: LinearLayout
    private lateinit var sectionLte: TextView
    private lateinit var pauseBtn: MaterialButton
    private lateinit var spotsBtn: MaterialButton
    private lateinit var logSwitch: MaterialSwitch

    private val tiles = HashMap<String, TileView>()
    private var columns = 3

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        location.start()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)
        source = prefs.source
        label = prefs.label
        applyKeepScreenOn()

        verdict = findViewById(R.id.verdict)
        spark = findViewById(R.id.spark)
        status = findViewById(R.id.status)
        footer = findViewById(R.id.footer)
        clock = findViewById(R.id.clock)
        contextLine = findViewById(R.id.contextLine)
        freshness = findViewById(R.id.freshness)
        updateBanner = findViewById(R.id.updateBanner)
        tilesSignal = findViewById(R.id.tilesSignal)
        tilesStats = findViewById(R.id.tilesStats)
        tilesCell = findViewById(R.id.tilesCell)
        tilesLte = findViewById(R.id.tilesLte)
        sectionLte = findViewById(R.id.sectionLte)
        pauseBtn = findViewById(R.id.pauseBtn)
        spotsBtn = findViewById(R.id.spotsBtn)
        logSwitch = findViewById(R.id.logSwitch)

        phone = PhoneRadio(this)
        location = LocationHelper(this)
        wifi = WifiWatcher(this)
        logger = CsvLogger(this)
        updater = Updater(this)
        gateway = GatewayClient(prefs.gatewayUrl) { wifi.network }

        footer.text = "Pingaroo Mobile v${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})  ·  Designed by Collin · QDRN"
        footer.setOnClickListener { showAbout() }

        val group = findViewById<MaterialButtonToggleGroup>(R.id.sourceGroup)
        group.check(if (source == "phone") R.id.btnPhone else R.id.btnGateway)
        group.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (isChecked) {
                source = if (checkedId == R.id.btnPhone) "phone" else "gateway"
                prefs.source = source
                spark.clear()
                gatewayMisses = 0
                last = null; lastAt = 0L
                buildTiles()
                verdict.set("SWITCHING", Thresholds.AMBER, 0, "Reading from the ${if (source == "gateway") "gateway" else "phone radio"}…")
                restartLoop()
            }
        }
        logSwitch.isChecked = prefs.logging
        logSwitch.setOnCheckedChangeListener { _, on -> prefs.logging = on; updateSpots() }
        findViewById<MaterialButton>(R.id.snapshotBtn).setOnClickListener { askSnapshot(it) }
        spotsBtn.setOnClickListener { startActivity(Intent(this, SnapshotsActivity::class.java)) }
        pauseBtn.setOnClickListener { togglePause() }
        findViewById<MaterialButton>(R.id.menuBtn).setOnClickListener { showMenu(it) }
        updateBanner.setOnClickListener { pendingUpdate?.let { installUpdate(it) } }
        verdict.setOnClickListener { last?.let { if (it.error.isNotEmpty()) openFixFor(it) } }

        buildTiles()
        updateSpots()
        permissions.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.READ_PHONE_STATE))
        if (prefs.autoUpdate) checkForUpdate(manual = false)

        // sampling loop: lifecycle-bound scope, restartable job (source switch cancels in-flight work)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                location.start(); wifi.start(); phone.start()
                loopScope = this
                restartLoop()
                try { awaitCancellation() } finally {
                    loopJob?.cancel(); loopJob = null; loopScope = null
                    location.stop(); wifi.stop(); phone.stop()
                }
            }
        }
        // 1 Hz clock / freshness ticker
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (isActive) { tick(); delay(1000) }
            }
        }
    }

    private fun restartLoop() {
        loopJob?.cancel()
        val scope = loopScope ?: return
        loopJob = scope.launch {
            while (isActive) {
                if (!paused) {
                    val src = source
                    val raw = if (src == "gateway") gateway.fetch(label) else phone.fetch(label)
                    if (!isActive || src != source) break      // switched while fetching: a new loop owns the screen
                    val loc = location.last
                    val sample = raw.copy(lat = loc?.latitude, lon = loc?.longitude, accuracyM = loc?.accuracy)
                    if (sample.source == "gateway" && sample.error.isNotEmpty()) {
                        gatewayMisses++
                        if (gatewayMisses < 3 && last != null && last!!.error.isEmpty()) { tick(); delay(prefs.pollMs); continue }
                    } else gatewayMisses = 0
                    last = sample; lastAt = System.currentTimeMillis()
                    render(sample)
                    if (prefs.logging && sample.error.isEmpty()) { logger.append(sample); updateSpots() }
                    if (prefs.tone && sample.error.isEmpty()) beepFor(sample)
                }
                delay(prefs.pollMs)
            }
        }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        buildTiles()
        last?.let { render(it) }
    }

    override fun onDestroy() {
        super.onDestroy()
        tone?.release(); tone = null
    }

    private fun applyKeepScreenOn() {
        if (prefs.keepScreenOn) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    // ------------------------------------------------------------- ticker
    private fun tick() {
        clock.text = SimpleDateFormat("EEE MMM d  HH:mm:ss", Locale.US).format(Date())
        if (paused) { freshness.text = "PAUSED"; freshness.setTextColor(Thresholds.AMBER); return }
        if (lastAt == 0L) { freshness.text = "waiting for first reading"; freshness.setTextColor(Thresholds.MUTED); return }
        val age = (System.currentTimeMillis() - lastAt) / 1000
        freshness.text = if (age < 1) "updated just now" else "updated ${age}s ago"
        freshness.setTextColor(when { age > 30 -> Thresholds.RED; age > 10 -> Thresholds.AMBER; else -> Thresholds.MUTED })
        // context line
        contextLine.text = if (source == "gateway") {
            val ss = wifi.ssid
            val l = last
            val gw = prefs.gatewayUrl.removePrefix("http://")
            val retry = if (gatewayMisses in 1..2) " · retrying ($gatewayMisses)" else ""
            when {
                !wifi.onWifi -> "Phone is not on Wi-Fi: join the gateway's network"
                l != null && l.error.isEmpty() -> "Wi-Fi ${ss.ifEmpty { "?" }} · $gw · ${l.fetchMs ?: "-"} ms$retry" + if (!wifi.validated) " · Wi-Fi has no internet (bound anyway)" else ""
                else -> "Wi-Fi ${ss.ifEmpty { "?" }} · $gw not answering$retry"
            }
        } else {
            val l = last
            "Phone radio · ${l?.operator?.ifEmpty { "-" } ?: "-"} · ${l?.tech?.ifEmpty { "-" } ?: "-"} · poll ${prefs.pollMs / 1000}s"
        }
    }

    private fun togglePause() {
        paused = !paused
        pauseBtn.text = if (paused) "Resume" else "Pause"
        tick()
        if (!paused) restartLoop()
    }

    // ------------------------------------------------------------------ tiles
    private fun tile(key: String): TileView = tiles.getOrPut(key) { TileView(this) }

    private fun grid(container: LinearLayout, keys: List<String>, cols: Int = columns, height: Int = 76) {
        container.removeAllViews()
        val d = resources.displayMetrics.density
        var row: LinearLayout? = null
        for ((i, key) in keys.withIndex()) {
            if (i % cols == 0) {
                row = LinearLayout(this).apply {
                    orientation = LinearLayout.HORIZONTAL
                    layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = (6 * d).toInt() }
                }
                container.addView(row)
            }
            val t = tile(key)
            (t.parent as? ViewGroup)?.removeView(t)
            val lastInRow = (i + 1) % cols == 0 || i == keys.size - 1
            t.layoutParams = LinearLayout.LayoutParams(0, (height * d).toInt(), 1f).apply { marginEnd = if (lastInRow) 0 else (6 * d).toInt() }
            row!!.addView(t)
        }
    }

    private fun buildTiles() {
        val wide = resources.configuration.screenWidthDp >= 600
        columns = if (wide) 4 else 3
        grid(tilesSignal, listOf("rsrp", "sinr", "rsrq", "rssi"), cols = if (wide) 4 else 2, height = 88)
        grid(tilesStats, listOf("session", "srsrp", "ssinr", "best"), cols = if (wide) 4 else 2, height = 64)
        if (source == "gateway")
            grid(tilesCell, listOf("tech", "band", "pci", "cell", "tac", "arfcn", "bw", "apn", "wan"))
        else
            grid(tilesCell, listOf("tech", "band", "pci", "cell", "tac", "arfcn", "bw", "level", "operator"))
        grid(tilesLte, listOf("lband", "lpci", "lcell", "lrsrp", "lrsrq", "lsnr", "learfcn"), height = 64)
        renderStats()
    }

    private fun arrow(cur: Int?, prev: Int?): String = when {
        cur == null || prev == null -> ""
        cur > prev -> " ▲"
        cur < prev -> " ▼"
        else -> " ▬"
    }

    private fun render(s: Sample) {
        if (s.error.isNotEmpty()) {
            val (title, hint) = errorPresentation(s)
            verdict.set(title, Thresholds.RED, 0, hint)
            status.text = ""
            return
        }
        val useLte = s.rsrp == null && s.lteRsrp != null
        val rsrp = if (useLte) s.lteRsrp else s.rsrp
        val sinr = if (useLte) s.lteRssnr else s.sinr
        val rsrq = if (useLte) s.lteRsrq else s.rsrq
        val tag = if (useLte) " (LTE)" else if (s.source == "phone" && s.rsrp != null) " (NR)" else ""
        val prevR = stats.prevRsrp; val prevS = stats.prevSinr
        stats.add(rsrp, sinr, label)
        val (v, col) = Thresholds.verdict(rsrp, sinr)
        val bars = when (v) { "EXCELLENT" -> 5; "GOOD" -> 4; "FAIR" -> 3; "POOR" -> 2; "INTERFERENCE" -> 1; else -> 0 }
        val where = if (label.isNotEmpty()) "at $label" else "no location label"
        verdict.set(v, col, bars, "${s.source.replaceFirstChar { it.uppercase() }} · ${s.tech.ifEmpty { "-" }} · $where")

        tile("rsrp").set("RSRP$tag", rsrp?.toString() ?: "-", "dBm" + arrow(rsrp, prevR), Thresholds.rsrp(rsrp), Thresholds.frac(rsrp, -120, -60))
        tile("sinr").set("SINR$tag", sinr?.toString() ?: "-", "dB" + arrow(sinr, prevS), Thresholds.sinr(sinr), Thresholds.frac(sinr, -5, 30))
        tile("rsrq").set("RSRQ$tag", rsrq?.toString() ?: "-", "dB", Thresholds.rsrq(rsrq), Thresholds.frac(rsrq, -20, -3))
        tile("rssi").set("RSSI", s.rssi?.toString() ?: "-", "dBm", Thresholds.rssi(s.rssi), Thresholds.frac(s.rssi, -100, -50))
        renderStats()

        // NR identity is often withheld in NSA (Samsung); the LTE anchor is the registered cell then.
        val nrIdMissing = s.pci == null && s.cellId == null
        val anchor = s.source == "phone" && nrIdMissing && (s.ltePci != null || s.lteCellId != null)
        val idTag = if (anchor) " (LTE ANCHOR)" else ""
        tile("tech").set("TECH", s.tech.ifEmpty { "-" }, "", Thresholds.CYAN, big = false)
        tile("band").set("BAND$idTag", s.band.ifEmpty { if (useLte || anchor) s.lteBand.ifEmpty { "-" } else "-" }, "", Thresholds.CYAN, big = false)
        tile("pci").set("PCI$idTag", (s.pci ?: if (useLte || anchor) s.ltePci else null)?.toString() ?: "-", "", Thresholds.BLUE, big = false)
        tile("cell").set("CELL ID$idTag", (s.cellId ?: if (useLte || anchor) s.lteCellId else null)?.toString() ?: "-", "", Thresholds.PURPLE, big = false)
        tile("tac").set("TAC$idTag", (s.tac ?: if (useLte || anchor) s.lteTac else null)?.toString() ?: "-", "", Thresholds.BLUE, big = false)
        tile("arfcn").set("ARFCN$idTag", (s.arfcn ?: if (useLte || anchor) s.lteEarfcn else null)?.toString() ?: "-", "", Thresholds.MUTED, big = false)
        tile("bw").set("BANDWIDTH", s.bandwidth.ifEmpty { "-" }, "", Thresholds.MUTED, big = false)
        tile("operator").set("OPERATOR", s.operator.ifEmpty { "-" }, "", Thresholds.TEXT, big = false)
        tile("apn").set("APN", s.apn.ifEmpty { "-" }, "", Thresholds.AMBER, big = false)
        tile("wan").set("WAN IP", s.wanIp.ifEmpty { "-" }, "", Thresholds.MUTED, big = false)
        tile("level").set("ANDROID LEVEL", s.level?.let { "$it / 4" } ?: "-", "",
            when (s.level) { 4 -> Thresholds.GREEN; 3 -> Thresholds.LIME; 2 -> Thresholds.AMBER; null -> Thresholds.MUTED; else -> Thresholds.RED }, big = false)

        val hasLte = (s.lteBand.isNotEmpty() || s.lteRsrp != null || s.ltePci != null) && !useLte
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
        spark.push(rsrp, sinr)
        val gps = if (s.lat != null) "GPS %.5f, %.5f (±%.0f m)".format(s.lat, s.lon, s.accuracyM ?: 0f) else "GPS: no fix yet"
        status.text = "$gps   ·   sample ${s.time.substring(11, 19)}"
    }

    private fun renderStats() {
        val e = stats.elapsedS
        tile("session").set("SESSION", "${e / 60}:${"%02d".format(e % 60)}", "${stats.samples} samples", Thresholds.TEXT, big = false)
        tile("srsrp").set("RSRP MIN / AVG / MAX", "${stats.rsrp.lo ?: "-"} / ${stats.rsrp.avg ?: "-"} / ${stats.rsrp.hi ?: "-"}", "", Thresholds.rsrp(stats.rsrp.avg), big = false)
        tile("ssinr").set("SINR MIN / AVG / MAX", "${stats.sinr.lo ?: "-"} / ${stats.sinr.avg ?: "-"} / ${stats.sinr.hi ?: "-"}", "", Thresholds.sinr(stats.sinr.avg), big = false)
        val best = SnapshotStore.best()
        if (best != null) {
            val bs = best.sinr ?: best.lteRssnr
            tile("best").set("BEST SPOT", best.label.ifEmpty { "(no label)" }, "SINR ${bs ?: "-"}", Thresholds.sinr(bs), big = false)
        } else tile("best").set("BEST SPOT", "-", "save snapshots", Thresholds.MUTED, big = false)
    }

    // ------------------------------------------------------------- errors
    private fun errorPresentation(s: Sample): Pair<String, String> {
        if (s.source == "gateway") {
            return "GATEWAY OFFLINE" to if (!wifi.onWifi) "Phone is not on Wi-Fi. Join Kangaroo Labs." else
                "No answer from ${prefs.gatewayUrl.removePrefix("http://")} on Wi-Fi '${wifi.ssid.ifEmpty { "?" }}'. ${s.error}"
        }
        val locOk = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        val lm = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        return when {
            !locOk -> "PERMISSION NEEDED" to "Android needs Location permission to expose cell data. Tap here to grant it."
            !lm.isLocationEnabled -> "LOCATION OFF" to "Turn on Location services; Android hides cell data without it. Tap here."
            else -> "NO RADIO DATA" to s.error
        }
    }

    private fun openFixFor(s: Sample) {
        if (s.source == "gateway") return
        val locOk = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        val lm = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        when {
            !locOk -> startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
            !lm.isLocationEnabled -> startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
        }
    }

    // ---------------------------------------------------------------- tone
    private fun beepFor(s: Sample) {
        val sinr = s.sinr ?: s.lteRssnr
        val rsrp = s.rsrp ?: s.lteRsrp
        val (v, _) = Thresholds.verdict(rsrp, sinr)
        val t = tone ?: ToneGenerator(AudioManager.STREAM_MUSIC, 60).also { tone = it }
        val type = when (v) {
            "EXCELLENT" -> ToneGenerator.TONE_DTMF_9
            "GOOD" -> ToneGenerator.TONE_DTMF_6
            "FAIR" -> ToneGenerator.TONE_DTMF_3
            "POOR" -> ToneGenerator.TONE_DTMF_1
            else -> ToneGenerator.TONE_SUP_ERROR
        }
        try { t.startTone(type, 90) } catch (_: Exception) { }
    }

    // --------------------------------------------------------------- snapshot
    private fun askSnapshot(anchor: View) {
        val input = EditText(this).apply {
            hint = getString(R.string.snapshot_hint)
            setText(label); setSelection(text.length)
            val d = resources.displayMetrics.density
            setPadding((20 * d).toInt(), (16 * d).toInt(), (20 * d).toInt(), (16 * d).toInt())
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.snapshot_title)
            .setView(input)
            .setPositiveButton("Save") { _, _ ->
                label = input.text.toString().trim(); prefs.label = label
                val s = last
                if (s == null || s.error.isNotEmpty()) { Toast.makeText(this, "No reading to save yet", Toast.LENGTH_SHORT).show(); return@setPositiveButton }
                val snap = s.copy(label = label, event = "snapshot")
                logger.append(snap); SnapshotStore.add(snap)
                anchor.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
                updateSpots(); renderStats()
                last?.let { render(it.copy(label = label)) }
                Toast.makeText(this, "Saved $label: RSRP ${snap.rsrp ?: snap.lteRsrp ?: "-"}  SINR ${snap.sinr ?: snap.lteRssnr ?: "-"}", Toast.LENGTH_LONG).show()
            }
            .setNegativeButton("Cancel", null)
            .show()
        input.requestFocus()
    }

    private fun updateSpots() {
        spotsBtn.text = "Spots (${SnapshotStore.items.size})"
        CsvLogger.lastShareIntent = logger.shareIntent()
    }

    // ------------------------------------------------------------------ menu
    private fun showMenu(anchor: View) {
        val m = PopupMenu(this, anchor)
        m.menu.add(0, 1, 0, "Snapshots")
        m.menu.add(0, 2, 1, "Share CSV")
        m.menu.add(0, 3, 2, "Settings")
        m.menu.add(0, 4, 3, "New session (clear stats)")
        m.menu.add(0, 5, 4, "Check for updates")
        m.menu.add(0, 6, 5, "About")
        m.menu.add(0, 7, 6, "Radio debug dump")
        m.setOnMenuItemClickListener {
            when (it.itemId) {
                1 -> startActivity(Intent(this, SnapshotsActivity::class.java))
                2 -> logger.shareIntent()?.let { i -> startActivity(Intent.createChooser(i, "Share CSV")) }
                    ?: Toast.makeText(this, "Nothing logged yet", Toast.LENGTH_SHORT).show()
                3 -> showSettings()
                4 -> confirmNewSession()
                5 -> checkForUpdate(manual = true)
                6 -> showAbout()
                7 -> showRadioDebug()
            }
            true
        }
        m.show()
    }

    private fun confirmNewSession() {
        AlertDialog.Builder(this).setTitle("New session?")
            .setMessage("Clears the session stats, best spot and the snapshot list, and starts a new CSV file. Existing CSV files stay on the phone.")
            .setPositiveButton("Clear") { _, _ -> stats.reset(); SnapshotStore.clear(); spark.clear(); logger.newFile(); updateSpots(); renderStats() }
            .setNegativeButton("Cancel", null).show()
    }

    private fun showRadioDebug() {
        lifecycleScope.launch {
            val text = try { phone.debugDump() } catch (e: Exception) { "dump failed: $e" }
            AlertDialog.Builder(this@MainActivity).setTitle("Radio debug")
                .setMessage(text.take(6000))
                .setPositiveButton("Share") { _, _ ->
                    startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply { type = "text/plain"; putExtra(Intent.EXTRA_TEXT, text) }, "Share radio dump"))
                }
                .setNegativeButton("Close", null).show()
        }
    }

    private fun showAbout() {
        val pi = packageManager.getPackageInfo(packageName, 0)
        val built = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date(pi.lastUpdateTime))
        AlertDialog.Builder(this).setTitle("Pingaroo Mobile")
            .setMessage("Version ${BuildConfig.VERSION_NAME} (build ${BuildConfig.VERSION_CODE})\nInstalled $built\n" +
                "Device ${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}, Android ${android.os.Build.VERSION.RELEASE}\n\n" +
                "Designed by Collin · QDRN\nSource: github.com/QDRN1/Pingaroo\n\n" +
                "Gateway source reads the AT&T Internet Air (NCM1120) over its Wi-Fi, bound to the Wi-Fi network even when Android routes other traffic over mobile data. Phone source reads this phone's radio; it is a proxy, not the gateway.\n\nSampling runs only while the app is on screen.")
            .setPositiveButton("OK", null).show()
    }

    private fun showSettings() {
        val d = resources.displayMetrics.density
        fun pad(v: View) = v.apply { setPadding((20 * d).toInt(), (8 * d).toInt(), (20 * d).toInt(), (8 * d).toInt()) }
        val url = EditText(this).apply { hint = "Gateway URL"; setText(prefs.gatewayUrl) }
        val poll = RadioGroup(this).apply { orientation = RadioGroup.HORIZONTAL }
        val options = listOf(1000L to "1 s", 2000L to "2 s", 5000L to "5 s")
        for ((ms, name) in options) poll.addView(RadioButton(this).apply { text = name; tag = ms; isChecked = prefs.pollMs == ms })
        val toneSw = Switch(this).apply { text = "Audio tone follows signal quality"; isChecked = prefs.tone }
        val keepSw = Switch(this).apply { text = "Keep screen on"; isChecked = prefs.keepScreenOn }
        val autoSw = Switch(this).apply { text = "Check for updates on launch"; isChecked = prefs.autoUpdate }
        val token = EditText(this).apply { hint = "GitHub token (only if the repo is private)"; setText(updater.token) }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(pad(TextView(this@MainActivity).apply { text = "Gateway"; setTextColor(Thresholds.AMBER) }))
            addView(pad(url))
            addView(pad(TextView(this@MainActivity).apply { text = "Poll interval"; setTextColor(Thresholds.AMBER) }))
            addView(pad(poll))
            addView(pad(toneSw)); addView(pad(keepSw)); addView(pad(autoSw))
            addView(pad(TextView(this@MainActivity).apply { text = "Updates"; setTextColor(Thresholds.AMBER) }))
            addView(pad(token))
        }
        AlertDialog.Builder(this).setTitle("Settings").setView(ScrollView(this).apply { addView(box) })
            .setPositiveButton("Save") { _, _ ->
                prefs.gatewayUrl = url.text.toString(); gateway = GatewayClient(prefs.gatewayUrl) { wifi.network }
                for (i in 0 until poll.childCount) { val rb = poll.getChildAt(i) as RadioButton; if (rb.isChecked) prefs.pollMs = rb.tag as Long }
                prefs.tone = toneSw.isChecked; prefs.keepScreenOn = keepSw.isChecked; prefs.autoUpdate = autoSw.isChecked
                updater.token = token.text.toString()
                applyKeepScreenOn(); tick(); restartLoop()
            }
            .setNegativeButton("Cancel", null).show()
    }

    // ---------------------------------------------------------------- updates
    private fun checkForUpdate(manual: Boolean) {
        lifecycleScope.launch {
            try {
                val info = updater.check()
                if (updater.isNewer(info)) {
                    pendingUpdate = info
                    updateBanner.text = "Update available: v${info.versionName}  (tap to install)" + if (info.notes.isNotEmpty()) "\n${info.notes}" else ""
                    updateBanner.visibility = View.VISIBLE
                    if (manual) installUpdate(info)
                } else if (manual) {
                    Toast.makeText(this@MainActivity, "Up to date (v${BuildConfig.VERSION_NAME})", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Updater.AuthRequired) {
                if (manual) askToken()
            } catch (e: Exception) {
                if (manual) Toast.makeText(this@MainActivity, "Update check failed: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun installUpdate(info: Updater.Info) {
        AlertDialog.Builder(this)
            .setTitle("Install v${info.versionName}?")
            .setMessage((info.notes.ifEmpty { "A newer build is on GitHub." }) + "\n\nAndroid will ask you to confirm the install.")
            .setPositiveButton("Download") { _, _ ->
                lifecycleScope.launch {
                    try {
                        updateBanner.visibility = View.VISIBLE
                        val apk = updater.download(info) { pct -> updateBanner.post { updateBanner.text = "Downloading v${info.versionName}: $pct%" } }
                        updateBanner.text = "Downloaded. Opening installer…"
                        startActivity(updater.installIntent(apk))
                    } catch (e: Updater.AuthRequired) {
                        askToken()
                    } catch (e: Exception) {
                        updateBanner.text = "Update failed: ${e.message}"
                    }
                }
            }
            .setNegativeButton("Later", null)
            .show()
    }

    private fun askToken() {
        val input = EditText(this).apply { hint = "GitHub token"; setText(updater.token) }
        AlertDialog.Builder(this).setTitle("GitHub access needed")
            .setMessage("The repo is private. Paste a fine-grained token with Contents: read. It is stored only on this phone.")
            .setView(input)
            .setPositiveButton("Save") { _, _ -> updater.token = input.text.toString(); checkForUpdate(manual = true) }
            .setNegativeButton("Cancel", null).show()
    }
}
