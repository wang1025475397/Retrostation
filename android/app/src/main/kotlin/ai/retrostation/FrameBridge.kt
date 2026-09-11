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

    fun close() {
        closed = true
    }

    fun push(index: Int, rgba: ByteArray) {
        if (closed || index !in surfaces.indices) return
        val (lw, lh) = logical[index]
        if (rgba.size != lw * lh * 4) return // size mismatch: ignore rather than crash
        val bmp = Bitmap.createBitmap(lw, lh, Bitmap.Config.ARGB_8888)
        bmp.copyPixelsFromBuffer(ByteBuffer.wrap(rgba))
        surfaces[index].setBitmap(bmp)
    }
}
