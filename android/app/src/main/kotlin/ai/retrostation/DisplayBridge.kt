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

    /** @return one (w,h) pair per usable display, in probe order (main first).
     *  Sizes are *logical* -- the Python side paints to a logical canvas and the GPU
     *  upscales (DESIGN.ANDROID §4.1).  This must stay in sync with
     *  `retrostation.platform.android.display.logical_size`. */
    fun probe(mode: String): List<Pair<Int, Int>> {
        val dm = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        val main = dm.getDisplay(Display.DEFAULT_DISPLAY)
        val sizes = mutableListOf(logicalSize(sizeOf(main)))

        if (mode == "single") return sizes.take(1)

        // Do not trust DISPLAY_CATEGORY_PRESENTATION: some vendors omit the marker
        // on a real second panel (§6.4.2).  Take every valid, non-default display.
        val secondary = dm.displays.filter {
            it.isValid && it.displayId != Display.DEFAULT_DISPLAY
        }
        if (mode == "dual" && secondary.isEmpty()) {
            // Fail soft: the frontend still runs single-screen (§6.4.2 rule).
            return sizes
        }
        for (d in secondary) sizes += logicalSize(sizeOf(d))
        return sizes
    }

    /** Mirror of `display.logical_size` (DESIGN.ANDROID §4.1).  0.7 MP budget,
     *  aligned to 4 px, clamped to [480, 1280].  Keep in sync with the Python side. */
    private fun logicalSize(physical: Pair<Int, Int>): Pair<Int, Int> {
        val (pw, ph) = physical
        val targetPx = 0.7 * 1_000_000.0
        val ratio = pw.toDouble() / ph
        val lh = kotlin.math.sqrt(targetPx / ratio)
        val lw = lh * ratio
        fun snap(v: Double): Int {
            val s = kotlin.math.round(v / 4.0) * 4.0
            return s.toInt().coerceIn(480, 1280)
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
