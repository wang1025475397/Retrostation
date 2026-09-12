package ai.retrostation

import android.view.KeyEvent
import android.view.MotionEvent
import android.view.VelocityTracker
import com.chaquo.python.Python
import java.util.concurrent.LinkedBlockingQueue
import kotlin.math.abs

/**
 * Turns Android input into the Python-side semantic queue.  Key codes are mapped
 * through the *Python* keymap ([retrostation.platform.android.input.keymap_action_name])
 * so the single source of truth stays in Python (DESIGN.ANDROID §10.2).  Touch is
 * recognised here into the semantic gestures the session understands -- TAP, DRAG
 * and FLING (§10.3) -- carrying *logical* canvas coordinates.
 *
 * Latency: [drainInput] blocks on a monitor rather than sleeping, so a touch
 * wakes the Python loop immediately instead of waiting out its poll slice.  That
 * is the difference between "the tap lands this frame" and "…the next one".
 */
class InputBridge(
    private val surfaces: List<RetroSurfaceView>,
    private val logical: List<Pair<Int, Int>>,
) {
    private val queue: LinkedBlockingQueue<Map<String, Any>> = LinkedBlockingQueue()

    /** Guards [queue] waits; every producer notifies so a blocked drain wakes up. */
    private val lock = Object()

    /** Set by [close] when the host is tearing the frontend down (rotation rebuild). */
    @Volatile private var closed = false

    private val py = Python.getInstance()

    // -- gesture state -------------------------------------------------------- //

    private var downX = 0f
    private var downY = 0f
    private var downAt = 0L
    private var lastX = 0f
    private var lastY = 0f
    private var dragging = false
    private var tracker: VelocityTracker? = null

    private companion object {
        /** Finger jitter below this stays a tap (physical px).  Raised from 24
         *  so a tap on the on-screen d-pad does not turn into a scroll. */
        const val TAP_SLOP_PX = 36f
        /** A press held longer than this is not a tap (long-press menu arrives later). */
        const val TAP_MAX_MS = 600L
        /** Vertical speed above which releasing becomes a fling (px/s). */
        const val FLING_MIN_VX = 900f
        /** How much of a fling's inertia is handed to Python as one scroll (seconds). */
        const val FLING_SECONDS = 0.10f
    }

    // -- buttons -------------------------------------------------------------- //

    fun offerKey(keyCode: Int, event: KeyEvent): Boolean {
        val action = mapAction(keyCode) ?: return false
        emit("press", action)
        return true
    }

    fun offerKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        val action = mapAction(keyCode) ?: return false
        emit("release", action)
        return true
    }

    /**
     * The system back gesture, as the session's B action.
     *
     * The framework consumes KEYCODE_BACK before [MainActivity.onKeyDown] sees
     * it, so the mapping cannot live in the keymap; the activity forwards it
     * here instead (DESIGN.ANDROID §10.2 pitfall).
     */
    fun offerBack(): Boolean {
        emit("press", "b")
        return true
    }

    /** DPAD / joystick axes -> directional press/release with edge detection. */
    fun offerAxis(event: MotionEvent): Boolean {
        val axisX = event.getAxisValue(MotionEvent.AXIS_HAT_X)
        val axisY = event.getAxisValue(MotionEvent.AXIS_HAT_Y)
        // TODO(A2): dead-zone (0.5) + edge detection so a held direction fires once.
        if (axisX > 0.5) emit("press", "right") else if (axisX < -0.5) emit("press", "left")
        if (axisY > 0.5) emit("press", "down") else if (axisY < -0.5) emit("press", "up")
        return axisX != 0f || axisY != 0f
    }

    // -- touch ---------------------------------------------------------------- //

    /**
     * Recognise one [MotionEvent] (a full down/move/up sequence) into semantic
     * events.  Drags are emitted as *increments* in logical units, so the session
     * can accumulate them without knowing anything about the physical panel;
     * a short release with no movement stays a tap.
     */
    fun offerTouch(event: MotionEvent, screen: Int): Boolean {
        if (closed || screen !in surfaces.indices) return true
        val (lw, lh) = logical[screen]
        val view = surfaces[screen]
        val sx = view.width.toFloat().coerceAtLeast(1f) / lw
        val sy = view.height.toFloat().coerceAtLeast(1f) / lh

        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.x; downY = event.y
                lastX = event.x; lastY = event.y
                downAt = event.eventTime
                dragging = false
                tracker?.recycle()
                tracker = VelocityTracker.obtain().apply { addMovement(event) }
                // The pad has to know a finger is *on* a button: a press held
                // past the tap window used to emit nothing at all, so holding a
                // direction did nothing at all.
                emitTouch("touch_down", screen,
                    x = (event.x / sx).toInt(), y = (event.y / sy).toInt())
            }

            MotionEvent.ACTION_MOVE -> {
                tracker?.addMovement(event)
                if (!dragging &&
                    (abs(event.x - downX) > TAP_SLOP_PX || abs(event.y - downY) > TAP_SLOP_PX)) {
                    dragging = true
                }
                if (dragging) {
                    val dx = ((event.x - lastX) / sx).toInt()
                    val dy = ((event.y - lastY) / sy).toInt()
                    if (dx != 0 || dy != 0) emitGesture("drag", screen, dx = dx, dy = dy)
                }
                lastX = event.x; lastY = event.y
            }

            MotionEvent.ACTION_UP -> {
                // Release first: it stops the pad repeating, and the tap that may
                // follow is the one that press already accounted for.
                emitTouch("touch_up", screen,
                    x = (event.x / sx).toInt(), y = (event.y / sy).toInt(),
                    kind = "release")
                if (!dragging && event.eventTime - downAt <= TAP_MAX_MS) {
                    emitTouch("tap", screen,
                        x = (event.x / sx).toInt(), y = (event.y / sy).toInt())
                } else if (dragging) {
                    trackVelocity(event)?.let { (vx, vy) ->
                        // Whichever axis the finger was actually travelling
                        // along.  The platform row and the game carousel are
                        // rows of cards, so their swipe is sideways -- measuring
                        // only yVelocity made those a no-op.
                        if (abs(vx) > abs(vy)) {
                            if (abs(vx) > FLING_MIN_VX) {
                                emitGesture("fling", screen,
                                    dx = (vx / 1000f * FLING_SECONDS / sx).toInt())
                            }
                        } else if (abs(vy) > FLING_MIN_VX) {
                            emitGesture("fling", screen,
                                dy = (vy / 1000f * FLING_SECONDS / sy).toInt())
                        }
                    }
                }
                dragging = false
                tracker?.recycle(); tracker = null
            }

            MotionEvent.ACTION_CANCEL -> {
                dragging = false
                tracker?.recycle(); tracker = null
            }
        }
        return true
    }

    /** The finger's velocity on both axes, in physical px per second. */
    private fun trackVelocity(event: MotionEvent): Pair<Float, Float>? {
        val t = tracker ?: return null
        t.addMovement(event)
        t.computeCurrentVelocity(1000) // px per second
        return t.xVelocity to t.yVelocity
    }

    // -- drain ---------------------------------------------------------------- //

    /** Called by the Python bridge; blocks until input arrives or the slice ends. */
    fun drainInput(timeoutSec: Double): List<Map<String, Any>> {
        if (closed) throw IllegalStateException("frontend stopped")
        val out = mutableListOf<Map<String, Any>>()
        synchronized(lock) {
            queue.drainTo(out)
            if (out.isEmpty() && timeoutSec > 0) {
                try {
                    // Notify-based wait: a touch wakes us at once instead of
                    // sleeping out the whole poll slice.
                    lock.wait((timeoutSec * 1000).toLong().coerceAtLeast(1))
                } catch (e: InterruptedException) {
                    Thread.currentThread().interrupt()
                }
                queue.drainTo(out)
            }
        }
        return out
    }

    /** Stop the core: the next drain throws, unwinding the Python frame loop. */
    fun close() {
        closed = true
        synchronized(lock) { lock.notifyAll() }
    }

    // -- helpers -------------------------------------------------------------- //

    private fun emit(kind: String, action: String) {
        push(mapOf("action" to action, "kind" to kind))
    }

    private fun emitTouch(action: String, screen: Int, x: Int, y: Int,
                          kind: String = "press") {
        push(mapOf(
            "action" to action, "kind" to kind,
            "screen" to screen, "x" to x, "y" to y,
        ))
    }

    private fun emitGesture(action: String, screen: Int, dx: Int = 0, dy: Int = 0) {
        push(mapOf(
            "action" to action, "kind" to "press",
            "screen" to screen, "dx" to dx, "dy" to dy,
        ))
    }

    private fun push(event: Map<String, Any>) {
        synchronized(lock) {
            queue.add(event)
            lock.notifyAll()
        }
    }

    private fun mapAction(keyCode: Int): String? {
        // chaquopy's PyObject has no isNone(); str(None) == "None" is the reliable test.
        // keymap_action_name hands back the action *value* ("b"), not the enum repr.
        val res = py.getModule("retrostation.platform.android.input")
            .callAttr("keymap_action_name", keyCode)
        val s = res.toString()
        return if (s == "None") null else s
    }
}
