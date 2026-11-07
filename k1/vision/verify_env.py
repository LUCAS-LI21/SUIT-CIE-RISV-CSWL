#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证板端 Python 推理环境，含 SpaceMIT EP 激活。"""
import sys

print("python:", sys.version.split()[0])

ok = True
try:
    import numpy
    print("numpy:", numpy.__version__)
except Exception as e:
    ok = False
    print("numpy: FAIL", e)

try:
    import cv2
    print("cv2:", cv2.__version__)
except Exception as e:
    ok = False
    print("cv2: FAIL", e)

try:
    import onnxruntime as ort
    print("onnxruntime:", ort.__version__)
    print("providers(import 前):", ort.get_available_providers())
except Exception as e:
    ok = False
    print("onnxruntime: FAIL", type(e).__name__, e)
    sys.exit(1)

# SpaceMIT EP 需要 import spacemit_ort 才会被注册（它 patch 了 session 初始化）
try:
    import spacemit_ort
    print("spacemit_ort:", getattr(spacemit_ort, "__file__", "?"))
    print("EPLibPath:", getattr(spacemit_ort, "EPLibPath", "?"))
    eps = ort.get_available_providers()
    print("providers(import 后):", eps)
    if not any("SpaceMIT" in p for p in eps):
        ok = False
        print("!! 仍然没有 SpaceMITExecutionProvider")
except Exception as e:
    ok = False
    print("spacemit_ort: FAIL", type(e).__name__, e)

print("\nRESULT:", "OK" if ok else "INCOMPLETE")
