package io.qdrn.pingaroo

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import androidx.core.content.ContextCompat

/** Last known GPS fix, kept fresh with 1 s updates while the screen is open. */
class LocationHelper(private val ctx: Context) : LocationListener {
    private val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    @Volatile var last: Location? = null
        private set
    /** The last fix if it is under two minutes old, else null. Samples use this. */
    val fresh: Location? get() = last?.takeIf { System.currentTimeMillis() - it.time <= MAX_AGE_MS }

    companion object { const val MAX_AGE_MS = 120_000L }

    fun start() {
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) return
        try {
            // A cached fix can be hours old, and so can the one kept from before stop(): keep the
            // newest of the two only when it is recent.
            val cached = lm.getLastKnownLocation(LocationManager.GPS_PROVIDER) ?: lm.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)
            last = listOfNotNull(last, cached).filter { System.currentTimeMillis() - it.time <= MAX_AGE_MS }.maxByOrNull { it.time }
            if (lm.isProviderEnabled(LocationManager.GPS_PROVIDER))
                lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 1000L, 0f, this)
            if (lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER))
                lm.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, 2000L, 0f, this)
        } catch (_: SecurityException) { }
    }

    fun stop() {
        try { lm.removeUpdates(this) } catch (_: Exception) { }
    }

    override fun onLocationChanged(location: Location) {
        val cur = last
        if (cur == null || location.accuracy <= cur.accuracy + 5 || location.time - cur.time > 10_000) last = location
    }
}
