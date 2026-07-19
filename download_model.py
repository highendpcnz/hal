#!/usr/bin/env python3
import os
import urllib.request

BASE = "https://huggingface.co/campwill/HAL-9000-Piper-TTS/resolve/main"
FILES = ["hal.onnx", "hal.onnx.json"]

os.makedirs("models", exist_ok=True)
for f in FILES:
    dest = os.path.join("models", f)
    if os.path.exists(dest):
        print(f"skip {dest} (exists)")
        continue
    print(f"downloading {f}...")
    urllib.request.urlretrieve(f"{BASE}/{f}", dest)
    print(f"  -> {dest}")
print("done.")
