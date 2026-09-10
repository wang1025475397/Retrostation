package ai.retrostation

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.RectF
import android.view.View

/**
 * One display's draw target.  Holds the latest bitmap pushed from Python and paints
 * it (GPU-accelerated via the view's hardware canvas) on every [onDraw]
 * (DESIGN.ANDROID §4.4).  The bitmap is at *logical* resolution; the View scales it
 * up to the physical panel.
 *
 * A plain [View] (rather than SurfaceView) is used deliberately: [View.onDraw] is
 * reliably re-invoked by [postInvalidateOnAnimation], whereas SurfaceView bypasses
 * the normal view draw pass and would never call onDraw.  This keeps the single
 * blit path simple and correct; both the activity's content and any dual-screen
 * [android.app.Presentation] can host one of these.
 */
class RetroSurfaceView(context: Context, val index: Int) : View(context) {

    @Volatile private var bitmap: Bitmap? = null

    /** Replace the bitmap for this view (called from the Python push path). */
    fun setBitmap(bmp: Bitmap) {
        bitmap = bmp
        postInvalidateOnAnimation()
    }

    override fun onDraw(canvas: Canvas) {
        val bmp = bitmap ?: return
        val dst = RectF(0f, 0f, width.toFloat(), height.toFloat())
        canvas.drawBitmap(bmp, null, dst, null)
    }
}
