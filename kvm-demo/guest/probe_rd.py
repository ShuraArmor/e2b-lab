#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""probe_rd.py —— console 读探针：结果写进加速卡 BAR（旁路输出）"""
import glob
import mmap
import os

path = [p for p in glob.glob("/sys/bus/pci/devices/*")
        if open(p + "/device").read().strip() == "0x4c4d"][0]
start, _end = open(path + "/resource").readline().split()
bar = mmap.mmap(os.open("/dev/mem", os.O_RDWR | os.O_SYNC),
                0x1000, offset=int(start, 16))

err, ndata, data = 0, 0, b""
try:
    fd = os.open("/dev/console", os.O_RDONLY)
    d = os.read(fd, 16)
    ndata = len(d)
    data = d[:8]
    os.close(fd)
except OSError as e:
    err = e.errno

bar[0x200:0x204] = err.to_bytes(4, "little", signed=True)
bar[0x204:0x208] = ndata.to_bytes(4, "little")
bar[0x208:0x210] = data
bar[0x040] = 1          # DOORBELL
