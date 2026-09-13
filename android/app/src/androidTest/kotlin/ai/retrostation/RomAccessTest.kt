package ai.retrostation

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Turning a ROM path into the storage provider's `content://` URI (§8.3).
 *
 * This is the shape DraStic is handed: a plain `file://` path and this app's own
 * FileProvider URI both leave it on its own main menu, so the URI has to be the
 * *system provider's*, under the folder the player authorised.  The paths that
 * must **not** produce a URI matter just as much -- a wrong one is a ROM the
 * emulator silently fails to open.
 *
 * The grant itself is real here (the tree URI is remembered exactly the way the
 * folder picker remembers it); the provider grants no read permission for a
 * synthetic tree, which only affects *opening* the ROM, not mapping the path.
 */
@RunWith(AndroidJUnit4::class)
class RomAccessTest {

    private val context: Context
        get() = InstrumentationRegistry.getInstrumentation().targetContext

    /** The tree URI the folder picker returns for /storage/emulated/0/Roms. */
    private val tree: Uri = Uri.parse(
        "content://com.android.externalstorage.documents/tree/primary%3ARoms"
    )

    @Before
    fun clear() {
        prefs().edit().clear().commit()
    }

    @After
    fun tidy() {
        prefs().edit().clear().commit()
    }

    @Test
    fun withoutAnAuthorisedFolderNothingIsHandedOver() {
        assertFalse(RomAccess.granted(context))
        assertNull(RomAccess.documentUri(context, "/storage/emulated/0/Roms/NDS/x.zip"))
    }

    @Test
    fun aRomUnderTheFolderBecomesTheProvidersUri() {
        RomAccess.remember(context, tree)
        assertTrue(RomAccess.granted(context))

        val uri = RomAccess.documentUri(
            context, "/storage/emulated/0/Roms/NDS/007 - 血石.zip"
        )

        assertNotNull(uri)
        assertEquals("content", uri!!.scheme)
        // The document id keeps the provider's own spelling, <volume>:<relative>,
        // which is what the emulator opens.
        assertEquals(
            "primary:Roms/NDS/007 - 血石.zip",
            DocumentsContract.getDocumentId(uri),
        )
    }

    @Test
    fun theAuthorisedFolderItselfMaps() {
        RomAccess.remember(context, tree)
        val uri = RomAccess.documentUri(context, "/storage/emulated/0/Roms")
        assertEquals("primary:Roms", DocumentsContract.getDocumentId(uri!!))
    }

    @Test
    fun aRomOutsideTheFolderIsRefused() {
        RomAccess.remember(context, tree)
        // A different top-level folder...
        assertNull(RomAccess.documentUri(context, "/storage/emulated/0/Games/x.zip"))
        // ...and the same folder on another volume (an SD card).
        assertNull(RomAccess.documentUri(context, "/storage/1A2B-3C4D/Roms/x.zip"))
    }

    @Test
    fun aForgottenGrantLeavesNothingBehind() {
        RomAccess.remember(context, tree)
        prefs().edit().clear().commit()
        assertFalse(RomAccess.granted(context))
        assertNull(RomAccess.documentUri(context, "/storage/emulated/0/Roms/NDS/x.zip"))
    }

    /** `RomAccess` keeps its grant here; tests start and end with it empty. */
    private fun prefs() = context.getSharedPreferences("rom_access", Context.MODE_PRIVATE)
}
