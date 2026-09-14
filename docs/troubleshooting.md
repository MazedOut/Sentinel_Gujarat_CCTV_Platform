# Troubleshooting & Diagnostics

> [!WARNING]
> Video ingestion and AI inference involve complex hardware and network interactions. This document covers common failures and their resolutions.

## 1. RTSP Stream Failures

### Symptom: `cv2.VideoCapture` fails to open or drops frames constantly.
**Error Log**: `[h264 @ 0x...] error while decoding MB ...` or `non-existing PPS 0 referenced`

**Cause**: The default OpenCV implementation uses UDP, which drops packets on congested networks, resulting in corrupted frames.
**Solution**: Force TCP transport via environment variables before importing cv2. This is handled natively in `stream_manager.py`, but ensure your system isn't overriding it.
```python
import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
```

### Symptom: Stream crashes after exactly 5-10 minutes.
**Cause**: The Sentinel Gateway might enforce session timeouts on inactive TCP connections.
**Solution**: Ensure `stream_manager.py`'s exponential backoff loop is running. It will catch the broken pipe and reconnect within 2-4 seconds.

## 2. Hardware Acceleration Issues

### Symptom: YOLOv8 runs very slowly (e.g., 2-3 FPS) on a machine with an NVIDIA GPU.
**Cause**: PyTorch was installed from the default PyPI index, which installs the CPU-only version.
**Solution**: Reinstall PyTorch specifically with the CUDA index.
```bash
pip uninstall torch torchvision
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```
Verify in Python:
```python
import torch
print(torch.cuda.is_available()) # Must be True
print(torch.cuda.get_device_name(0))
```

## 3. HLS Proxy Errors

### Symptom: Browser displays `HLS.js error: networkError - fatal: true`.
**Cause**: The server-side cookie (`SENTINEL_HLS_COOKIE`) in your `.env` has expired.
**Solution**: 
1. Log into the official Sentinel Catalogue portal manually via a browser.
2. Inspect the network requests and copy the new `sentinel=...` cookie.
3. Update your `.env` file and restart the FastAPI server.

## 4. PaddleOCR Installation on Windows

### Symptom: `pip install paddleocr` fails with C++ Build Tools errors.
**Cause**: PaddleOCR compiles certain C++ dependencies during installation.
**Solution**: 
1. Download Microsoft C++ Build Tools.
2. Ensure "Desktop development with C++" is checked.
3. Alternatively, install pre-built wheels or use WSL2 (Windows Subsystem for Linux) which compiles much easier.

## 5. Running the Diagnostic Suite

If you are unsure where the failure lies, run the built-in diagnostic tool.
```bash
python scripts/sentinel_m1_diagnostic.py
```
This script will test:
1. Environment variables.
2. CUDA availability.
3. Sentinel Catalogue reachability.
4. RTSP Gateway ping.
5. OCR sanity check.
