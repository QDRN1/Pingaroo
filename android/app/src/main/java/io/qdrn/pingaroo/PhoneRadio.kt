package io.qdrn.pingaroo

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.CellInfo
import android.telephony.CellInfoLte
import android.telephony.CellInfoNr
import android.telephony.CellIdentityNr
import android.telephony.CellSignalStrengthNr
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.Executor
import kotlin.coroutines.resume

/** The phone's own cellular measurements via TelephonyManager. */
class PhoneRadio(private val ctx: Context) {

    private val tm = ctx.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
    private val mainExecutor: Executor = ContextCompat.getMainExecutor(ctx)

    private fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED

    private suspend fun freshCellInfo(): List<CellInfo> {
        if (!hasPermission()) return emptyList()
        val updated = withTimeoutOrNull(2500) {
            suspendCancellableCoroutine<List<CellInfo>> { cont ->
                try {
                    tm.requestCellInfoUpdate(mainExecutor, object : TelephonyManager.CellInfoCallback() {
                        override fun onCellInfo(cellInfo: MutableList<CellInfo>) {
                            if (cont.isActive) cont.resume(cellInfo)
                        }
                        override fun onError(errorCode: Int, detail: Throwable?) {
                            if (cont.isActive) cont.resume(emptyList())
                        }
                    })
                } catch (e: SecurityException) {
                    if (cont.isActive) cont.resume(emptyList())
                }
            }
        }
        return updated ?: try { tm.allCellInfo ?: emptyList() } catch (_: SecurityException) { emptyList() }
    }

    private fun validInt(v: Int): Int? = if (v == CellInfo.UNAVAILABLE || v == Int.MAX_VALUE) null else v
    private fun validLong(v: Long): Long? = if (v == CellInfo.UNAVAILABLE_LONG || v == Long.MAX_VALUE) null else v

    @Suppress("DEPRECATION")
    private fun techName(): String {
        val type = try { tm.dataNetworkType } catch (_: SecurityException) { TelephonyManager.NETWORK_TYPE_UNKNOWN }
        return when (type) {
            TelephonyManager.NETWORK_TYPE_NR -> "5G SA"
            TelephonyManager.NETWORK_TYPE_LTE -> "LTE"
            TelephonyManager.NETWORK_TYPE_UNKNOWN -> ""
            else -> "type $type"
        }
    }

    suspend fun fetch(label: String): Sample {
        if (!hasPermission()) return Sample(source = "phone", label = label, error = "location permission needed for cell info")
        val cells = freshCellInfo()
        val nr = cells.filterIsInstance<CellInfoNr>().sortedByDescending { it.isRegistered }.firstOrNull()
        val lte = cells.filterIsInstance<CellInfoLte>().sortedByDescending { it.isRegistered }.firstOrNull()
        val operator = try { tm.networkOperatorName ?: "" } catch (_: Exception) { "" }
        var tech = techName()
        if (nr != null && lte != null && !nr.isRegistered) tech = "5G NSA"
        if (nr == null && lte == null) {
            return Sample(source = "phone", label = label, operator = operator, tech = tech,
                error = "no cell info reported (airplane mode, or OS withholds it)")
        }
        val nid = nr?.cellIdentity as? CellIdentityNr
        val nss = nr?.cellSignalStrength as? CellSignalStrengthNr
        val nrArfcn = nid?.nrarfcn?.let { validInt(it) }
        val nrBand = nid?.let { id ->
            if (Build.VERSION.SDK_INT >= 30 && id.bands.isNotEmpty()) "n" + id.bands.joinToString("/") else Bands.nrBandFromArfcn(nrArfcn)
        } ?: ""
        val lid = lte?.cellIdentity
        val lss = lte?.cellSignalStrength
        val earfcn = lid?.earfcn?.let { validInt(it) }
        val lteBand = lid?.let { id ->
            if (Build.VERSION.SDK_INT >= 30 && id.bands.isNotEmpty()) "B" + id.bands.joinToString("/") else Bands.lteBandFromEarfcn(earfcn)
        } ?: ""
        return Sample(
            source = "phone",
            label = label,
            tech = tech,
            operator = operator,
            band = nrBand,
            arfcn = nrArfcn,
            pci = nid?.pci?.let { validInt(it) },
            tac = nid?.tac?.let { validInt(it) },
            cellId = nid?.nci?.let { validLong(it) },
            rsrp = nss?.ssRsrp?.let { validInt(it) },
            rsrq = nss?.ssRsrq?.let { validInt(it) },
            sinr = nss?.ssSinr?.let { validInt(it) },
            rssi = lss?.rssi?.let { validInt(it) },
            bandwidth = lid?.bandwidth?.let { validInt(it) }?.let { "${it / 1000} MHz" } ?: "",
            lteBand = lteBand,
            lteEarfcn = earfcn,
            ltePci = lid?.pci?.let { validInt(it) },
            lteCellId = lid?.ci?.let { validInt(it) }?.toLong(),
            lteRsrp = lss?.rsrp?.let { validInt(it) },
            lteRsrq = lss?.rsrq?.let { validInt(it) },
            lteRssnr = lss?.rssnr?.let { validInt(it) },
        )
    }
}

/** Band lookups from channel numbers, for devices that do not report bands directly. */
object Bands {
    /** NR-ARFCN to frequency in MHz (3GPP 38.104 global raster). */
    fun nrArfcnToMhz(n: Int): Double = when {
        n < 600000 -> n * 0.005
        n < 2016667 -> 3000.0 + (n - 600000) * 0.015
        else -> 24250.08 + (n - 2016667) * 0.06
    }

    fun nrBandFromArfcn(n: Int?): String {
        if (n == null) return ""
        val f = nrArfcnToMhz(n)
        return when {
            f in 3700.0..3980.0 -> "n77"          // C-band (US)
            f in 3300.0..3700.0 -> "n78"
            f in 2496.0..2690.0 -> "n41"
            f in 1930.0..1990.0 -> "n2"
            f in 2110.0..2200.0 -> "n66"
            f in 869.0..894.0 -> "n5"
            f in 617.0..652.0 -> "n71"
            f in 758.0..768.0 -> "n14"
            f in 2350.0..2360.0 -> "n30"
            f > 24000 -> "mmWave"
            else -> "n? (${"%.0f".format(f)} MHz)"
        }
    }

    fun lteBandFromEarfcn(e: Int?): String {
        if (e == null) return ""
        return when (e) {
            in 600..1199 -> "B2"
            in 1950..2399 -> "B4"
            in 2400..2649 -> "B5"
            in 5010..5179 -> "B12"
            in 5280..5379 -> "B14"
            in 9660..9769 -> "B29"
            in 9770..9869 -> "B30"
            in 66436..67335 -> "B66"
            in 0..599 -> "B1"
            in 1200..1949 -> "B3"
            else -> "B? ($e)"
        }
    }
}
