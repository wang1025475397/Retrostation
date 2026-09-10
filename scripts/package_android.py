#!/usr/bin/env python3
"""Build and package the Android APK.

Usage::

    python scripts/package_android.py                  # signed release APK
    python scripts/package_android.py --debug          # debug APK (debug key)
    python scripts/package_android.py --version-code 2 --version-name 0.2.0-android
    python scripts/package_android.py --list           # print the plan, build nothing

Produces ``dist/Retrostation-<versionName>.apk`` (arm64-v8a, minSdk 26).

Version: ``versionName`` defaults to the pyproject version
(``retrostation.__version__``) with an ``-android`` suffix.  ``versionCode``
defaults to the value baked into ``android/app/build.gradle.kts`` -- bump it
with ``--version-code`` whenever you ship an update, because Android refuses
to "downgrade" over an installed build with a higher code.

Signing: the first release build generates ``android/keystore/release.jks``
plus ``keystore.properties`` (both gitignored).  **Back them up**: an APK
signed with a different key can never update an installed one.  A release
build without a keystore falls back to the debug key (local installs only).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android"
DIST = ROOT / "dist"


def project_version() -> str:
    """The pyproject version: ``version = {attr = "retrostation.__version__"}``."""
    sys.path.insert(0, str(ROOT / "src"))
    from retrostation import __version__

    return __version__


def gradle_launcher() -> Path:
    """The wrapper script in android/, whichever platform flavour exists."""
    for name in ("gradlew.bat", "gradlew"):
        path = ANDROID / name
        if path.exists():
            return path
    raise SystemExit("no gradlew found under android/ -- restore the wrapper first")


def keytool() -> str:
    """The JDK's keytool, from JAVA_HOME or PATH."""
    home = os.environ.get("JAVA_HOME")
    if home:
        exe = "keytool.exe" if sys.platform == "win32" else "keytool"
        candidate = Path(home) / "bin" / exe
        if candidate.exists():
            return str(candidate)
    found = shutil.which("keytool")
    if found:
        return found
    raise SystemExit("keytool not found -- set JAVA_HOME to your JDK 17")


def ensure_keystore() -> None:
    """Generate android/keystore/{release.jks,keystore.properties} on first use."""
    props = ANDROID / "keystore" / "keystore.properties"
    jks = ANDROID / "keystore" / "release.jks"
    if props.exists() and jks.exists():
        return
    password = secrets.token_urlsafe(18)
    jks.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            keytool(), "-genkeypair", "-v",
            "-keystore", str(jks), "-alias", "retrostation",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-storepass", password, "-keypass", password,
            "-dname", "CN=Retrostation, OU=Retrostation, O=Retrostation, C=CN",
        ],
        check=True,
    )
    props.write_text(
        "storeFile=keystore/release.jks\n"
        f"storePassword={password}\n"
        "keyAlias=retrostation\n"
        f"keyPassword={password}\n",
        encoding="utf-8",
    )
    print(f"[pkg] generated {jks}")
    print("[pkg]   gitignored -- back it up before losing it, ever")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Retrostation Android APK")
    parser.add_argument("--debug", action="store_true",
                        help="build the debug APK (signed with the debug key)")
    parser.add_argument("--version-code", type=int, default=None,
                        help="versionCode (default: the one in build.gradle.kts)")
    parser.add_argument("--version-name", default=None,
                        help="versionName (default: pyproject version + '-android')")
    parser.add_argument("--list", action="store_true",
                        help="print the plan and exit without building")
    args = parser.parse_args()

    version = args.version_name or f"{project_version()}-android"
    variant = "debug" if args.debug else "release"
    apk_src = ANDROID / "app" / "build" / "outputs" / "apk" / variant / f"app-{variant}.apk"
    out = DIST / f"Retrostation-{version}.apk"

    print(f"[pkg] versionName={version}  variant={variant}")
    print(f"[pkg] output    ={out}")
    if args.list:
        return 0

    if variant == "release":
        ensure_keystore()

    gradle_props = [f"-PandroidVersionName={version}"]
    if args.version_code is not None:
        gradle_props.append(f"-PandroidVersionCode={args.version_code}")

    task = "assembleDebug" if args.debug else "assembleRelease"
    cmd = [str(gradle_launcher()), *gradle_props, task]
    print("[pkg] " + " ".join(cmd))
    subprocess.run(cmd, cwd=ANDROID, check=True)

    if not apk_src.exists():
        raise SystemExit(f"build finished but {apk_src} is missing")
    DIST.mkdir(parents=True, exist_ok=True)
    shutil.copy2(apk_src, out)
    print(f"[pkg] wrote {out} ({out.stat().st_size / 1_048_576:.1f} MiB)")
    print(f"[pkg] sha256 {sha256(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
