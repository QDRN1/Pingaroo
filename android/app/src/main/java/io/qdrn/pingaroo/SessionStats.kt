package io.qdrn.pingaroo

/** Running min / avg / max per metric, plus best-spot tracking. */
class SessionStats {
    class Agg {
        var n = 0; var sum = 0.0; var min = Int.MAX_VALUE; var max = Int.MIN_VALUE
        fun add(v: Int?) { if (v == null) return; n++; sum += v; if (v < min) min = v; if (v > max) max = v }
        val avg: Int? get() = if (n == 0) null else (sum / n).toInt()
        val lo: Int? get() = if (n == 0) null else min
        val hi: Int? get() = if (n == 0) null else max
        fun reset() { n = 0; sum = 0.0; min = Int.MAX_VALUE; max = Int.MIN_VALUE }
    }

    val rsrp = Agg()
    val sinr = Agg()
    var samples = 0
    var startedAt = System.currentTimeMillis()
        private set
    var bestSinr: Int? = null
    var bestLabel = ""
    var bestRsrp: Int? = null
    /** The most recent sample added. */
    var lastRsrp: Int? = null
        private set
    var lastSinr: Int? = null
        private set
    /** The sample before the most recent one. */
    var prevRsrp: Int? = null
        private set
    var prevSinr: Int? = null
        private set

    fun add(r: Int?, s: Int?, label: String) {
        prevRsrp = lastRsrp; prevSinr = lastSinr
        rsrp.add(r); sinr.add(s); samples++
        lastRsrp = r; lastSinr = s
        if (s != null && (bestSinr == null || s > bestSinr!!)) { bestSinr = s; bestLabel = label; bestRsrp = r }
    }

    fun reset() {
        rsrp.reset(); sinr.reset(); samples = 0; bestSinr = null; bestLabel = ""; bestRsrp = null
        lastRsrp = null; lastSinr = null; prevRsrp = null; prevSinr = null
        startedAt = System.currentTimeMillis()
    }

    val elapsedS: Long get() = (System.currentTimeMillis() - startedAt) / 1000
}
