package ai.retrostation

import android.app.Activity
import android.os.Bundle
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.WindowManager

/**
 * Host activity.  Owns the lifecycle, the (one or two) [RetroSurfaceView]s, and the
 * Python core thread (via [PyRuntime]).  Per DESIGN.ANDROID §6.3 the UI is drawn by
 * Python into off-screen canvases and pushed as bitmaps; this activity never draws
 * widgets itself.
 *
 * Lifecycle maps straight onto the already-existing platform hooks:
 * onPause -> suspend_display, onResume -> resume_display, and a launched emulator
 * (or inline core) returning is signalled through onActivityResult -> onGameExited.
 */
class MainActivity : Activity() {

    private lateinit var py: PyRuntime

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // The frontend draws its own status strip, so drop the system bars.
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        py = PyRuntime(this)
        // Add the primary surface (index 0) to the shared list BEFORE the core
        // starts, so FrameBridge/InputBridge render to it from frame one.
        val top = RetroSurfaceView(this, 0)
        py.surfaces.add(top)
        setContentView(top)
        // Bootstrap Python *after* the first frame.  The Chaquopy runtime start
        // blocks the main thread for seconds, and an onCreate that heavy stalls
        // the launch transition (measured on an API 35 emulator).  Posting
        // defers the boot until the activity is on screen.
        top.post { py.start() }
        // Bootstrap Python *after* the first frame.  The Chaquopy runtime start
        // blocks the main thread for seconds, and an onCreate that heavy stalls
        // the launch transition: the window surface is never shown and the screen
        // stays black until the task is re-opened (measured on an API 35
        // emulator).  Posting defers the boot until the activity is on screen.
        top.post { py.start() }
    }

    // Both the activity AND any attached Presentation feed the same queue
    // (DESIGN.ANDROID §6.4.3 pitfall 1: focus must not strand the gamepad).
    // Every handler guards on ``ready``: the core boots after the first frame,
    // so a key pressed during that window has no bridge to feed yet.
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (py.ready && py.inputBridge.offerKey(keyCode, event)) return true
        return super.onKeyDown(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (py.ready && py.inputBridge.offerKeyUp(keyCode, event)) return true
        return super.onKeyUp(keyCode, event)
    }

    override fun onGenericMotionEvent(event: MotionEvent?): Boolean {
        if (event != null && py.ready && py.inputBridge.offerAxis(event)) return true
        return super.onGenericMotionEvent(event)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (py.ready && py.inputBridge.offerTouch(event, screen = 0)) return true
        return super.onTouchEvent(event)
    }

    override fun onPause() {
        super.onPause()
        py.suspend()
    }

    override fun onResume() {
        super.onResume()
        py.resume()
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: android.content.Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        py.onGameExited() // a launched emulator or inline core returned
    }

    override fun onDestroy() {
        py.stop()
        super.onDestroy()
    }
}
