package io.qdrn.pingaroo

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Snapshots saved on this phone, persisted as JSON so they survive restarts. */
object SnapshotStore {
    val items = ArrayList<Sample>()
    private var file: File? = null

    fun init(ctx: Context) {
        if (file != null) return
        file = File(ctx.getExternalFilesDir(null) ?: ctx.filesDir, "snapshots.json")
        try {
            val f = file!!
            if (f.exists()) {
                val arr = JSONArray(f.readText())
                items.clear()
                for (i in 0 until arr.length()) fromJson(arr.getJSONObject(i))?.let { items.add(it) }
            }
        } catch (_: Exception) { }
    }

    fun add(s: Sample) { items.add(0, s); save() }
    fun remove(s: Sample) { items.remove(s); save() }
    fun clear() { items.clear(); save() }
    fun best(): Sample? = items.filter { it.sinr != null || it.lteRssnr != null }.maxByOrNull { (it.sinr ?: it.lteRssnr ?: -99) }

    private fun save() {
        val f = file ?: return
        try {
            val arr = JSONArray()
            for (s in items) arr.put(toJson(s))
            f.writeText(arr.toString())
        } catch (_: Exception) { }
    }

    private fun toJson(s: Sample): JSONObject = JSONObject().apply {
        put("time", s.time); put("source", s.source); put("label", s.label)
        s.lat?.let { put("lat", it) }; s.lon?.let { put("lon", it) }; s.accuracyM?.let { put("acc", it.toDouble()) }
        put("tech", s.tech); put("operator", s.operator); put("band", s.band)
        s.arfcn?.let { put("arfcn", it) }; s.pci?.let { put("pci", it) }; s.tac?.let { put("tac", it) }; s.cellId?.let { put("cellId", it) }
        s.rsrp?.let { put("rsrp", it) }; s.rsrq?.let { put("rsrq", it) }; s.sinr?.let { put("sinr", it) }; s.rssi?.let { put("rssi", it) }
        put("bandwidth", s.bandwidth); put("lteBand", s.lteBand)
        s.lteEarfcn?.let { put("lteEarfcn", it) }; s.ltePci?.let { put("ltePci", it) }; s.lteCellId?.let { put("lteCellId", it) }; s.lteTac?.let { put("lteTac", it) }
        s.lteRsrp?.let { put("lteRsrp", it) }; s.lteRsrq?.let { put("lteRsrq", it) }; s.lteRssnr?.let { put("lteRssnr", it) }
        put("apn", s.apn); put("wanIp", s.wanIp); put("event", s.event)
    }

    private fun JSONObject.i(k: String): Int? = if (has(k)) getInt(k) else null
    private fun JSONObject.l(k: String): Long? = if (has(k)) getLong(k) else null
    private fun JSONObject.d(k: String): Double? = if (has(k)) getDouble(k) else null

    private fun fromJson(j: JSONObject): Sample? = try {
        Sample(time = j.optString("time"), source = j.optString("source"), label = j.optString("label"),
            lat = j.d("lat"), lon = j.d("lon"), accuracyM = j.d("acc")?.toFloat(),
            tech = j.optString("tech"), operator = j.optString("operator"), band = j.optString("band"),
            arfcn = j.i("arfcn"), pci = j.i("pci"), tac = j.i("tac"), cellId = j.l("cellId"),
            rsrp = j.i("rsrp"), rsrq = j.i("rsrq"), sinr = j.i("sinr"), rssi = j.i("rssi"),
            bandwidth = j.optString("bandwidth"), lteBand = j.optString("lteBand"),
            lteEarfcn = j.i("lteEarfcn"), ltePci = j.i("ltePci"), lteCellId = j.l("lteCellId"), lteTac = j.i("lteTac"),
            lteRsrp = j.i("lteRsrp"), lteRsrq = j.i("lteRsrq"), lteRssnr = j.i("lteRssnr"),
            apn = j.optString("apn"), wanIp = j.optString("wanIp"), event = j.optString("event", "snapshot"))
    } catch (_: Exception) { null }
}
