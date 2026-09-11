package ai.retrostation

import android.content.Context
import android.net.Uri
import android.view.SurfaceView
import android.view.ViewGroup
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import java.io.File

/**
 * Video preview composited by the platform, under the UI frame
 * (DESIGN.ANDROID §9.2).
 *
 * Python tells us the media box in canvas units; we put a [SurfaceView] over
 * the matching surface at that spot and let ExoPlayer render into it.  Decoded
 * frames never travel through the CPU or the bitmap upload path, and Python's
 * `read_frame()` stays `None` for the whole clip.
 *
 * Every entry point posts to the view's handler: ExoPlayer must be driven from
 * the main thread, and these come in from the Python core thread.
 */
class MediaBridge(
    private val context: Context,
    private val root: ViewGroup,
    private val surfaces: List<RetroSurfaceView>,
    private val logical: List<Pair<Int, Int>>,
) {
    private var player: ExoPlayer? = null
    private var view: SurfaceView? = null
    private var volume = 0f

    /** Start [path] in the media box (canvas units) on canvas [index]. */
    fun play(path: String, index: Int, x: Int, y: Int, w: Int, h: Int) {
        root.post {
            release()
            if (index !in surfaces.indices || w <= 0 || h <= 0) return@post
            val host = surfaces[index]
            val (lw, lh) = logical[index]
            val sx = host.width.toFloat().coerceAtLeast(1f) / lw
            val sy = host.height.toFloat().coerceAtLeast(1f) / lh

            val surface = SurfaceView(context)
            // The frontend paints opaque bitmaps over the whole window, and a
            // SurfaceView's surface normally sits *behind* the window content,
            // so the clip was decoded but never seen.  On top is safe here: the
            // media box is reserved for the clip.
            surface.setZOrderOnTop(true)
            root.addView(
                surface,
                ViewGroup.LayoutParams((w * sx).toInt(), (h * sy).toInt()),
            )
            surface.x = host.left + x * sx
            surface.y = host.top + y * sy

            val exo = ExoPlayer.Builder(context).build()
            exo.setVideoSurfaceView(surface)
            exo.volume = volume
            exo.repeatMode = Player.REPEAT_MODE_ALL
            exo.setMediaItem(MediaItem.fromUri(Uri.fromFile(File(path))))
            exo.prepare()
            exo.playWhenReady = true
            player = exo
            view = surface
        }
    }

    fun move(index: Int, x: Int, y: Int, w: Int, h: Int) {
        root.post { place(surfaces.getOrNull(index) ?: return@post, index, x, y, w, h) }
    }

    private fun place(host: RetroSurfaceView, index: Int, x: Int, y: Int, w: Int, h: Int) {
        val surface = view ?: return
        val (lw, _) = logical[index]
        val sc = host.width.toFloat().coerceAtLeast(1f) / lw
        // Mutate the existing params rather than replacing them: the parent is
        // a FrameLayout, and handing it a plain LayoutParams makes its next
        // measure pass cast it to MarginLayoutParams and crash.
        // Mutate the existing params in place: the parent is a FrameLayout, and
        // handing it a plain LayoutParams makes its next measure pass cast it to
        // MarginLayoutParams and crash.  Positioning by x/y (rather than by
        // calling layout() here) is what survives the parent's own layout pass.
        val lp = surface.layoutParams
        lp.width = (w * sc).toInt()
        lp.height = (h * sc).toInt()
        surface.layoutParams = lp
        surface.x = host.left + x * sc
        surface.y = host.top + y * sc
    }

    fun setVolume(value: Float) {
        volume = value.coerceIn(0f, 1f)
        root.post { player?.volume = volume }
    }

    fun stop() = root.post { release() }

    private fun release() {
        player?.release()
        player = null
        view?.let { root.removeView(it) }
        view = null
    }
}
