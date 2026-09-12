package ai.retrostation

import android.app.Activity
import android.content.res.Configuration
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import kotlin.math.roundToInt

/**
 * Host activity.  Owns the lifecycle, the (one or two) [RetroSurfaceView]s, and the
 * Python core thread (via [PyRuntime]).  Per DESIGN.ANDROID §6.3 the UI is drawn by
 * Python into off-screen canvases and pushed as bitmaps; this activity never draws
 * widgets itself.
 *
 * Screen shape: a phone gets **two stacked canvases in portrait** (content above,
 * detail below -- the handheld's dual-screen model, reused) and **one canvas in
 * landscape**.  [DisplayBridge.probe] decides; this class only arranges the views to
 * match, so the two can never disagree about how many canvases there are.
 *
 * Lifecycle maps straight onto the already-existing platform hooks:
 * onPause -> suspend_display, onResume -> resume_display, and a launched emulator
 * (or inline core) returning is signalled through onActivityResult -> onGameExited.
 */
class MainActivity : Activity() {

    private lateinit var py: PyRuntime
    /** Cached so [applySplit] can recompute logical canvas sizes without re-probing. */
    private val display = DisplayBridge(this)
    /** The stacked/side-by-side container; [applySplit] flips its orientation. */
    private lateinit var contentColumn: LinearLayout
    /** Orientation this instance was built for; see [onConfigurationChanged]. */
    private var orientation = Configuration.ORIENTATION_UNDEFINED
    /** Storage access as of the last resume; see [onResume]. */
    private var hadStorage = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // The frontend draws its own status strip, so drop the system bars.
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        orientation = resources.configuration.orientation

        py = PyRuntime(this)
        // Probe first: the view layout has to match the canvas count the Python
        // side will be told about.  One instance here, one in PyRuntime.start();
        // DisplayBridge is stateless, so both agree.
        val sizes = display.probe("auto")
        val root = buildLayout(sizes.size)
        py.rootView = root
        setContentView(root)

        // All-files access is the app's primary storage mode (§7.2).  Without it
        // the ROM scan sees an empty card, so ask for it once, after the first
        // frame (jumping to Settings during launch looks like a crash).
        hadStorage = storageGranted()
        if (!hadStorage) root.post { askStorageAccess() }

