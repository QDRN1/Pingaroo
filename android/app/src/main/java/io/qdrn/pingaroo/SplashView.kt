package io.qdrn.pingaroo

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.View
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin

/** 8-bit boot screen: sunset, stars, a hopping kangaroo, pixel title and credit. */
class SplashView(ctx: Context) : View(ctx) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 6f; color = PixelArt.WIFI }
    private val sky = listOf(Color.rgb(12, 10, 36), Color.rgb(26, 18, 64), Color.rgb(52, 24, 84), Color.rgb(120, 48, 90), Color.rgb(220, 110, 70))
    private val start = System.nanoTime()
    var onDone: (() -> Unit)? = null
    var durationMs = 3800L
    private var finished = false

    private fun t(): Float = (System.nanoTime() - start) / 1_000_000_000f

    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        val t = t()
        // pixel unit from the screen width: the scene is designed on a 480-wide grid
        val u = w / 480f
        val groundY = h - 90f * u
        val bandH = groundY / sky.size
        for ((i, col) in sky.withIndex()) { paint.color = col; c.drawRect(0f, i * bandH, w, (i + 1) * bandH + 1, paint) }
        paint.color = Color.rgb(255, 240, 180)
        for (k in 0 until 40) {
            val sx = ((k * 97 + 13) % 480) * u
            val sy = ((k * 53 + 7) % (bandH * 2 / u).toInt().coerceAtLeast(1)) * u
            if ((t * 3 + k).toInt() % 4 != 0) c.drawRect(sx, sy, sx + 3 * u, sy + 3 * u, paint)
        }
        // sun
        val sunX = w - 90f * u; val sunY = bandH * 3 + 6 * u
        for ((r, col) in listOf(26 to Color.rgb(255, 170, 60), 20 to Color.rgb(255, 200, 90), 14 to Color.rgb(255, 230, 140))) {
            paint.color = col; c.drawRect(sunX - r * u, sunY - r * u / 2, sunX + r * u, sunY + r * u / 2, paint)
        }
        // ground
        paint.color = Color.rgb(70, 40, 20); c.drawRect(0f, groundY, w, h, paint)
        paint.color = Color.rgb(110, 62, 28)
        var k = 0f
        while (k < w) { val off = (t * 60 * u) % (16 * u); c.drawRect(k + off, groundY, k + off + 8 * u, groundY + 4 * u, paint); k += 16 * u }
        c.drawRect(0f, groundY + 4 * u, w, groundY + 8 * u, paint)
        // kangaroo
        val scale = 5f * u
        val kh = PixelArt.KANGAROO_H * scale
        val hop = abs(sin(t * 4.0)).toFloat() * 34f * u
        val x0 = 30f * u
        val y0 = groundY - kh + 6 * u - hop
        paint.color = Color.rgb(50, 28, 14)
        c.drawRect(x0 + 40 * u, groundY + 2 * u, x0 + 40 * u + (120f * u - hop * 0.6f).coerceAtLeast(20 * u), groundY + 6 * u, paint)
        PixelArt.drawKangaroo(c, x0, y0, scale, paint)
        val arcs = (t * 3).toInt() % 4
        val ax = x0 + 29 * scale; val ay = y0 + 2 * scale
        for (i in 0 until arcs) { val r = (8 + i * 8) * u; c.drawArc(RectF(ax - r, ay - r, ax + r, ay + r), 225f, 90f, false, arcPaint) }
        // title + credit
        val ts = 5f * u
        PixelArt.drawText(c, "PINGAROO", 229 * u, 37 * u, ts, Color.rgb(40, 30, 60), paint)
        PixelArt.drawText(c, "PINGAROO", 232 * u, 40 * u, ts, Color.rgb(250, 246, 230), paint)
        PixelArt.drawText(c, "MOBILE", 232 * u, 86 * u, 3f * u, Color.rgb(255, 240, 180), paint)
        PixelArt.drawText(c, "DESIGNED BY COLLIN", 232 * u, 118 * u, 2f * u, Color.rgb(250, 246, 230), paint)
        PixelArt.drawText(c, "V" + BuildConfig.VERSION_NAME, 232 * u, 140 * u, 2f * u, Color.rgb(255, 240, 180), paint)
        if ((t * 2).toInt() % 2 == 0) {
            val msg = "TAP TO START"
            PixelArt.drawText(c, msg, (w - PixelArt.textWidth(msg, 3f * u)) / 2, h - 50 * u, 3f * u, Color.rgb(250, 246, 230), paint)
        }
        if (!finished && t * 1000 > durationMs) finish()
        postInvalidateOnAnimation()
    }

    fun finish() {
        if (finished) return
        finished = true
        onDone?.invoke()
    }
}
