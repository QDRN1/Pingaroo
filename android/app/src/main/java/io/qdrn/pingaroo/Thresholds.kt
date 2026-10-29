package io.qdrn.pingaroo

import android.graphics.Color

/** Colour and verdict rules for each metric. */
object Thresholds {
    val GREEN = Color.rgb(72, 210, 110)
    val LIME = Color.rgb(170, 220, 80)
    val AMBER = Color.rgb(250, 184, 48)
    val RED = Color.rgb(240, 84, 72)
    val BLUE = Color.rgb(92, 160, 250)
    val CYAN = Color.rgb(70, 200, 220)
    val PURPLE = Color.rgb(176, 120, 240)
    val MUTED = Color.rgb(150, 158, 172)
    val TEXT = Color.rgb(242, 244, 248)

    fun rsrp(v: Int?): Int = when { v == null -> MUTED; v >= -80 -> GREEN; v >= -90 -> LIME; v >= -100 -> AMBER; else -> RED }
    fun sinr(v: Int?): Int = when { v == null -> MUTED; v >= 20 -> GREEN; v >= 10 -> LIME; v >= 3 -> AMBER; else -> RED }
    fun rsrq(v: Int?): Int = when { v == null -> MUTED; v >= -10 -> GREEN; v >= -15 -> AMBER; else -> RED }
    fun rssi(v: Int?): Int = when { v == null -> MUTED; v >= -65 -> GREEN; v >= -80 -> AMBER; else -> RED }

    /** 0..1 position of a value inside a range, for bars. */
    fun frac(v: Int?, lo: Int, hi: Int): Float = if (v == null) 0f else ((v - lo).toFloat() / (hi - lo)).coerceIn(0f, 1f)

    /** Verdict from SINR first, RSRP second. */
    fun verdict(rsrp: Int?, sinr: Int?): Pair<String, Int> {
        if (rsrp == null && sinr == null) return "NO SIGNAL DATA" to MUTED
        if (sinr == null) {
            // RSRP only: say so rather than inventing a quality figure
            val r = rsrp!!
            return when {
                r >= -80 -> "STRONG (RSRP ONLY)" to GREEN
                r >= -90 -> "OK (RSRP ONLY)" to LIME
                r >= -100 -> "WEAK (RSRP ONLY)" to AMBER
                else -> "VERY WEAK (RSRP ONLY)" to RED
            }
        }
        val s = sinr
        val r = rsrp ?: -90
        return when {
            s >= 20 && r >= -85 -> "EXCELLENT" to GREEN
            s >= 13 && r >= -95 -> "GOOD" to LIME
            s >= 5 && r >= -105 -> "FAIR" to AMBER
            s >= 0 -> "POOR" to RED
            else -> "INTERFERENCE" to RED
        }
    }
}
