package io.qdrn.pingaroo

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View

/** Big verdict banner: EXCELLENT / GOOD / FAIR / POOR with a 5-bar meter. */
class VerdictView @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null) : View(ctx, attrs) {
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    private val d = resources.displayMetrics.density
    var verdict = "NO SIGNAL DATA"
    var color = Thresholds.MUTED
    var bars = 0
    var subtitle = ""
    var pulse = 0f

    fun set(verdict: String, color: Int, bars: Int, subtitle: String) {
        this.verdict = verdict; this.color = color; this.bars = bars; this.subtitle = subtitle
        pulse = 1f
        invalidate()
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        p.style = Paint.Style.FILL
        p.color = Color.rgb(26, 33, 46)
        c.drawRoundRect(RectF(0f, 0f, w, h), 14 * d, 14 * d, p)
        // glow bar at the top in the verdict colour
        p.color = color
        c.drawRoundRect(RectF(0f, 0f, w, 6 * d), 14 * d, 14 * d, p)
        // 5-bar meter (left)
        for (i in 0 until 5) {
            val bh = (10 + i * 8) * d
            val x = 16 * d + i * 14 * d
            p.color = if (i < bars) color else Color.rgb(50, 58, 76)
            c.drawRoundRect(RectF(x, h - 16 * d - bh, x + 10 * d, h - 16 * d), 2 * d, 2 * d, p)
        }
        // verdict text
        p.color = color; p.isFakeBoldText = true; p.textSize = 30 * d
        val s = if (pulse > 0) 1f + pulse * 0.04f else 1f
        c.save(); c.scale(s, s, 100 * d, h / 2)
        c.drawText(verdict, 96 * d, h / 2 + 6 * d, p)
        c.restore()
        p.color = Thresholds.MUTED; p.isFakeBoldText = false; p.textSize = 13 * d
        c.drawText(subtitle, 96 * d, h - 16 * d, p)
        if (pulse > 0) { pulse -= 0.08f; postInvalidateOnAnimation() }
    }
}
