package io.qdrn.pingaroo

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.view.View

/** Rolling history of RSRP and SINR (last 90 samples). */
class SparklineView(ctx: Context) : View(ctx) {
    private val rsrp = ArrayDeque<Int?>()
    private val sinr = ArrayDeque<Int?>()
    private val cap = 90
    private val d = resources.displayMetrics.density
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)

    fun push(r: Int?, s: Int?) {
        rsrp.addLast(r); sinr.addLast(s)
        while (rsrp.size > cap) rsrp.removeFirst()
        while (sinr.size > cap) sinr.removeFirst()
        invalidate()
    }

    fun clear() { rsrp.clear(); sinr.clear(); invalidate() }

    private fun series(c: Canvas, data: ArrayDeque<Int?>, lo: Int, hi: Int, color: Int, top: Float, bottom: Float) {
        if (data.size < 2) return
        val w = width.toFloat()
        val path = Path(); var started = false
        val step = (w - 20 * d) / (cap - 1)
        val offset = cap - data.size
        for ((i, v) in data.withIndex()) {
            if (v == null) { started = false; continue }
            val x = 10 * d + (i + offset) * step
            val f = ((v - lo).toFloat() / (hi - lo)).coerceIn(0f, 1f)
            val y = bottom - f * (bottom - top)
            if (!started) { path.moveTo(x, y); started = true } else path.lineTo(x, y)
        }
        p.style = Paint.Style.STROKE; p.strokeWidth = 2.5f * d; p.color = color
        c.drawPath(path, p)
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        p.style = Paint.Style.FILL; p.color = Color.rgb(26, 33, 46)
        c.drawRoundRect(RectF(0f, 0f, w, h), 12 * d, 12 * d, p)
        p.color = Thresholds.MUTED; p.textSize = 12 * d
        c.drawText("RSRP", 12 * d, 18 * d, p)
        p.color = Thresholds.BLUE; c.drawRect(48 * d, 10 * d, 60 * d, 14 * d, p)
        p.color = Thresholds.MUTED; c.drawText("SINR", 70 * d, 18 * d, p)
        p.color = Thresholds.AMBER; c.drawRect(104 * d, 10 * d, 116 * d, 14 * d, p)
        p.color = Thresholds.MUTED; c.drawText("last ${rsrp.size} samples", w - p.measureText("last ${rsrp.size} samples") - 12 * d, 18 * d, p)
        val top = 26 * d; val bottom = h - 10 * d
        p.color = Color.rgb(40, 48, 64); p.strokeWidth = 1f
        for (k in 1..3) { val y = top + (bottom - top) * k / 4; c.drawLine(10 * d, y, w - 10 * d, y, p) }
        series(c, rsrp, -120, -60, Thresholds.BLUE, top, bottom)
        series(c, sinr, -5, 30, Thresholds.AMBER, top, bottom)
    }
}
