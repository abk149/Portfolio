# PortfolioQuant Android Walkthrough

This document guides you through the process of building and running the PortfolioQuant Android application with local DeepSeek R1 integration.

## Prerequisites
- Android Studio installed on your Mac.
- Android NDK (Side by side) installed via SDK Manager.
- Python 3.10 or 3.11 available on your host machine.

## Setup Steps

### 1. Cross-Compile `llama-server`
The Android app requires a native `llama-server` binary compiled for `arm64-v8a`.
Run the following command from the project root:
```bash
chmod +x build_llama_server.sh
./build_llama_server.sh
```
This script will clone `llama.cpp`, compile it using the NDK, and copy the resulting binary to `android/app/src/main/assets/llama-server`.

### 2. Open Project in Android Studio
Open the `android/` directory in Android Studio. Gradle will synchronize the project and download required dependencies (including the Chaquopy Python runtime).

### 3. Build and Deploy
- Connect an Android device with Developer Options and USB Debugging enabled.
- Click **Run** (green play button) in Android Studio.

### 4. Application Usage
- **Dashboard**: Once the servers are started, the WebView will display the FastAPI dashboard.
- **Console**: Go to the Console tab and click **Start Servers** to launch the backend.
- **Settings**: 
    - Enter your Upstox API credentials and save them.
    - Download the recommended 1.5B GGUF model from Hugging Face.
    - You can also pick a custom GGUF model file from your device storage.

## Troubleshooting

### Python Dependencies
The Android version uses a pure-Python compatibility layer (`src/utils/compat.py`) to replace `scipy` functionality (root finding for XIRR and constrained optimization for portfolio MPT). This ensures that all analytics features work on Android even though `scipy` binary wheels are currently problematic in the Chaquopy environment.

### Server Startup
If the servers fail to start, check the logs in the **Console** tab. Ensure that you have downloaded a valid GGUF model and that the `llama-server` binary exists in the app's internal storage (managed automatically by `LlamaServerManager`).
