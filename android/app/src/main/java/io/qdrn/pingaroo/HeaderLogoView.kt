package io.qdrn.pingaroo

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View

/** Small pixel kangaroo + pixel title for the main screen header. */
class HeaderLogoView @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null) : View(ctx, attrs) {
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val h = (34 * resources.displayMetrics.density).toInt()
        val scale = h.toFloat() / PixelArt.KANGAROO_H
        val ts = (h / 7f) * 0.72f
        val w = (PixelArt.KANGAROO_W * scale + 8f + PixelArt.textWidth("PINGAROO", ts) + 4f).toInt()
        setMeasuredDimension(resolveSize(w, widthMeasureSpec), resolveSize(h, heightMeasureSpec))
    }

    override fun onDraw(c: Canvas) {
        val h = height.toFloat()
        val scale = h / PixelArt.KANGAROO_H
        PixelArt.drawKangaroo(c, 0f, 0f, scale, p)
        val tx = PixelArt.KANGAROO_W * scale + 8f
        val ts = (h / 7f) * 0.72f
        PixelArt.drawText(c, "PINGAROO", tx + 1.5f, h * 0.16f + 1.5f, ts, Color.rgb(40, 30, 60), p)
        PixelArt.drawText(c, "PINGAROO", tx, h * 0.16f, ts, Color.rgb(250, 246, 230), p)
    }
}
