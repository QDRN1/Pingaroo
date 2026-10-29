package io.qdrn.pingaroo

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.CellIdentityNr
import android.telephony.CellInfo
import android.telephony.CellInfoLte
import android.telephony.CellInfoNr
import android.telephony.CellSignalStrengthLte
import android.telephony.CellSignalStrengthNr
import android.telephony.TelephonyCallback
import android.telephony.TelephonyDisplayInfo
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.Executor
import kotlin.coroutines.resume

/**
 * The phone's own cellular measurements.
 *
 * Serving-cell signal values come from TelephonyManager.signalStrength (reliable on
 * Samsung, including NR in NSA).  Cell identity (PCI, TAC, cell id, band) comes from
 * the cell-info list.  The 5G NSA / 5G+ state comes from the display-info callback.
 */
class PhoneRadio(private val ctx: Context) {

    private val tm = ctx.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
    private val mainExecutor: Executor = ContextCompat.getMainExecutor(ctx)
    @Volatile private var overrideType: Int = -1
    private var callback: Any? = null

    private fun hasLocation(): Boolean =
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED

    private fun hasPhoneState(): Boolean =
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED

    /** Start listening for display-info (5G NSA/5G+) changes; safe to call repeatedly. */
    fun start() {
        if (callback != null || Build.VERSION.SDK_INT < 31 || !hasPhoneState()) return
        try {
            val cb = object : TelephonyCallback(), TelephonyCallback.DisplayInfoListener {
                override fun onDisplayInfoChanged(info: TelephonyDisplayInfo) {
                    overrideType = info.overrideNetworkType
                }
            }
            tm.registerTelephonyCallback(mainExecutor, cb)
            callback = cb
        } catch (_: SecurityException) { }
    }

    fun stop() {
        val cb = callback
        if (cb != null && Build.VERSION.SDK_INT >= 31) {
            try { tm.unregisterTelephonyCallback(cb as TelephonyCallback) } catch (_: Exception) { }
        }
        callback = null
    }

    private suspend fun freshCellInfo(): List<CellInfo> {
        if (!hasLocation()) return emptyList()
        val updated = withTimeoutOrNull(2500) {
            suspendCancellableCoroutine<List<CellInfo>> { cont ->
                try {
                    tm.requestCellInfoUpdate(mainExecutor, object : TelephonyManager.CellInfoCallback() {
                        override fun onCellInfo(cellInfo: MutableList<CellInfo>) { if (cont.isActive) cont.resume(cellInfo) }
                        override fun onError(errorCode: Int, detail: Throwable?) { if (cont.isActive) cont.resume(emptyList()) }
                    })
                } catch (e: SecurityException) { if (cont.isActive) cont.resume(emptyList()) }
            }
        }
        return updated ?: try { tm.allCellInfo ?: emptyList() } catch (_: SecurityException) { emptyList() }
    }

    private fun vi(v: Int): Int? = if (v == CellInfo.UNAVAILABLE || v == Int.MAX_VALUE || v == Int.MIN_VALUE) null else v
    private fun vl(v: Long): Long? = if (v == CellInfo.UNAVAILABLE_LONG || v == Long.MAX_VALUE) null else v

    @Suppress("DEPRECATION")
    private fun techName(hasNr: Boolean): String {
        val base = try { tm.dataNetworkType } catch (_: SecurityException) { TelephonyManager.NETWORK_TYPE_UNKNOWN }
        val ov = overrideType
        if (Build.VERSION.SDK_INT >= 30) {
            when (ov) {
                TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_NSA -> return "5G NSA"
                TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_ADVANCED -> return "5G+"
                TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_NSA_MMWAVE -> return "5G+ mmWave"
                TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_LTE_CA -> return "LTE CA"
                TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_LTE_ADVANCED_PRO -> return "LTE+"
            }
        }
        return when (base) {
            TelephonyManager.NETWORK_TYPE_NR -> "5G SA"
            TelephonyManager.NETWORK_TYPE_LTE -> if (hasNr) "5G NSA" else "LTE"
            TelephonyManager.NETWORK_TYPE_UNKNOWN -> if (hasNr) "5G" else ""
            else -> "type $base"
        }
    }

