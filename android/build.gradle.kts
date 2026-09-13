// Root project: declare plugin versions only.  Heavy lifting (Chaquopy
// sourceSets, abi filters, dependencies) lives in app/build.gradle.kts.
plugins {
    // AGP 8.11 supports compileSdk/targetSdk 36 (Android 16) and pairs with Gradle 8.11.
    id("com.android.application") version "8.11.0" apply false
    id("com.chaquo.python") version "17.0.0" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
