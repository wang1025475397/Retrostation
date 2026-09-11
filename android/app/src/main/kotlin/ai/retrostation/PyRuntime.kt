package ai.retrostation

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.math.max

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

    @Volatile private var booted = false
    @Volatile private var stopped = false

    lateinit var inputBridge: InputBridge
        private set

    // Shared with MainActivity: it adds the primary surface (index 0) before
    // start(); any secondary display is appended later (A6).  FrameBridge and
    // InputBridge hold this same reference so canvas indices stay consistent
    // (DESIGN.ANDROID §6.4.3).
    val surfaces = mutableListOf<RetroSurfaceView>()

    /** True once the core thread and bridges are up; input handlers guard on it. */
    val ready: Boolean
        get() = booted

    /** Set by MainActivity before start(): the view group holding the surfaces. */
    lateinit var rootView: android.view.ViewGroup

    private lateinit var frame: FrameBridge
    private lateinit var media: MediaBridge
    private lateinit var display: DisplayBridge
    private lateinit var host: HostBridge
    private var thread: Thread? = null

    /**
     * Boot the runtime and the frontend.
     *
     * Everything runs on a worker thread: ``Python.start`` unpacks the runtime
     * and blocks for seconds (measured >10 s cold on a loaded emulator), and on
     * the UI thread that is an ANR.  ``booted`` is volatile, so the UI sees
     * either "no bridge yet" or a fully built bridge pair -- never a half-made
     * one; the input handlers already guard on it.
     */
    fun start() {
        Thread({
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(context))
            }
            if (stopped) return@Thread
            val py = Python.getInstance()

            // Probe canvases and build the bridges.  ``surfaces`` was filled by
            // MainActivity before start() (Thread.start gives us the happens-before).
            display = DisplayBridge(context)
            val sizes = display.probe("auto")
            frame = FrameBridge(surfaces, sizes)
            media = MediaBridge(context, rootView, surfaces, sizes)
            inputBridge = InputBridge(surfaces, sizes)
            host = HostBridge(context, frame, inputBridge, display, sizes, media)
            booted = true
            if (stopped) {
                host.shutdown()
                return@Thread
            }

            // Launch the frontend on its own thread (DESIGN.ANDROID §6.2).  The
            // Python side wraps HostBridge in the Python AndroidBridge and runs the
            // shared boot order: platform -> config -> translator -> library -> UI.
            thread = Thread({
                py.getModule("retrostation.main")!!.callAttr("run_android", host)
            }, "retrostation-core").also { it.start() }
        }, "retrostation-boot").start()
    }

    // Lifecycle calls can arrive before the deferred core boot finishes (e.g.
    // onResume right after onCreate); they are no-ops until ``ready``.
    fun suspend() { if (ready) host.suspendDisplay() }
    fun resume() { if (ready) host.resumeDisplay() }
    fun onGameExited() { if (ready) host.onGameExited() }
    fun stop() {
        // Closing the bridges makes the core's blocked drain throw, so the Python
        // thread unwinds instead of spinning a frame loop against a torn-down
        // activity (a rotation rebuild tears this instance down).  ``stopped``
        // also covers the window where the boot thread is still unpacking the
        // runtime -- it will tear itself down instead of starting a core.
        stopped = true
        if (booted) {
            inputBridge.close()
            host.shutdown()
        }
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
        private val media: MediaBridge,
    ) {
        // -- video preview (DESIGN.ANDROID §9.2) ---------------------------- //

        /** Start the clip in the media box (canvas units) on canvas [index]. */
        fun openVideo(path: String, index: Int, x: Int, y: Int, w: Int, h: Int) =
            media.play(path, index, x, y, w, h)

        fun stopVideo() = media.stop()

        /** 0.0-1.0; the preview is muted unless the player turned sound on. */
        fun setVideoVolume(value: Double) = media.setVolume(value.toFloat())

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

        /**
         * Decode an image with Android's own decoder and hand it back as PNG
         * bytes.
         *
         * The bundled Pillow has no webp plugin (the Chaquopy wheel is built
         * without it), so every shipped platform background/logo and any webp
         * cover would fail to load.  BitmapFactory reads webp/gif/png/jpeg, and
         * PNG bytes are something PIL can always open -- at whatever size, so
         * the Python side keeps its own scaling/caching.
         */
        fun decodeImage(path: String): ByteArray? {
            if (!File(path).isFile) return null
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(path, bounds)
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

            // Cap the long edge: the UI never draws above ~2x its logical size,
            // and a full-size phone photo would cost tens of MB of bitmap.
            var sample = 1
            while (max(bounds.outWidth, bounds.outHeight) / sample > MAX_DECODE_EDGE) sample *= 2
            val bitmap = BitmapFactory.decodeFile(
                path, BitmapFactory.Options().apply { inSampleSize = sample }
            ) ?: return null
            return ByteArrayOutputStream().use { out ->
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
                out.toByteArray()
            }
        }
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

        /**
         * Fire a launcher intent (DESIGN.ANDROID §8.1).  Returns false when no app
         * can handle it, so the caller can say "install RetroArch" instead of
         * dropping the player out of the frontend.
         */
        fun startActivity(intent: String): Boolean {
            val spec = org.json.JSONObject(intent)
            val target = android.content.Intent(
                spec.optString("action", android.content.Intent.ACTION_MAIN)
            )
            spec.optString("data_uri").takeIf { it.isNotEmpty() }?.let {
                target.data = android.net.Uri.parse(it)
            }
            val pkg = spec.optString("package")
            spec.optString("activity").takeIf { it.isNotEmpty() }?.let {
                target.setClassName(pkg, it)
            } ?: pkg.takeIf { it.isNotEmpty() }?.let { target.setPackage(it) }
            spec.optString("mime").takeIf { it.isNotEmpty() }?.let { target.type = it }
            spec.optJSONArray("extras")?.let { extras ->
                for (i in 0 until extras.length()) {
                    val pair = extras.getJSONArray(i)
                    target.putExtra(pair.getString(0), pair.getString(1))
                }
            }
            return try {
                context.startActivity(target)
                true
            } catch (e: android.content.ActivityNotFoundException) {
                android.util.Log.w("RSK", "no activity for $target")
                false
            }
        }
        fun onGameExited() = Unit
        fun shutdown() {
            frame.close()
            input.close()
        }
        fun suspendDisplay() = Unit
        fun resumeDisplay() = Unit

        private companion object {
            /** Longest edge [decodeImage] will hand to Python. */
            const val MAX_DECODE_EDGE = 2048
        }
    }
}
