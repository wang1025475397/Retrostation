# Chaquopy reaches into Kotlin by name: the Python side calls
# ``self._kt.configDir()``, ``probeDisplays()`` and the rest of HostBridge, and
# R8 renaming them turns every one of those calls into an AttributeError at
# startup.  Keeping the whole bridge is the point -- it is the boundary, so
# nothing in it is dead code.
-keep class ai.retrostation.PyRuntime$HostBridge { *; }
-keepclassmembers class ai.retrostation.PyRuntime$HostBridge { *; }

# ExoPlayer / media3 is driven from Python through the same bridge; keep the
# classes the bridge hands back and forth.
-keep class ai.retrostation.MediaBridge { *; }
-keep class ai.retrostation.RetroSurfaceView { *; }
