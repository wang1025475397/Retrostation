package ai.retrostation

import android.app.Presentation
import android.content.Context
import android.hardware.display.DisplayManager
import android.os.Bundle
import android.util.DisplayMetrics
import android.view.Display

/**
 * Probes the device's displays and decides how many canvases the frontend gets
 * (DESIGN.ANDROID §6.4.2).
 *
 * A dual-screen handheld exposes the second panel as a separate [Display]; we attach
 * an Android [Presentation] bound to it and give it its own [RetroSurfaceView].  Both
 * windows feed the same input queue, so gamepad focus never strands (§6.4.3).
 *
 * The probe result is a list of *(physicalWidth, physicalHeight)*; the Python side
 * converts each to a *logical* canvas size with [logicalSize] semantics (§4.1).
 */
class DisplayBridge(private val context: Context) {

    /** @return one (w,h) pair per *logical canvas*, in paint order.
     *
     *  A real dual-panel handheld still gets one canvas per physical display.
     *  A single-panel device (a phone) instead splits by orientation so the
     *  shared frontend keeps the layout it was designed around:
     *
     *  - **portrait** -> two stacked canvases: content on top, detail below.
     *    That is the handheld's dual-screen interaction model, reused verbatim
     *    (no picture-in-picture strip, real 40% for artwork and metadata).
     *  - **landscape** -> one full-screen canvas; the detail folds into the
     *    strip exactly like the handheld's single-screen mode.
     *
     *  Sizes are *logical* -- the Python side paints to a logical canvas and
     *  the GPU upscales (DESIGN.ANDROID §4.1).  Keep in sync with
     *  `retrostation.platform.android.display.logical_size`. */
    fun probe(mode: String): List<Pair<Int, Int>> {
        val dm = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager

        // Sizes come from the *activity's* display metrics, which follow the
        // window's rotation.  Display.getRealMetrics reports the panel's native
        // orientation instead, which produced canvases rotated against the
        // window (a landscape window got portrait-shaped canvases).
        val metrics = context.resources.displayMetrics
        val primary = metrics.widthPixels to metrics.heightPixels

        // Do not trust DISPLAY_CATEGORY_PRESENTATION: some vendors omit the marker
        // on a real second panel (§6.4.2).  Take every valid, non-default display.
        val secondary = dm.displays.filter {
            it.isValid && it.displayId != Display.DEFAULT_DISPLAY
        }
        if (secondary.isNotEmpty()) {
            val sizes = mutableListOf(logicalSize(primary))
            if (mode == "single") return sizes
            for (d in secondary) sizes += logicalSize(sizeOf(d))
            return sizes
        }

        // Single physical panel: portrait keeps the handheld's two stacked
        // canvases (content on top, detail/preview below); landscape is ONE
        // full-screen canvas, where the game page moves the detail into a
        // right-hand column instead.  The flip between one and two canvases is
        // why MainActivity recreates the activity on a real orientation change --
        // the running core commits to a canvas list at ``init_display``.
        // "single" / "dual" override the orientation.
        val (pw, ph) = primary
        val split = when (mode) {
            "single" -> false
            "dual" -> true
            else -> ph > pw
        }
        if (!split) return listOf(logicalSize(pw to ph))
        val topH = (ph * PORTRAIT_TOP_SHARE).toInt()
        return listOf(logicalSize(pw to topH), logicalSize(pw to (ph - topH)))
    }

    companion object {
        /** Share of the panel the content canvas takes in portrait; the rest is
         *  the detail canvas ("bottom screen").  MainActivity uses it for the
         *  view weights, so both sides agree without a round trip. */
        const val PORTRAIT_TOP_SHARE = 0.56
    }

    /** Mirror of `display.logical_size` (DESIGN.ANDROID §4.1).  1.5 MP budget,
     *  aligned to 4 px, clamped to [640, 2400].  Larger logical canvas shrinks the
     *  GPU upscale and kills "毛边", but every frame copies the whole canvas out of
     *  Python and into a bitmap, so this is the direct cost of a scroll frame --
     *  keep in sync with `display._TARGET_MP`. */
    internal fun logicalSize(physical: Pair<Int, Int>): Pair<Int, Int> {
        val (pw, ph) = physical
        val targetPx = 1.5 * 1_000_000.0
        val ratio = pw.toDouble() / ph
        val lh = kotlin.math.sqrt(targetPx / ratio)
        val lw = lh * ratio
        fun snap(v: Double): Int {
            val s = kotlin.math.round(v / 4.0) * 4.0
            return s.toInt().coerceIn(640, 2400)
        }
        return snap(lw) to snap(lh)
    }

    /** Attach a Presentation for each secondary display found by [probe]. */
    fun attachPresentations(
        displays: List<Pair<Int, Int>>,
        surfaces: MutableList<RetroSurfaceView>,
        onAttach: (RetroSurfaceView) -> Unit,
    ) {
        if (displays.size < 2) return
        val dm = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        val secondaries = dm.displays.filter {
            it.isValid && it.displayId != Display.DEFAULT_DISPLAY
        }
        for (disp in secondaries) {
            val view = RetroSurfaceView(context, surfaces.size)
            val presentation = object : Presentation(context, disp) {
                override fun onCreate(savedInstanceState: Bundle?) {
                    setContentView(view)
                }
            }
            // A tap on the secondary must not dismiss it (§6.4.3); keep it cancelable=false.
            presentation.setCanceledOnTouchOutside(false)
            presentation.show()
            surfaces += view
            onAttach(view)
        }
    }

    private fun sizeOf(display: Display): Pair<Int, Int> {
        val m = DisplayMetrics()
        display.getRealMetrics(m)
        return m.widthPixels to m.heightPixels
    }
}
