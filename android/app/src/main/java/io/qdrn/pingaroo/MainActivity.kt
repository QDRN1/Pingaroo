package io.qdrn.pingaroo

import android.Manifest
import android.os.Bundle
import android.view.WindowManager
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.button.MaterialButton
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

    private lateinit var rsrpView: TextView
    private lateinit var sinrView: TextView
    private lateinit var rsrqView: TextView
    private lateinit var cellLine: TextView
    private lateinit var details: TextView
    private lateinit var status: TextView
    private lateinit var logInfo: TextView
    private lateinit var clock: TextView
    private lateinit var labelInput: TextInputEditText
    private lateinit var gatewayUrl: TextInputEditText

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        location.start()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        rsrpView = findViewById(R.id.rsrp)
        sinrView = findViewById(R.id.sinr)
        rsrqView = findViewById(R.id.rsrq)
        cellLine = findViewById(R.id.cellLine)
        details = findViewById(R.id.details)
        status = findViewById(R.id.status)
        logInfo = findViewById(R.id.logInfo)
        clock = findViewById(R.id.clock)
        labelInput = findViewById(R.id.labelInput)
        gatewayUrl = findViewById(R.id.gatewayUrl)

        phone = PhoneRadio(this)
        location = LocationHelper(this)
        logger = CsvLogger(this)
        gateway = GatewayClient(gatewayUrl.text?.toString()?.trim().orEmpty().ifEmpty { "http://192.168.1.1" })

        findViewById<MaterialButtonToggleGroup>(R.id.sourceGroup).addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (isChecked) source = if (checkedId == R.id.btnPhone) "phone" else "gateway"
        }
        findViewById<MaterialSwitch>(R.id.logSwitch).setOnCheckedChangeListener { _, on ->
            logging = on
            updateLogInfo()
        }
        findViewById<MaterialButton>(R.id.shareBtn).setOnClickListener {
            logger.shareIntent()?.let { startActivity(android.content.Intent.createChooser(it, "Share CSV")) }
                ?: run { status.text = "Nothing logged yet. Turn on Log To CSV first." }
        }
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
                        val label = labelInput.text?.toString()?.trim().orEmpty()
                        val raw = if (source == "gateway") gateway.fetch(label) else phone.fetch(label)
                        val loc = location.last
                        val sample = raw.copy(lat = loc?.latitude, lon = loc?.longitude, accuracyM = loc?.accuracy)
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

    private fun colorFor(sinr: Int?, rsrp: Int?): Int = when {
        sinr != null && sinr >= 10 -> R.color.green
        sinr != null && sinr < 3 -> R.color.red
        sinr != null -> R.color.amber
        rsrp != null && rsrp >= -85 -> R.color.green
        rsrp != null && rsrp < -105 -> R.color.red
        else -> R.color.text
    }

    private fun render(s: Sample) {
        clock.text = SimpleDateFormat("EEE MMM d  HH:mm:ss", Locale.US).format(Date())
        rsrpView.text = s.rsrp?.toString() ?: "-"
        sinrView.text = s.sinr?.toString() ?: "-"
        rsrqView.text = s.rsrq?.toString() ?: "-"
        val col = ContextCompat.getColor(this, colorFor(s.sinr, s.rsrp))
        sinrView.setTextColor(col)
        rsrpView.setTextColor(ContextCompat.getColor(this, if (s.rsrp == null) R.color.text else colorFor(null, s.rsrp)))
        if (s.error.isNotEmpty()) {
            cellLine.text = if (s.source == "gateway") "Gateway unreachable" else "Phone radio"
            status.text = s.error + if (s.source == "gateway") "  (is the phone on Kangaroo Labs?)" else ""
            details.text = ""
            return
        }
        cellLine.text = listOf(
            s.source.replaceFirstChar { it.uppercase() },
            s.tech.ifEmpty { null },
            s.band.ifEmpty { null },
            s.pci?.let { "PCI $it" },
            s.cellId?.let { "cell $it" },
        ).filterNotNull().joinToString("   ")
        val lines = mutableListOf<String>()
        lines += "Operator  ${s.operator.ifEmpty { "-" }}      TAC ${s.tac ?: "-"}"
        lines += "RSSI ${s.rssi ?: "-"} dBm      ARFCN ${s.arfcn ?: "-"}      BW ${s.bandwidth.ifEmpty { "-" }}"
        if (s.lteBand.isNotEmpty() || s.lteRsrp != null) {
            lines += "LTE ${s.lteBand.ifEmpty { "-" }}  PCI ${s.ltePci ?: "-"}  cell ${s.lteCellId ?: "-"}"
            lines += "LTE RSRP ${s.lteRsrp ?: "-"}  RSRQ ${s.lteRsrq ?: "-"}  SNR ${s.lteRssnr ?: "-"}  EARFCN ${s.lteEarfcn ?: "-"}"
        }
        if (s.apn.isNotEmpty() || s.wanIp.isNotEmpty()) lines += "APN ${s.apn.ifEmpty { "-" }}      WAN ${s.wanIp.ifEmpty { "-" }}"
        details.text = lines.joinToString("\n")
        val gps = if (s.lat != null) "GPS %.5f, %.5f (±%.0f m)".format(s.lat, s.lon, s.accuracyM ?: 0f) else "GPS: no fix yet"
        status.text = "$gps   ${s.time.substring(11, 19)}"
    }

    private fun updateLogInfo() {
        logInfo.text = if (logging) "Logging: ${logger.count} samples  ->  ${logger.path()}" else "Not logging (${logger.count} samples in this session's file)"
    }
}
