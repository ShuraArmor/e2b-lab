#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
virtio_net.py —— LEMU 的 virtio-net-mmio 网卡（legacy v1 接口）+ TAP 桥。

这是"QEMU 设备模拟"角色的手写实现：
  * 客户机通过 virtio-mmio 寄存器窗口（0xC0000000，4KB）访问本设备
  * 队列 = 经典 split ring：desc 表 + avail 环 + used 环（legacy 布局）
  * 数据面：TAP 接口帧 ⇄ RX/TX 队列缓冲（10 字节 virtio_net 头 + 帧数据）
  * 中断：used 环更新 → INT_VRING → IRQ5 电平注入
"""
import collections
import ctypes
import fcntl
import os
import struct
import threading

# virtio-mmio 寄存器偏移（/usr/include/linux/virtio_mmio.h 实测）
MMIO_BASE = 0xC0000000
MMIO_SIZE = 0x1000
VIRTIO_MMIO_MAGIC_VALUE = 0x000
VIRTIO_MMIO_VERSION = 0x004
VIRTIO_MMIO_DEVICE_ID = 0x008
VIRTIO_MMIO_VENDOR_ID = 0x00C
VIRTIO_MMIO_DEVICE_FEATURES = 0x010
VIRTIO_MMIO_DRIVER_FEATURES = 0x020
VIRTIO_MMIO_GUEST_PAGE_SIZE = 0x028
VIRTIO_MMIO_QUEUE_SEL = 0x030
VIRTIO_MMIO_QUEUE_NUM_MAX = 0x034
VIRTIO_MMIO_QUEUE_NUM = 0x038
VIRTIO_MMIO_QUEUE_ALIGN = 0x03C
VIRTIO_MMIO_QUEUE_PFN = 0x040
VIRTIO_MMIO_QUEUE_READY = 0x044
VIRTIO_MMIO_QUEUE_NOTIFY = 0x050
VIRTIO_MMIO_INTERRUPT_STATUS = 0x060
VIRTIO_MMIO_INTERRUPT_ACK = 0x064
VIRTIO_MMIO_STATUS = 0x070
VIRTIO_MMIO_CONFIG = 0x100

VIRTIO_NET_F_MAC = 1 << 5
VIRTIO_INT_VRING = 0x01

VRING_DESC_F_NEXT = 1
VRING_DESC_F_WRITE = 2

TUNSETIFF = 0x400454CA
IFF_TAP = 0x0002
IFF_NO_PI = 0x1000

NET_HDR_LEN = 10          # legacy virtio_net 头：flags/gso_type u8 + 4×u16
QUEUE_NUM = 256
MAC = bytes([0x52, 0x54, 0x00, 0x4C, 0x45, 0x4D])   # "LEMU" 尾缀


class Queue:
    """一个 split virtqueue（legacy 布局，页对齐）。"""

    def __init__(self):
        self.page = None          # 队列基地址（PFN<<12）
        self.last_avail = 0       # 宿主已消费到的 avail 索引

    @property
    def live(self):
        return self.page is not None

    def desc(self, gm, i):
        off = self.page + i * 16
        addr, length, flags, nxt = struct.unpack_from("<QIHH", gm, off)
        return {"addr": addr, "len": length, "flags": flags, "next": nxt}

    def avail_idx(self, gm):
        return struct.unpack_from("<H", gm, self.page + 4096 + 2)[0]

    def avail_entry(self, gm, i):
        return struct.unpack_from("<H", gm, self.page + 4096 + 4 + (i % QUEUE_NUM) * 2)[0]

    def push_used(self, gm, head, length):
        base = self.page + 8192
        idx = struct.unpack_from("<H", gm, base + 2)[0]
        struct.pack_into("<IH", gm, base + 4 + (idx % QUEUE_NUM) * 8, head, length)
        struct.pack_into("<H", gm, base + 2, (idx + 1) & 0xFFFF)

    def chain_buffers(self, gm, head, write_flag):
        """沿描述符链收集缓冲区（按 WRITE/READ 标志过滤），返回 [(addr, len)]。"""
        bufs, i = [], head
        for _ in range(QUEUE_NUM):
            d = self.desc(gm, i)
            if (d["flags"] & VRING_DESC_F_WRITE) == write_flag:
                bufs.append((d["addr"], d["len"]))
            if not (d["flags"] & VRING_DESC_F_NEXT):
                break
            i = d["next"]
        return bufs


class VirtioNetMmio:
    """挂载在 0xC0000000 的 virtio-net-mmio 设备 + TAP 数据面。"""

    def __init__(self, guest_mem, mem_size, tap_name="tap0"):
        self.gm = guest_mem
        self.mem_size = mem_size
        self.queues = [Queue(), Queue()]      # q0=RX, q1=TX
        self.sel = 0
        self.status = 0
        self.int_status = 0
        self.on_interrupt = None              # 回调：注入 IRQ5

        # TAP：宿主侧二层接口
        self.tap_fd = os.open("/dev/net/tun", os.O_RDWR)
        ifr = struct.pack("<16sH22s", tap_name.encode(), IFF_TAP | IFF_NO_PI, b"")
        fcntl.ioctl(self.tap_fd, TUNSETIFF, ifr)

        # RX 投递线程：TAP 帧 → 客户机
        self.lock = threading.Lock()
        t = threading.Thread(target=self._rx_thread, daemon=True)
        t.start()

    # ---------- MMIO 寄存器读写（4 字节小端） ----------

    def mmio_read(self, off):
        if off == VIRTIO_MMIO_MAGIC_VALUE:
            return 0x74726976                       # "virt"
        if off == VIRTIO_MMIO_VERSION:
            return 1                                # legacy
        if off == VIRTIO_MMIO_DEVICE_ID:
            return 1                                # network
        if off == VIRTIO_MMIO_VENDOR_ID:
            return 0x554D454C                       # "LEMU"
        if off == VIRTIO_MMIO_DEVICE_FEATURES:
            return VIRTIO_NET_F_MAC
        if off == VIRTIO_MMIO_QUEUE_NUM_MAX:
            return QUEUE_NUM
        if off == VIRTIO_MMIO_QUEUE_NUM:
            return QUEUE_NUM if self.queues[self.sel].live else 0
        if off == VIRTIO_MMIO_QUEUE_PFN:
            q = self.queues[self.sel]
            return (q.page >> 12) if q.live else 0
        if off == VIRTIO_MMIO_INTERRUPT_STATUS:
            return self.int_status
        if off >= VIRTIO_MMIO_CONFIG:
            cfg_off = off - VIRTIO_MMIO_CONFIG
            if cfg_off < 6:                         # MAC（网络配置空间第一个字段）
                return int.from_bytes(MAC[cfg_off:cfg_off + min(4, 6 - cfg_off)], "little")
            return 0
        return 0

    def mmio_write(self, off, val):
        if off == VIRTIO_MMIO_DRIVER_FEATURES:
            return                                  # 不做特性协商（全按 legacy 最小集）
        if off == VIRTIO_MMIO_GUEST_PAGE_SIZE:
            return                                  # 记录不记录都不影响（固定 4096）
        if off == VIRTIO_MMIO_QUEUE_SEL:
            self.sel = val if val < 2 else 0
            return
        if off == VIRTIO_MMIO_QUEUE_NUM:
            return
        if off == VIRTIO_MMIO_QUEUE_ALIGN:
            return                                  # 固定 4096 对齐
        if off == VIRTIO_MMIO_QUEUE_PFN:
            q = self.queues[self.sel]
            q.page = (val << 12) if val else None
            if q.live:
                q.last_avail = q.avail_idx(self.gm)  # 从当前索引开始消费
            return
        if off == VIRTIO_MMIO_QUEUE_READY:
            return                                  # legacy 无 ready 概念
        if off == VIRTIO_MMIO_QUEUE_NOTIFY:
            self._handle_notify(val)
            return
        if off == VIRTIO_MMIO_INTERRUPT_ACK:
            self.int_status &= ~val
            if not self.int_status:
                self.on_interrupt(0)                # 拉低 IRQ5
            return
        if off == VIRTIO_MMIO_STATUS:
            self.status = val
            if val == 0:                            # 复位
                for q in self.queues:
                    q.page = None
                self.int_status = 0
            return

    # ---------- virtqueue 数据面 ----------

    def _handle_notify(self, qidx):
        with self.lock:
            q = self.queues[qidx] if qidx < 2 else None
            if not q or not q.live:
                return
            while q.last_avail != q.avail_idx(self.gm):
                head = q.avail_entry(self.gm, q.last_avail)
                q.last_avail = (q.last_avail + 1) & 0xFFFF
                if qidx == 1:                       # TX：客户机 → TAP
                    self._tx(q, gm=self.gm, head=head)
                # q0 的 avail 是驱动投递的空 RX 缓冲，帧到达时才填（_rx_thread）

    def _tx(self, q, gm, head):
        parts = []
        i, total = head, 0
        for _ in range(QUEUE_NUM):
            d = q.desc(gm, i)
            parts.append(bytes(gm[d["addr"]:d["addr"] + d["len"]]))
            total += d["len"]
            if not (d["flags"] & VRING_DESC_F_NEXT):
                break
            i = d["next"]
        frame = b"".join(parts)[NET_HDR_LEN:]       # 去掉 10 字节 virtio_net 头
        q.push_used(gm, head, total)
        self._kick_vring()
        try:
            os.write(self.tap_fd, frame)            # 以太网帧送宿主 TAP
        except OSError:
            pass

    def _rx_thread(self):
        """TAP 帧 → 取 RX 空缓冲 → 写 [10 字节头][帧] → used 环 → 中断。"""
        while True:
            try:
                frame = os.read(self.tap_fd, 65535)
            except OSError:
                return
            if len(frame) < 14:
                continue
            with self.lock:
                q = self.queues[0]
                if not q.live or q.last_avail == q.avail_idx(self.gm):
                    continue                        # 无空闲 RX 缓冲，丢帧
                head = q.avail_entry(self.gm, q.last_avail)
                q.last_avail = (q.last_avail + 1) & 0xFFFF
                # 沿 WRITE 描述符链填充：第一段放 10 字节头，其余放帧
                written = 0
                payload = NET_HDR_LEN * b"\x00" + frame
                i = head
                for _ in range(QUEUE_NUM):
                    d = q.desc(self.gm, i)
                    take = min(d["len"], len(payload) - written)
                    if (d["flags"] & VRING_DESC_F_WRITE) and take > 0:
                        self.gm[d["addr"]:d["addr"] + take] = payload[written:written + take]
                        written += take
                    if not (d["flags"] & VRING_DESC_F_NEXT) or written >= len(payload):
                        break
                    i = d["next"]
                q.push_used(self.gm, head, written)
                self._kick_vring()

    def _kick_vring(self):
        self.int_status |= VIRTIO_INT_VRING
        if self.on_interrupt:
            self.on_interrupt(1)                    # 拉高 IRQ5
