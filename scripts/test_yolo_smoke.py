"""
Quick smoke-test: loads YOLO26n and dedicated plate detector with GPU acceleration.
Reports REAL steady-state inference time (excludes JIT compilation spike).
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=== YOLO26 + PLATE DETECTOR SMOKE TEST ===")
print()

import torch
print(f"torch        : {torch.__version__}")
print(f"CUDA         : {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU          : {torch.cuda.get_device_name(0)}")
else:
    print(f"GPU          : not available (CPU mode)")
print()

import numpy as np
from ultralytics import YOLO

device = "cuda" if torch.cuda.is_available() else "cpu"

print("1. Loading YOLO26n (yolo26n.pt)...")
t0 = time.monotonic()
model = YOLO("yolo26n.pt")
model.to(device)

dummy = np.ones((720, 1280, 3), dtype=np.uint8) * 128
model(dummy, classes=[2, 3, 5, 7], verbose=False)
model(dummy, classes=[2, 3, 5, 7], verbose=False)
print(f"YOLO26n loaded and warmed up: {(time.monotonic() - t0)*1000:.0f}ms on {device}")

print("2. Benchmarking YOLO26n (5 frames)...")
times = []
for i in range(5):
    t0 = time.monotonic()
    results = model(dummy, classes=[2, 3, 5, 7], verbose=False)
    elapsed = (time.monotonic() - t0) * 1000
    times.append(elapsed)
    print(f"  Frame {i+1}: {elapsed:.1f}ms")

avg_yolo = sum(times) / len(times)
print(f"  Average YOLO26n inference: {avg_yolo:.1f}ms/frame")
print()

print("3. Loading dedicated License Plate Detector (plate_detector.pt)...")
t0 = time.monotonic()
plate_model = YOLO("plate_detector.pt")
plate_model.to(device)
plate_crop_dummy = np.ones((120, 240, 3), dtype=np.uint8) * 128
plate_model(plate_crop_dummy, verbose=False)
print(f"Plate detector loaded and warmed up: {(time.monotonic() - t0)*1000:.0f}ms on {device}")

times_plate = []
for i in range(5):
    t0 = time.monotonic()
    results = plate_model(plate_crop_dummy, verbose=False)
    elapsed = (time.monotonic() - t0) * 1000
    times_plate.append(elapsed)
    print(f"  Crop {i+1}: {elapsed:.1f}ms")

avg_plate = sum(times_plate) / len(times_plate)
print(f"  Average Plate Detector inference: {avg_plate:.1f}ms/crop")
print()

print(f"Combined pipeline inference capability: ~{avg_yolo + avg_plate:.1f}ms total")
print("SUCCESS: YOLO26 and dedicated Plate Detector operational!")
