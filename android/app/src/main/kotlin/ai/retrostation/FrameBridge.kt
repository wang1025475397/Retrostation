package ai.retrostation

import android.graphics.Bitmap
import java.nio.ByteBuffer

/**
 * Receives RGBA bytes from Python ([AndroidBridge.push_frame]) and composites them
 * onto the matching [RetroSurfaceView].  One ByteBuffer wrap + one
 * copyPixelsFromBuffer per frame (~0.5 ms at 0.65 MP, DESIGN.ANDROID §4.4).
 *
 * The bitmap carries logical-resolution pixels; [RetroSurfaceView.onDraw] does the
 * free GPU upscale to the physical panel.
 */
class FrameBridge(
    private val surfaces: List<RetroSurfaceView>,
    private val logical: List<Pair<Int, Int>>,
) {
    /** Set when the host is tearing the frontend down; pushes are dropped. */
    @Volatile private var closed = false

    /**
     * Two bitmaps per surface, filled alternately.
     *
     * A fresh ``Bitmap.createBitmap`` per push allocated ~8.8 MB at the phone's
     * logical size and we push at the frame rate: ~265 MB/s of Java-heap churn,
     * which showed up as the carousel hitching every few frames while the GC
     * caught up.  Reusing a pair removes the allocation, and alternating keeps
     * the UI thread reading the buffer we are not writing (no tearing).
     */
    private val pool = Array(logical.size) { arrayOfNulls<Bitmap>(2) }
    private val cursor = IntArray(logical.size)

    fun close() {
        closed = true
    }

    fun push(index: Int, rgba: ByteArray) {
        if (closed || index !in surfaces.indices) return
        val (lw, lh) = logical[index]
        if (rgba.size != lw * lh * 4) return // size mismatch: ignore rather than crash
        val slot = cursor[index] and 1
        cursor[index] = slot + 1
        var bmp = pool[index][slot]
        if (bmp == null || bmp.width != lw || bmp.height != lh) {
            bmp = Bitmap.createBitmap(lw, lh, Bitmap.Config.ARGB_8888)
            pool[index][slot] = bmp
        }
        bmp.copyPixelsFromBuffer(ByteBuffer.wrap(rgba))
        surfaces[index].setBitmap(bmp)
    }
}