    suspend fun fetch(label: String): Sample {
        if (!hasLocation()) return Sample(source = "phone", label = label, error = "location permission needed for cell info")
        start()
        // 1. serving-cell signal values
        val ss = try { tm.signalStrength } catch (_: SecurityException) { null }
        val nrS = ss?.getCellSignalStrengths(CellSignalStrengthNr::class.java)?.firstOrNull()
        val lteS = ss?.getCellSignalStrengths(CellSignalStrengthLte::class.java)?.firstOrNull()
        val level = ss?.level
        // 2. identity from the cell list (registered first)
        val cells = freshCellInfo()
        val nrC = cells.filterIsInstance<CellInfoNr>().sortedByDescending { it.isRegistered }.firstOrNull()
        val lteC = cells.filterIsInstance<CellInfoLte>().sortedByDescending { it.isRegistered }.firstOrNull()
        val operator = try { tm.networkOperatorName ?: "" } catch (_: Exception) { "" }
        val nrRsrp = nrS?.ssRsrp?.let { vi(it) } ?: (nrC?.cellSignalStrength as? CellSignalStrengthNr)?.ssRsrp?.let { vi(it) }
        val hasNr = nrRsrp != null || nrC != null
        val tech = techName(hasNr)
        if (nrS == null && lteS == null && nrC == null && lteC == null) {
            return Sample(source = "phone", label = label, operator = operator, tech = tech,
                error = "no cell data from Android (airplane mode? location services off?)")
        }
        val nid = nrC?.cellIdentity as? CellIdentityNr
        val nrSig = nrS ?: (nrC?.cellSignalStrength as? CellSignalStrengthNr)
        val nrArfcn = nid?.nrarfcn?.let { vi(it) }
        val nrBand = nid?.let { id -> if (Build.VERSION.SDK_INT >= 30 && id.bands.isNotEmpty()) "n" + id.bands.joinToString("/") else Bands.nrBandFromArfcn(nrArfcn) } ?: ""
        val lid = lteC?.cellIdentity
        val lteSig = lteS ?: lteC?.cellSignalStrength
        val earfcn = lid?.earfcn?.let { vi(it) }
        val lteBand = lid?.let { id -> if (Build.VERSION.SDK_INT >= 30 && id.bands.isNotEmpty()) "B" + id.bands.joinToString("/") else Bands.lteBandFromEarfcn(earfcn) } ?: ""
        return Sample(
            source = "phone",
            label = label,
            tech = tech,
            operator = operator,
            band = nrBand,
            arfcn = nrArfcn,
            pci = nid?.pci?.let { vi(it) },
            tac = nid?.tac?.let { vi(it) },
            cellId = nid?.nci?.let { vl(it) },
            rsrp = nrSig?.ssRsrp?.let { vi(it) },
            rsrq = nrSig?.ssRsrq?.let { vi(it) },
            sinr = nrSig?.ssSinr?.let { vi(it) },
            rssi = lteSig?.rssi?.let { vi(it) },
            bandwidth = lid?.bandwidth?.let { vi(it) }?.let { "${it / 1000} MHz" } ?: "",
            lteBand = lteBand,
            lteEarfcn = earfcn,
            ltePci = lid?.pci?.let { vi(it) },
            lteCellId = lid?.ci?.let { vi(it) }?.toLong(),
            lteRsrp = lteSig?.rsrp?.let { vi(it) },
            lteRsrq = lteSig?.rsrq?.let { vi(it) },
            lteRssnr = lteSig?.rssnr?.let { vi(it) },
            level = level,
        )
    }
}

/** Band lookups from channel numbers, for devices that do not report bands directly. */
object Bands {
    fun nrArfcnToMhz(n: Int): Double = when {
        n < 600000 -> n * 0.005
        n < 2016667 -> 3000.0 + (n - 600000) * 0.015
        else -> 24250.08 + (n - 2016667) * 0.06
    }

    fun nrBandFromArfcn(n: Int?): String {
        if (n == null) return ""
        val f = nrArfcnToMhz(n)
        return when {
            f in 3700.0..3980.0 -> "n77"
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
