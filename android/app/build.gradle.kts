import java.util.Properties

plugins {
    id("com.android.application")
    id("com.chaquo.python")
    id("org.jetbrains.kotlin.android")
}

// Align Kotlin stdlib: an old transitive kotlin-stdlib-jdk7/jdk8 (1.6.21) conflicts
// with the kotlin-stdlib AGP ships (1.8.22), producing duplicate-class errors.
// Redirect the legacy jdk7/jdk8 artifacts onto the main stdlib (they were merged
// into kotlin-stdlib in 1.8) so only one copy resolves.
configurations.all {
    resolutionStrategy {
        // Align with the Kotlin Android plugin version (1.9.24) so compiler and
        // stdlib match; redirect the legacy jdk7/jdk8 artifacts (merged into the
        // main stdlib since 1.8) onto it to kill duplicate-class errors.
        force("org.jetbrains.kotlin:kotlin-stdlib:1.9.24")
        dependencySubstitution {
            substitute(module("org.jetbrains.kotlin:kotlin-stdlib-jdk7"))
                .using(module("org.jetbrains.kotlin:kotlin-stdlib:1.9.24"))
            substitute(module("org.jetbrains.kotlin:kotlin-stdlib-jdk8"))
                .using(module("org.jetbrains.kotlin:kotlin-stdlib:1.9.24"))
        }
    }
}

// Version and signing are driven by scripts/package_android.py.  The defaults
// keep a plain `gradlew assembleDebug` working without the packaging script.
val pkgVersionName = (project.findProperty("androidVersionName") as String?) ?: "0.1.0-android"
val pkgVersionCode = (project.findProperty("androidVersionCode") as String?)?.toInt() ?: 1

// Release signing material: android/keystore/keystore.properties is generated
// (and gitignored) by scripts/package_android.py on the first release build.
// Without it a release build falls back to the debug key -- fine for local
// installs, not for a store upload.
val keystorePropsFile = rootProject.file("keystore/keystore.properties")
val keystoreProps = Properties().apply {
    if (keystorePropsFile.exists()) keystorePropsFile.inputStream().use { load(it) }
}

android {
    namespace = "ai.retrostation"
    compileSdk = 36

    defaultConfig {
        applicationId = "ai.retrostation"
        minSdk = 26 // Android 8.0: covers virtually every Android handheld.
        targetSdk = 36
        versionCode = pkgVersionCode
        versionName = pkgVersionName

        ndk {
            // Ship only 64-bit: the 16 KB page devices are all arm64.
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        if (keystorePropsFile.exists()) {
            create("release") {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystorePropsFile.exists())
                signingConfigs.getByName("release")
            else
                signingConfigs.getByName("debug")
        }
    }

    // No view binding: the UI is entirely on the Python side (DESIGN.ANDROID §6.3).
    buildFeatures {
        viewBinding = false
    }

    // Match Java and Kotlin JVM targets (the host JDK is 17).
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

chaquopy {
    defaultConfig {
        // 3.13 matches the host interpreter used to build (buildPython below).
        version = "3.13"
        buildPython("C:/Users/37988/AppData/Local/Programs/Python/Python313/python.exe")
        pip {
            install("Pillow") // R1 rendering path.  Remove for R2 (Skia canvas).
        }
        // Make the retrostation package readable at runtime (bundled CJK font, etc.).
        extractPackages("retrostation")
    }

    // The Python core lives in <repo>/src and is shared verbatim with the Linux /
    // desktop platforms -- never copied into android/.  Resolve the path from
    // rootProject so it stays correct regardless of checkout location.
    sourceSets {
        getByName("main") {
            srcDir(rootProject.rootDir.parentFile.resolve("src"))
        }
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.core:core-ktx:1.13.1")
    // Video preview: ExoPlayer renders into its own surface under the UI frame.
    implementation("androidx.media3:media3-exoplayer:1.4.1")
}
