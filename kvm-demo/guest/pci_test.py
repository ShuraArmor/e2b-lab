#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pci_test.py —— 在 LEMU 虚拟机里验证虚拟 PCIe 设备
1) 从 sysfs 枚举 PCI 设备（内核枚举了我们配置空间的结果）
2) 读 BAR0 实际分配地址，mmap /dev/mem 直读设备签名
3) 往 BAR 写一个值再读回（echo 设备）"""
import glob
import mmap
import os

print("--- PCI 枚举（sysfs）---")
for f in sorted(glob.glob("/sys/bus/pci/devices/*")):
    g = lambda n: open(f + "/" + n).read().strip()
    print(f"{f.split('/')[-1]}  vendor={g('vendor')}  device={g('device')}  class={g('class')}")

print("--- BAR0 / 设备签名 ---")
line = open("/sys/bus/pci/devices/0000:00:01.0/resource").readline().split()
addr = int(line[0], 16) or 0xC0001000
print(f"BAR0 客户机物理地址 = {addr:#x}")
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x100, offset=addr)
sig = bytes(m[:12])
print("签名:", sig, "->", "✅ 这是我们配置空间外的真实 MMIO 窗口" if sig.startswith(b"LEMU-PCIE") else "❌ 不对")
m[0:4] = b"TEST"
print("写 TEST 后回读:", bytes(m[:4]))
m.close()
os.close(fd)
