pluginManagement {
    repositories {
        google()
        mavenCentral()
        // Chaquopy's Gradle plugin is published on the Gradle Plugin Portal.
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // LibretroDroid (B series inline emulator) is on JitPack.
        maven("https://jitpack.io")
    }
}

rootProject.name = "Retrostation"
include(":app")
