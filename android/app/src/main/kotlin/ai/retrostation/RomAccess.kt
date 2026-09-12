package ai.retrostation

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract

/**
 * The ROM folder the player authorised, and the document URIs built from it.
 *
 * Some emulators refuse a plain path: handed a ``file://`` path or another app's
 * FileProvider URI, DraStic answers on its own main menu ("Unable to open game
 * from ...").  What it takes is the *system storage provider's* ``content://``
 * URI -- exactly what a file manager passes on "open with" -- and Android only
 * lets an app grant a URI it holds itself (``SecurityException``: "you could
 * obtain access using ACTION_OPEN_DOCUMENT").  So the folder is picked once by
 * the player ([MainActivity.pickRomTree]), the grant is persisted here, and every
 * later launch rewrites a ROM path into a document URI under that tree
 * (DESIGN.ANDROID §8.3).
 */
object RomAccess {

    private const val PREFS = "rom_access"
    private const val KEY_TREE = "tree"
    private const val KEY_ASKED = "asked"

    /** Flags a picked tree has to be taken with to survive a reboot. */
    const val PERSIST_FLAGS =
        Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION

    /** The authorised folder, or ``null`` when the player has not picked one. */
    fun tree(context: Context): Uri? =
        prefs(context).getString(KEY_TREE, null)?.let { Uri.parse(it) }

    /** Whether a folder has been authorised (the launch path gates on this). */
    fun granted(context: Context): Boolean = tree(context) != null

    /**
     * Whether the player has already been asked once.
     *
     * The picker is offered on the first boot and then left alone: a player who
     * backs out should not be asked again on every start, and both the settings
     * row and the launch prompt still lead back to it.
     */
    fun askedBefore(context: Context): Boolean = prefs(context).getBoolean(KEY_ASKED, false)

    /** Remember that the picker has been offered (see [askedBefore]). */
    fun markAsked(context: Context) {
        prefs(context).edit().putBoolean(KEY_ASKED, true).apply()
    }

    /** A readable form of the authorised folder, for the settings row. */
    fun label(context: Context): String {
        val tree = tree(context) ?: return ""
        val id = try {
            DocumentsContract.getTreeDocumentId(tree)
        } catch (e: IllegalArgumentException) {
            return ""
        }
        // "primary:Roms" -> /storage/emulated/0/Roms, "1A2B-3C4D:X" -> /storage/1A2B-3C4D/X
        val parts = id.split(':', limit = 2)
        if (parts.size != 2) return id
        val volume = if (parts[0] == "primary") "emulated/0" else parts[0]
        return "/storage/$volume/${parts[1]}".trimEnd('/')
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Remember the folder the player picked, keeping the grant across reboots. */
    fun remember(context: Context, uri: Uri?) {
        if (uri == null) return
        try {
            context.contentResolver.takePersistableUriPermission(uri, PERSIST_FLAGS)
        } catch (e: SecurityException) {
            android.util.Log.w("RSK", "tree grant is not persistable (${e.message})")
        }
        prefs(context).edit().putString(KEY_TREE, uri.toString()).apply()
    }

    /**
     * ``path`` as a document URI under the authorised folder.
     *
     * ``null`` when nothing was authorised or the file sits outside the tree --
     * the caller reports that instead of handing an unusable URI over.
     */
    fun documentUri(context: Context, path: String): Uri? {
        val tree = tree(context) ?: return null
        val treeId = try {
            DocumentsContract.getTreeDocumentId(tree)
        } catch (e: IllegalArgumentException) {
            android.util.Log.w("RSK", "stored tree URI is unusable (${e.message})")
            return null
        }
        val split = splitVolumeAndRelative(path) ?: return null
        val (volume, relative) = split
        val prefix = "$volume:"
        if (!treeId.startsWith(prefix)) return null
        val inside = treeId.removePrefix(prefix).trimEnd('/')
        if (inside.isNotEmpty() && relative != inside && !relative.startsWith("$inside/")) {
            return null
        }
        val rest = relative.removePrefix(inside).trimStart('/')
        return DocumentsContract.buildDocumentUriUsingTree(tree, "$treeId/$rest")
    }

    /**
     * ``/storage/emulated/0/Roms/x.zip`` -> ``primary`` to ``Roms/x.zip``.
     *
     * The storage provider spells the primary volume ``primary`` and any other
     * (an SD card) with its mount UUID, which is what the path already carries.
     */
    private fun splitVolumeAndRelative(path: String): Pair<String, String>? {
        val root = "/storage/"
        if (!path.startsWith(root)) return null
        val rest = path.removePrefix(root)
        val slash = rest.indexOf('/')
        if (slash <= 0) return null
        val volume = rest.substring(0, slash)
        val relative = rest.substring(slash + 1)
        return if (volume == "emulated") "primary" to relative.removePrefix("0/")
        else volume to relative
    }
}
