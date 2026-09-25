#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""acc_test.py —— 加速卡端到端验证：卡上算的 SHA256 必须与 hashlib 一致"""
import hashlib
import sys

sys.path.insert(0, "/opt")
from acc_driver import LemuAccel

card = LemuAccel()
print("设备发现:", card.path)
print("身份    :", card.identity())

cases = [b"", b"hello from LEMU guest", bytes(range(256)), b"x" * 255]
ok = True
for data in cases:
    mine = card.sha256(data)
    ref = hashlib.sha256(data).digest()
    same = mine == ref
    ok &= same
    print(f"  len={len(data):3d}  {'✅' if same else '❌'}  {mine.hex()[:32]}...")

print("\n结论:", "✅ 宿主 Python 替客户机算完了哈希，虚拟 PCIe 设备有真功能"
      if ok else "❌ 摘要不一致")
