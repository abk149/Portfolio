"""Build identity. Overwritten by Gradle's syncPythonSource for the APK.

The checked-in values are what you get running from source; the Android build
replaces this file with the git SHA and timestamp of the build, so the running
app can say exactly which code it is executing.
"""
BUILD_ID = "source"
BUILT_AT = "running from working tree"
