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
        val dir = ctx.getExternalFilesDir(null) ?: ctx.filesDir
        file = File(dir, "snapshots.json")
        try {
            val f = file!!
            if (f.exists()) {
                val arr = JSONArray(f.readText())
                items.clear()
                for (i in 0 until arr.length()) fromJson(arr.getJSONObject(i))?.let { items.add(it) }
            }
        } catch (_: Exception) { }
        // Import snapshot rows from CSV files written by earlier versions (once per row).
        try {
            val known = items.map { it.time + it.label }.toHashSet()
            var added = 0
            dir.listFiles { f -> f.name.startsWith("pingaroo-mobile-") && f.name.endsWith(".csv") }?.sortedBy { it.name }?.forEach { csv ->
                val lines = csv.readLines()
                if (lines.size < 2) return@forEach
                val idx = lines[0].split(",").withIndex().associate { it.value to it.index }
                for (line in lines.drop(1)) {
                    val cells = splitCsv(line)
                    fun g(name: String): String? = idx[name]?.let { cells.getOrNull(it) }?.takeIf { it.isNotEmpty() }
                    if (g("event") != "snapshot") continue
                    val key = (g("time") ?: "") + (g("label") ?: "")
                    if (key in known) continue
                    items.add(Sample(time = g("time") ?: "", source = g("source") ?: "", label = g("label") ?: "",
                        lat = g("lat")?.toDoubleOrNull(), lon = g("lon")?.toDoubleOrNull(), accuracyM = g("accuracy_m")?.toFloatOrNull(),
                        tech = g("tech") ?: "", operator = g("operator") ?: "", band = g("band") ?: "",
                        arfcn = g("arfcn")?.toIntOrNull(), pci = g("pci")?.toIntOrNull(), tac = g("tac")?.toIntOrNull(), cellId = g("cell_id")?.toLongOrNull(),
                        rsrp = g("rsrp")?.toIntOrNull(), rsrq = g("rsrq")?.toIntOrNull(), sinr = g("sinr")?.toIntOrNull(), rssi = g("rssi")?.toIntOrNull(),
                        bandwidth = g("bandwidth") ?: "", lteBand = g("lte_band") ?: "", lteEarfcn = g("lte_earfcn")?.toIntOrNull(),
                        ltePci = g("lte_pci")?.toIntOrNull(), lteCellId = g("lte_cell_id")?.toLongOrNull(), lteTac = g("lte_tac")?.toIntOrNull(),
                        lteRsrp = g("lte_rsrp")?.toIntOrNull(), lteRsrq = g("lte_rsrq")?.toIntOrNull(), lteRssnr = g("lte_rssnr")?.toIntOrNull(),
                        apn = g("apn") ?: "", wanIp = g("wan_ip") ?: "", event = "snapshot"))
                    known.add(key); added++
                }
            }
            if (added > 0) { items.sortByDescending { it.time }; save() }
        } catch (_: Exception) { }
    }

    private fun splitCsv(line: String): List<String> {
        val out = ArrayList<String>(); val sb = StringBuilder(); var q = false; var i = 0
        while (i < line.length) {
            val c = line[i]
            when {
                c == '"' && q && i + 1 < line.length && line[i + 1] == '"' -> { sb.append('"'); i++ }
                c == '"' -> q = !q
                c == ',' && !q -> { out.add(sb.toString()); sb.setLength(0) }
                else -> sb.append(c)
            }
            i++
        }
        out.add(sb.toString())
        return out
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
