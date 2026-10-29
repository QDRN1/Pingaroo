package io.qdrn.pingaroo

/** Snapshots saved this session, in memory (the CSV has the durable copy). */
object SnapshotStore {
    val items = ArrayList<Sample>()
    fun add(s: Sample) { items.add(0, s) }
    fun clear() = items.clear()
    fun best(): Sample? = items.filter { it.sinr != null || it.lteRssnr != null }
        .maxByOrNull { (it.sinr ?: it.lteRssnr ?: -99) }
}
