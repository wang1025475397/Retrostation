package ai.retrostation

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/**
 * Boots the Chaquopy Python runtime, wires the Kotlin bridges into a Python
 * [AndroidBridge], and runs the frontend's main loop on a dedicated thread.
 *
 * Threading model (DESIGN.ANDROID §6.2): the Python loop never runs on the Android
 * main thread -- doing so would ANR.  It polls input, advances the UI state machine,
 * draws into the off-screen canvases, and calls present(); the Kotlin side merely
 * uploads the resulting bitmaps and feeds input back.
 *
 * [HostBridge] is the Kotlin object the Python [AndroidBridge] calls into.
 */
class PyRuntime(private val context: Context) {

    lateinit var inputBridge: InputBridge
        private set

    // Shared with MainActivity: it adds the primary surface (index 0) before
    // start(); any secondary display is appended later (A6).  FrameBridge and
    // InputBridge hold this same reference so canvas indices stay consistent
    // (DESIGN.ANDROID §6.4.3).
    val surfaces = mutableListOf<RetroSurfaceView>()

    /** True once the core thread and bridges are up; input handlers guard on it. */
    val ready: Boolean
        get() = ::inputBridge.isInitialized

    private lateinit var frame: FrameBridge
    private lateinit var display: DisplayBridge
    private lateinit var host: HostBridge
    private var thread: Thread? = null

    fun start() {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context))
        }
        val py = Python.getInstance()

        // 1. Probe displays and build the bridges up front.  ``surfaces`` is the
        //    shared property (MainActivity adds index 0 before start()), so both
        //    bridges see the primary view from the first frame.
        display = DisplayBridge(context)
        val sizes = display.probe("auto")
        frame = FrameBridge(surfaces, sizes)
        inputBridge = InputBridge(surfaces, sizes)
        host = HostBridge(context, frame, inputBridge, display, sizes)

        // 2. Launch the frontend on a dedicated thread (DESIGN.ANDROID §6.2).  The
        // Python side wraps the Kotlin HostBridge in the Python AndroidBridge and runs
        // the shared boot order: platform -> config -> translator -> library -> UI.
        thread = Thread({
            py.getModule("retrostation.main")!!.callAttr("run_android", host)
        }, "retrostation-core").also { it.start() }
    }

    // Lifecycle calls can arrive before the deferred core boot finishes (e.g.
    // onResume right after onCreate); they are no-ops until ``ready``.
    fun suspend() { if (ready) host.suspendDisplay() }
    fun resume() { if (ready) host.resumeDisplay() }
    fun onGameExited() { if (ready) host.onGameExited() }
    fun stop() {
        if (ready) host.shutdown()
        thread?.interrupt()
        thread = null
    }

    // ----------------------------------------------------------------------- //
    // The Kotlin object the Python AndroidBridge talks to.
    // ----------------------------------------------------------------------- //
    class HostBridge(
        private val context: Context,
        private val frame: FrameBridge,
        private val input: InputBridge,
        private val display: DisplayBridge,
        private val sizes: List<Pair<Int, Int>>,
    ) {
        // Chaquopy does not auto-convert Java/Kotlin containers into Python
        // containers (iterating an ArrayList from Python raises TypeError), so
        // every structured result crosses the boundary as a JSON string and is
        // parsed with json.loads on the Python side (DESIGN.ANDROID §6.2).
        fun probeDisplays(mode: String): String {
            val arr = org.json.JSONArray()
            for ((w, h) in display.probe(mode)) arr.put(org.json.JSONArray(listOf(w, h)))
            return arr.toString()
        }
        fun pushFrame(index: Int, rgba: ByteArray) = frame.push(index, rgba)
        fun drainInput(timeout: Double): String {
            val arr = org.json.JSONArray()
            for (event in input.drainInput(timeout)) {
                val obj = org.json.JSONObject()
                for ((key, value) in event) obj.put(key, value)
                arr.put(obj)
            }
            return arr.toString()
        }

        // A5: wire BatteryManager here; null means "no reading", which the
        // status bar already renders as "level unknown" (same as a handheld
        // with no sysfs node).  Returning null at boot avoids a crash.
        fun battery(): Int? = null
        fun temperature(): Float? = null
        // A5: wire Settings.System here.  No-op until then so the brightness
        // setting screen does not throw on every apply.
        fun setBrightness(value: Int, index: Int): Unit = Unit

        fun romRoot(): String = "/storage/emulated/0/Roms" // refined by StorageBridge (A3)
        fun configDir(): String =
            context.getExternalFilesDir(null)?.absolutePath ?: context.filesDir.absolutePath

        fun listDir(path: String): String {
            // All-files mode: plain java.io.File listing (DESIGN.ANDROID §7.1).
            // JSON string across the Chaquopy boundary (see probeDisplays).
            val arr = org.json.JSONArray()
            val dir = File(path)
            if (dir.isDirectory) {
                for (f in dir.listFiles() ?: emptyArray()) {
                    val obj = org.json.JSONObject()
                    obj.put("name", f.name)
                    obj.put("is_dir", f.isDirectory)
                    obj.put("size", f.length())
                    obj.put("mtime", f.lastModified().toDouble())
                    arr.put(obj)
                }
            }
            return arr.toString()
        }

        fun startActivity(intent: Map<String, Any>): Unit = TODO("LaunchBridge (A4)")
        fun onGameExited() = Unit
        fun shutdown() = Unit
        fun suspendDisplay() = Unit
        fun resumeDisplay() = Unit
    }
}
