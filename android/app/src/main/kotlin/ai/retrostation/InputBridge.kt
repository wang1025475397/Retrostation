package ai.retrostation

import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import com.chaquo.python.Python
import java.util.concurrent.LinkedBlockingQueue

/**
 * Turns Android input into the Python-side semantic queue.  Key codes are mapped
 * through the *Python* keymap ([retrostation.platform.android.input.keymap_action])
 * so the single source of truth stays in Python (DESIGN.ANDROID §10.2).  Touch
 * gestures (TAP/DRAG/FLING) are produced here from MotionEvent and carry logical
 * coordinates -- the platform maps physical pixels into the canvas space.
 *
 * Note: auto-repeat and long-press are synthesised on the Python side (mirroring the
 * desktop platform), so this bridge only ever forwards press/release/gesture events.
 */
class InputBridge(
    private val surfaces: List<RetroSurfaceView>,
    private val logical: List<Pair<Int, Int>>,
) {
    private val queue: LinkedBlockingQueue<Map<String, Any>> = LinkedBlockingQueue()
    private val py = Python.getInstance()

    // -- buttons ------------------------------------------------------------ //

    fun offerKey(keyCode: Int, event: KeyEvent): Boolean {
        val action = mapAction(keyCode) ?: return false
        queue.add(mapOf("action" to action, "kind" to "press"))
        return true
    }

    fun offerKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        val action = mapAction(keyCode) ?: return false
        queue.add(mapOf("action" to action, "kind" to "release"))
        return true
    }

    /** DPAD / joystick axes -> directional press/release with edge detection. */
    fun offerAxis(event: MotionEvent): Boolean {
        val axisX = event.getAxisValue(MotionEvent.AXIS_HAT_X)
        val axisY = event.getAxisValue(MotionEvent.AXIS_HAT_Y)
        // TODO(A2): dead-zone (0.5) + edge detection so a held direction fires once.
        if (axisX > 0.5) queue.add(dir("right")) else if (axisX < -0.5) queue.add(dir("left"))
        if (axisY > 0.5) queue.add(dir("down")) else if (axisY < -0.5) queue.add(dir("up"))
        return axisX != 0f || axisY != 0f
    }

    // -- touch -------------------------------------------------------------- //

    fun offerTouch(event: MotionEvent, screen: Int): Boolean {
        if (screen !in surfaces.indices) return false
        val (lw, lh) = logical[screen]
        val sx = surfaces[screen].width.toFloat().coerceAtLeast(1f) / lw
        val sy = surfaces[screen].height.toFloat().coerceAtLeast(1f) / lh
        val x = (event.x / sx).toInt()
        val y = (event.y / sy).toInt()
        // TODO(A6): DRAG/FLING from a GestureDetector; for now a tap.
        queue.add(
            mapOf(
                "action" to "tap",
                "kind" to "press",
                "x" to x,
                "y" to y,
                "screen" to screen,
            ),
        )
        return true
    }

    // -- drain -------------------------------------------------------------- //

    /** Called by [AndroidBridge.drain_events]; returns whatever is queued. */
    fun drainInput(timeoutSec: Double): List<Map<String, Any>> {
        val out = mutableListOf<Map<String, Any>>()
        queue.drainTo(out)
        if (out.isEmpty() && timeoutSec > 0) {
            Thread.sleep((timeoutSec * 1000).toLong().coerceAtMost(50))
            queue.drainTo(out)
        }
        return out
    }

    // -- helpers ------------------------------------------------------------ //

    private fun dir(name: String) = mapOf("action" to name, "kind" to "press")

    private fun mapAction(keyCode: Int): String? {
        // chaquopy's PyObject has no isNone(); str(None) == "None" is the reliable test.
        // keymap_action_name hands back the action *value* ("b"), not the enum repr.
        val res = py.getModule("retrostation.platform.android.input")
            .callAttr("keymap_action_name", keyCode)
        val s = res.toString()
        return if (s == "None") null else s
    }
}
