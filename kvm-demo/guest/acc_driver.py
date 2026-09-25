#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
acc_driver.py —— LEMU 加速卡的用户态驱动

没有内核模块依赖（WSL 内核不装模块），走 DPDK 同款哲学：
sysfs 发现设备 -> /dev/mem 映射 BAR -> 按寄存器合同收发。

本驱动实现的协议（与 vmm/lemu.py 的 pci_doorbell 约定一致）：
  0x010 CMD   1 = SHA256
  0x014 LEN   载入字节数 (<=256)
  0x018 STATUS bit1=DONE
  0x01C CTRL  写 1 清 DONE
  0x040 DOORBELL  任意写触发宿主计算
  0x200 载入区   0x300 摘要区(32B)
"""


class LemuAccel:
    VENDOR = 0x4C4D
    DEVICE = 0x4C4D

    CMD_SHA256 = 1
    STATUS_DONE = 0x2

    def __init__(self):
        self.path = self._find()
        self.bar = self._map_bar()

    # ---- 发现：走 sysfs 总线枚举结果，而不是硬编码地址 ----
    def _find(self):
        import glob
        for f in sorted(glob.glob("/sys/bus/pci/devices/*")):
            vendor = int(open(f + "/vendor").read(), 16)
            device = int(open(f + "/device").read(), 16)
            if vendor == self.VENDOR and device == self.DEVICE:
                return f
        raise RuntimeError("LEMU 加速卡未找到（PCI 枚举里没有 4c4d:4c4d）")

    def _map_bar(self):
        import mmap
        import os
        # resource 第一行 = BAR0: "起址 结束 标志"
        start, end, _ = open(self.path + "/resource").readline().split()
        start, size = int(start, 16), int(end, 16) - int(start, 16) + 1
        if start == 0:
            raise RuntimeError("BAR0 未被分配（resource 为空）")
        self.bar_phys = start
        self.bar_size = size
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        return mmap.mmap(fd, size, offset=start)

    def _rd(self, off, n=4):
        return int.from_bytes(self.bar[off:off + n], "little")

    def _wr(self, off, val, n=4):
        self.bar[off:off + n] = val.to_bytes(n, "little")

    # ---- 对外 API：像操作一块真卡 ----
    def identity(self):
        magic = self.bar[0:4]
        ver = self._rd(0x004)
        caps = self._rd(0x008)
        return {"magic": magic.decode("ascii", "replace"),
                "version": f"{ver >> 16}.{ver & 0xFFFF}",
                "caps": caps}

    def sha256(self, data: bytes) -> bytes:
        assert len(data) <= 256, "演示卡的载入区只有 256 字节"
        self._wr(0x01C, 1)                       # CTRL: 清 DONE
        self.bar[0x200:0x200 + len(data)] = data # 载入数据
        self._wr(0x014, len(data))               # LEN
        self._wr(0x010, self.CMD_SHA256)         # CMD
        self._wr(0x040, 1)                       # DOORBELL! 宿主开始计算
        while not (self._rd(0x018) & self.STATUS_DONE):
            pass                                  # 轮询 STATUS（同步卡，几乎立即完成）
        return bytes(self.bar[0x300:0x320])      # 取摘要

    def close(self):
        self.bar.close()
