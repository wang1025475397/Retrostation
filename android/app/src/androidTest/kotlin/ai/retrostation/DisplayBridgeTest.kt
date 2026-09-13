package ai.retrostation

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The Kotlin half of the logical-canvas maths (DESIGN.ANDROID §4.1).
 *
 * [DisplayBridge.logicalSize] mirrors `retrostation.platform.android.display.
 * logical_size`: Python paints to the logical canvas it computes, Kotlin sizes the
 * view from its own copy of the same formula.  The two drifting apart shows up on
 * a device as a stretched or rotated frame, so the agreed numbers are pinned here
 * as well as in `tests/test_android_display.py`.
 */
@RunWith(AndroidJUnit4::class)
class DisplayBridgeTest {

    private val bridge: DisplayBridge
        get() = DisplayBridge(InstrumentationRegistry.getInstrumentation().targetContext)

    @Test
    fun logicalSizeMatchesThePythonContract() {
        assertEquals(820 to 1824, bridge.logicalSize(1080 to 2400))
        assertEquals(1632 to 920, bridge.logicalSize(1920 to 1080))
    }

    @Test
    fun logicalSizeClampsAndAligns() {
        for (physical in listOf(100 to 10000, 4000 to 8000, 200 to 200, 2400 to 1080)) {
            val (lw, lh) = bridge.logicalSize(physical)
            assertEquals("$lw is not 4-aligned", 0, lw % 4)
            assertEquals("$lh is not 4-aligned", 0, lh % 4)
            assertTrue("$lw outside the bounds", lw in 640..2400)
            assertTrue("$lh outside the bounds", lh in 640..2400)
        }
    }

    @Test
    fun anExtremeRatioClampsRatherThanCollapsing() {
        assertEquals(640 to 2400, bridge.logicalSize(100 to 10000))
    }

    @Test
    fun probeReportsUsableCanvases() {
        for (mode in listOf("auto", "single", "dual")) {
            val sizes = bridge.probe(mode)
            assertTrue("$mode reported no canvas at all", sizes.isNotEmpty())
            for ((w, h) in sizes) {
                assertTrue("$mode reported a degenerate canvas ${w}x$h", w > 0 && h > 0)
                assertTrue("$mode reported an unaligned canvas ${w}x$h", w % 4 == 0 && h % 4 == 0)
            }
        }

        // "single" is the override that keeps a phone to one canvas whatever the
        // panel splits into -- the value the settings dialog offers.
        assertEquals(1, bridge.probe("single").size)
    }
}
