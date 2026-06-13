#!/bin/bash
set -e

# build_llama_server.sh
# This script cross-compiles llama.cpp for Android arm64-v8a and copies the binary to the assets folder.

# Use specific Android SDK paths
NDK="/Users/abheekbhowmik/Library/Android/sdk/ndk/30.0.14904198"
CMAKE="/Users/abheekbhowmik/Library/Android/sdk/cmake/4.1.2/bin/cmake"

if [ ! -d "$NDK" ]; then
    echo "ERROR: Android NDK not found at $NDK"
    exit 1
fi

if [ ! -x "$CMAKE" ]; then
    echo "ERROR: CMake not found at $CMAKE"
    exit 1
fi

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$WORKSPACE_DIR/llama.cpp_build"
TARGET_ASSETS_DIR="$WORKSPACE_DIR/android/app/src/main/assets"

echo "Creating assets directory..."
mkdir -p "$TARGET_ASSETS_DIR"

if [ ! -d "$BUILD_DIR/llama.cpp" ]; then
    echo "Cloning llama.cpp repository..."
    mkdir -p "$BUILD_DIR"
    git clone --depth 1 --branch b4600 https://github.com/ggerganov/llama.cpp.git "$BUILD_DIR/llama.cpp"
fi

cd "$BUILD_DIR/llama.cpp"

echo "Configuring CMake for Android arm64-v8a..."
"$CMAKE" -S . -B build-android \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI=arm64-v8a \
    -DANDROID_PLATFORM=android-28 \
    -DGGML_OPENMP=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_BUILD_TYPE=Release

echo "Building llama-server..."
"$CMAKE" --build build-android --config Release --target llama-server -- -j$(sysctl -n hw.ncpu)

echo "Copying compiled binary to Android assets..."
cp build-android/bin/llama-server "$TARGET_ASSETS_DIR/llama-server"
echo "Success! Compiled binary copied to $TARGET_ASSETS_DIR/llama-server"
