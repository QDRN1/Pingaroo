package io.qdrn.pingaroo

import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter

/** One measurement, from either the gateway's API or the phone's own radio. */
data class Sample(
    val time: String = OffsetDateTime.now().format(DateTimeFormatter.ISO_OFFSET_DATE_TIME),
    val source: String = "",            // "gateway" | "phone"
    val label: String = "",
    val lat: Double? = null,
    val lon: Double? = null,
    val accuracyM: Float? = null,
    val tech: String = "",              // 5G+, 5G NSA, LTE ...
    val operator: String = "",
    val band: String = "",              // n77, B2 ...
    val arfcn: Int? = null,
    val pci: Int? = null,
    val tac: Int? = null,
    val cellId: Long? = null,
    val rsrp: Int? = null,
    val rsrq: Int? = null,
    val sinr: Int? = null,
    val rssi: Int? = null,
    val bandwidth: String = "",
    // LTE anchor / secondary carrier (phone) or anchor band (gateway)
    val lteBand: String = "",
    val lteEarfcn: Int? = null,
    val ltePci: Int? = null,
    val lteCellId: Long? = null,
    val lteRsrp: Int? = null,
    val lteRsrq: Int? = null,
    val lteRssnr: Int? = null,
    val apn: String = "",
    val wanIp: String = "",
    val error: String = "",
    val event: String = "",             // "" for periodic samples, "snapshot" for saved spots
) {
    companion object {
        val CSV_COLUMNS = listOf(
            "time", "source", "label", "lat", "lon", "accuracy_m", "tech", "operator", "band", "arfcn", "pci",
            "tac", "cell_id", "rsrp", "rsrq", "sinr", "rssi", "bandwidth", "lte_band", "lte_earfcn", "lte_pci",
            "lte_cell_id", "lte_rsrp", "lte_rsrq", "lte_rssnr", "apn", "wan_ip", "error", "event",
        )
    }

    fun csvRow(): List<Any?> = listOf(
        time, source, label, lat, lon, accuracyM, tech, operator, band, arfcn, pci, tac, cellId, rsrp, rsrq,
        sinr, rssi, bandwidth, lteBand, lteEarfcn, ltePci, lteCellId, lteRsrp, lteRsrq, lteRssnr, apn, wanIp, error, event,
    )
}
