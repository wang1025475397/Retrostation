package ai.retrostation

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
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

    /** Filtered paint: the logical bitmap is upscaled to the physical panel, and
     *  the default Paint uses nearest-neighbour -- which makes text, rounded
     *  corners and cover art look jaggy ("毛边").  Enabling FILTER_BITMAP gives a
     *  bilinear upscale, so edges stay smooth at any resolution. */
    private val paint = Paint().apply { isFilterBitmap = true }

    @Volatile private var bitmap: Bitmap? = null

    /** Replace the bitmap for this view (called from the Python push path). */
    fun setBitmap(bmp: Bitmap) {
        bitmap = bmp
        // postInvalidate(), not postInvalidateOnAnimation(): the latter waits on a
        // vsync that the API 35 emulator withholds after the first frame, so every
        // pushed frame after the launch logo is never composited -- the UI "freezes"
        // on the logo until a lock/unlock forces a window redraw.  A plain traversal
        // fires regardless of vsync, so each frame reaches the screen (DESIGN.ANDROID
        // §4.4, emulator cold-start quirk).
        postInvalidate()
    }

    // Mirror the unlock path the user had to do by hand: when the window regains
    // focus or becomes visible again, redraw the latest bitmap.  These run on the
    // UI thread, so invalidate() is safe here (and equals a forced re-present).
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) postInvalidate()
    }

    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView, visibility)
        if (visibility == View.VISIBLE) postInvalidate()
    }

    override fun onDraw(canvas: Canvas) {
        val bmp = bitmap ?: return
        val dst = RectF(0f, 0f, width.toFloat(), height.toFloat())
        canvas.drawBitmap(bmp, null, dst, paint)
    }
}
