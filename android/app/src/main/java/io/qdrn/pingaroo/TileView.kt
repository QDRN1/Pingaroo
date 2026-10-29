package io.qdrn.pingaroo

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View

/** One metric tile: caption, big value, unit, accent colour, optional bar. */
class TileView @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null) : View(ctx, attrs) {
    var caption = ""
    var value = "-"
    var unit = ""
    var accent = Thresholds.MUTED
    var barFraction: Float? = null
    var big = true

    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    private val d = resources.displayMetrics.density

    fun set(caption: String, value: String, unit: String = "", accent: Int = Thresholds.TEXT, bar: Float? = null, big: Boolean = true) {
        this.caption = caption; this.value = value; this.unit = unit; this.accent = accent; this.barFraction = bar; this.big = big
        invalidate()
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        val r = 12 * d
        p.style = Paint.Style.FILL
        p.color = Color.rgb(26, 33, 46)
        c.drawRoundRect(RectF(0f, 0f, w, h), r, r, p)
        // accent stripe on the left
        p.color = accent
        c.drawRoundRect(RectF(0f, 0f, 5 * d, h), r, r, p)
        c.drawRect(2 * d, 0f, 5 * d, h, p)
        // caption
        p.color = Thresholds.MUTED
        p.textSize = 12 * d
        p.isFakeBoldText = false
        c.drawText(caption, 14 * d, 18 * d, p)
        // value
        p.color = accent
        p.isFakeBoldText = true
        p.textSize = (if (big) 30 else 20) * d
        val vy = if (big) 50 * d else 44 * d
        var vx = 14 * d
        // shrink to fit
        while (p.measureText(value) > w - 28 * d && p.textSize > 12 * d) p.textSize -= 1 * d
        c.drawText(value, vx, vy, p)
        vx += p.measureText(value) + 4 * d
        if (unit.isNotEmpty()) {
            p.textSize = 12 * d; p.isFakeBoldText = false; p.color = Thresholds.MUTED
            c.drawText(unit, vx, vy, p)
        }
        barFraction?.let { f ->
            p.color = Color.rgb(40, 48, 64)
            c.drawRoundRect(RectF(14 * d, h - 12 * d, w - 10 * d, h - 7 * d), 3 * d, 3 * d, p)
            p.color = accent
            c.drawRoundRect(RectF(14 * d, h - 12 * d, 14 * d + (w - 24 * d) * f, h - 7 * d), 3 * d, 3 * d, p)
        }
    }
}
