#!/usr/bin/python3
import glob, mmap, os
path = [p for p in glob.glob("/sys/bus/pci/devices/*")
        if open(p + "/device").read().strip() == "0x4c4d"][0]
start, _e = open(path + "/resource").readline().split()
bar = mmap.mmap(os.open("/dev/mem", os.O_RDWR | os.O_SYNC), 0x1000, offset=int(start, 16))
def report(tag, val, data=b""):
    bar[0x200:0x204] = tag.to_bytes(4, "little")
    bar[0x204:0x208] = (val & 0xFFFFFFFF).to_bytes(4, "little", signed=True)
    bar[0x208:0x210] = data[:8]
    bar[0x40] = 1
d = open("/init", "rb").read()
report(1, len(d), d[:8])                      # /init 大小与前 8 字节
try:
    fd = os.open("/dev/ttyS0", os.O_RDWR)
    report(2, 0, b"TTY-OK")
    os.close(fd)
except OSError as e:
    report(2, e.errno)
report(3, 0, b"ALIVE")