        // Bootstrap Python *after* the first frame.  The Chaquopy runtime start
        // blocks the main thread for seconds, and an onCreate that heavy stalls
        // the launch transition: the window surface is never shown and the screen
        // stays black until the task is re-opened (measured on an API 35
        // emulator).  Posting defers the boot until the activity is on screen.
        py.surfaces.first().post { py.start() }
    }

    /**
     * Build the view tree for [count] logical canvases and register the surfaces
     * with [PyRuntime] *before* the core starts (bridges hold the same list).
     *
     * Each surface consumes its own touches and hands them to the bridge with its
     * canvas index, so a tap on the lower "screen" is reported as ``screen=1`` --
     * which is what the shared session logic keys on.
     */
    private fun buildLayout(count: Int): android.view.ViewGroup {
        val views = (0 until count).map { RetroSurfaceView(this, it) }
        py.surfaces.addAll(views)
        for ((index, view) in views.withIndex()) {
            view.setOnTouchListener { _, event ->
                if (py.ready) py.inputBridge.offerTouch(event, index)
                true // consume: the Activity must not see the same tap again
            }
        }
        // Always a ViewGroup: the media layer adds/removes its surface inside it.
        // Stored as [contentColumn] so [applySplit] can flip it to side-by-side.
        contentColumn = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK) // the seam between the two canvases
        }
        if (views.size == 1) {
            contentColumn.addView(views[0], LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.MATCH_PARENT))
        } else {
            // Two canvases: stack them.  Weights come from the same constant
            // the probe split the pixels with, so the seam lands on the edge.
            // [applySplit] re-uses these weights for the horizontal arrangement.
            val topShare = DisplayBridge.PORTRAIT_TOP_SHARE.toFloat()
            contentColumn.addView(views[0], LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, topShare))
            contentColumn.addView(views[1], LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f - topShare))
        }
        // FrameLayout outside: the media layer positions its surface absolutely
        // inside it; a LinearLayout would re-place it and ignore x/y.
        return FrameLayout(this).apply {
            addView(contentColumn, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT))
        }
    }

    /**
     * Flip the two phone canvases between stacked (portrait / platform view) and
     * side-by-side (landscape + game detail) without restarting the core
     * (DESIGN §11.3).  Returns the new *logical* canvas sizes for each surface;
     * the Python side rebuilds its canvases/metrics from them.
     */
    fun applySplit(horizontal: Boolean): List<Pair<Int, Int>> {
        val share = DisplayBridge.PORTRAIT_TOP_SHARE
        // View mutation must run on the UI thread; the logical sizes are derived
        // from the panel dimensions + share, so they are ready immediately and do
        // not depend on the (async) layout pass the request triggers.
        runOnUiThread {
            // The core boots before the first layout in some cold starts; without
            // this guard a lateinit access would kill the core thread.
            if (!::contentColumn.isInitialized) return@runOnUiThread
            contentColumn.orientation =
                if (horizontal) LinearLayout.HORIZONTAL else LinearLayout.VERTICAL
            // buildLayout sized the children for a *column* (width MATCH_PARENT,
            // height 0 + weight).  Flipping to a row without rewriting them leaves
            // height=0, which collapses both canvases to nothing -- the screen goes
            // black.  In a row the weight sizes the WIDTH, so the two axes swap.
            if (contentColumn.childCount == 2) {
                val weights = listOf(share.toFloat(), 1f - share.toFloat())
                for (i in 0 until 2) {
                    val child = contentColumn.getChildAt(i)
                    val lp = (child.layoutParams as? LinearLayout.LayoutParams)
                        ?: LinearLayout.LayoutParams(0, 0)
                    if (horizontal) {
                        lp.width = 0
                        lp.height = LinearLayout.LayoutParams.MATCH_PARENT
                    } else {
                        lp.width = LinearLayout.LayoutParams.MATCH_PARENT
                        lp.height = 0
                    }
                    lp.weight = weights[i]
                    child.layoutParams = lp
                }
            }
            contentColumn.requestLayout()
            contentColumn.invalidate()
        }
        val metrics = resources.displayMetrics
        val pw = metrics.widthPixels
        val ph = metrics.heightPixels
        return if (horizontal) {
            listOf(
                display.logicalSize((pw * share).roundToInt() to ph),
                display.logicalSize((pw * (1f - share)).roundToInt() to ph),
            )
        } else {
            listOf(
                display.logicalSize(pw to (ph * share).roundToInt()),
                display.logicalSize(pw to (ph * (1f - share)).roundToInt()),
            )
        }
    }

    /**
     * Portrait <-> landscape changes the canvas count (1 <-> 2), which the running
     * Python core cannot renegotiate: it commits to a canvas list in ``init_display``.
     * Rebuilding the activity is the cleanest restart -- it stops the old core and
     * boots a fresh one against the new geometry, the activity-level equivalent of
     * the DESIGN.ANDROID §6.3 ``EXIT_RESTART_UI`` contract.
     */
    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        // Only a real orientation flip rebuilds.  Other changes (insets,
        // edge-to-edge padding) also arrive here, and rebuilding on those loops
        // forever: each relaunch delivers another one (measured on API 35).
        if (newConfig.orientation != orientation) {
            orientation = newConfig.orientation
            recreate()
        }
    }

    // Both the activity AND any attached Presentation feed the same queue
    // (DESIGN.ANDROID §6.4.3 pitfall 1: focus must not strand the gamepad).
    // Every handler guards on ``ready``: the core boots after the first frame,
    // so a key pressed during that window has no bridge to feed yet.
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        // Swallow every key once the core is up: only mapped keys become
        // semantic actions, and unmapped ones must never reach the system
        // default (some ROMs finish the activity on an unhandled key, which
        // looked like "any key quits").  Boot window still defers to the system.
        if (py.ready) {
            py.inputBridge.offerKey(keyCode, event)
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (py.ready) {
            py.inputBridge.offerKeyUp(keyCode, event)
            return true
        }
        return super.onKeyUp(keyCode, event)
    }

    override fun onGenericMotionEvent(event: MotionEvent?): Boolean {
        if (event != null && py.ready && py.inputBridge.offerAxis(event)) return true
        return super.onGenericMotionEvent(event)
    }

    /**
     * System back is the phone's "B".  The framework consumes KEYCODE_BACK
     * before onKeyDown, so the mapping has to live here -- otherwise a back
     * gesture drops the player straight to the launcher (DESIGN.ANDROID §10.2).
     */
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // Once the core is up the back key is the app's "B"; it must never finish
        // the activity.  A phone with no other exit path made any back press quit,
        // which read as "any key exits" (DESIGN.ANDROID §10.2).
        if (py.ready) {
            py.inputBridge.offerBack()
            return
        }
        super.onBackPressed()
    }

    override fun onPause() {
        super.onPause()
        py.suspend()
    }

    override fun onResume() {
        super.onResume()
        py.resume()
        // Back from Settings with access just granted: restart so the core
        // rescans the card (a fresh boot scans from scratch).
        if (!hadStorage && storageGranted()) {
            hadStorage = true
            recreate()
        }
    }

    /** All-files access on API 30+, plain read access below it. */
    private fun storageGranted(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            Environment.isExternalStorageManager()
        } else {
            checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED
        }

    /** Ask for storage access, on whichever page this API level uses (§7.2). */
    private fun askStorageAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startActivity(
                android.content.Intent(
                    Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:$packageName"),
                )
            )
        } else {
            requestPermissions(arrayOf(android.Manifest.permission.READ_EXTERNAL_STORAGE), 1)
        }
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
