#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LEMU —— 纯手写 VMM：不借助 QEMU，直接基于 /dev/kvm 引导真 Linux 内核。

角色与 QEMU 等效（都是 KVM 之上的用户态 VMM），负责：
  1. KVM 原语调用：CREATE_VM / SET_USER_MEMORY_REGION / CREATE_VCPU / RUN
  2. Linux 32 位引导协议：装载 bzImage、构造 zero page / E820 / GDT / initrd
  3. 内核态中断芯片挂载（KVM_CREATE_IRQCHIP，时钟中断）
  4. 16550 UART 模拟：串口控制台双向 I/O + IRQ4 注入
  5. 诊断系统：内置 IDT，引导期任何 CPU 异常都会以字节形式报给宿主

用法（WSL2）：
  python3 vmm/lemu.py -k images/bzImage-wsl -i images/initramfs.cpio.gz
  可选：-m 256  --append "..."  --trace  --max-seconds 60
退出：Ctrl+C
"""
import argparse
import collections
import ctypes
import fcntl
import mmap
import os
import select
import struct
import sys
import time
from datetime import datetime

# ---------------- ioctl（linux/kvm.h，编号已在本机验证） ----------------
KVM_GET_API_VERSION = 0xAE00
KVM_CREATE_VM = 0xAE01
KVM_GET_VCPU_MMAP_SIZE = 0xAE04
KVM_CREATE_IRQCHIP = 0xAE60
KVM_IRQ_LINE = 0x4008AE61
KVM_CREATE_VCPU = 0xAE41
KVM_SET_USER_MEMORY_REGION = 0x4020AE46
KVM_SET_TSS_ADDR = 0xAE47
KVM_GET_SREGS = 0x8138AE83
KVM_SET_SREGS = 0x4138AE84
KVM_GET_REGS = 0x8090AE81
KVM_SET_REGS = 0x4090AE82
KVM_CHECK_EXTENSION = 0xAE03
KVM_ENABLE_CAP = 0x4068AEA3
KVM_SET_MSR_FILTER = 0x4188AEC6        # 内核 6.x 里叫 KVM_X86_SET_MSR_FILTER（gcc 实测）
KVM_EXIT_X86_RDMSR = 27
KVM_EXIT_X86_WRMSR = 28
KVM_RUN = 0xAE80

KVM_EXIT_IO = 2
KVM_EXIT_HLT = 5
KVM_EXIT_MMIO = 6
KVM_EXIT_SHUTDOWN = 8
KVM_EXIT_FAIL_ENTRY = 9
KVM_EXIT_INTR = 10
KVM_EXIT_INTERNAL_ERROR = 17

OFF_EXIT_REASON = 8      # kvm_run 实测偏移（gcc offsetof，x86_64 6.6 内核）
OFF_IO = 32

# ---------------- 客户机物理内存布局 ----------------
GDT_ADDR = 0x1000
ZP_ADDR = 0x10000
CMDLINE_ADDR = 0x20000
KERNEL_ADDR = 0x100000
HANDLER_BASE = 0x8F000   # 诊断 IDT handler（放在 E820 第一段末尾，不与内核早期数据冲突）
IDT_ADDR = 0x8F800


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ================= 诊断 IDT =================

def install_debug_idt(guest_mem):
    """32 个异常向量各挂一个处理程序：输出 'E' + 向量号 + '!' 后 hlt。
    内核装好自己的 IDT 后这些门自然失效，只覆盖引导早期。"""
    for v in range(32):
        h = HANDLER_BASE + v * 16
        code = bytes([0xB0, v,                # mov al, v（向量号）
                      0xBA, 0xF8, 0x03,       # mov dx, 0x3F8
                      0xEE,                   # out dx, al  ← 触发 VM exit
                      0xB0, ord('!'),
                      0xEE,                   # out dx, '!'
                      0xEB, 0xF4,             # jmp 回 mov al,v（死循环刷串口）
                      0x90, 0x90, 0x90,       # nop padding to 16 bytes
                      0x90, 0x90])
        guest_mem[h:h + 16] = code[:16]
    idt = bytearray(32 * 8)
    for v in range(32):
        h = HANDLER_BASE + v * 16
        struct.pack_into("<HHBBH", idt, v * 8, h & 0xFFFF, 0x08, 0, 0x8E, h >> 16)
    guest_mem[IDT_ADDR:IDT_ADDR + len(idt)] = idt


# ================= 64 位引导协议 =================

PML4_ADDR = 0x2000      # 4 级页表放在 0x2000（4 页 = 16KB）
PDPT_ADDR = 0x3000
PD_ADDR   = 0x4000      # 2MB 大页 PD，一张覆盖 1GB

def build_identity_pagetable(guest_mem, mem_size):
    """构建恒等映射（identity mapping）4 级页表：虚拟地址 = 物理地址。
    使用 2MB 大页，一张 PD 覆盖 1GB——对 256MB 内存足够。"""
    pml4 = bytearray(4096)
    struct.pack_into("<Q", pml4, 0, PDPT_ADDR | 0x03)    # Present + RW
    guest_mem[PML4_ADDR:PML4_ADDR + 4096] = pml4

    pdpt = bytearray(4096)
    struct.pack_into("<Q", pdpt, 0, PD_ADDR | 0x03)      # Present + RW
    guest_mem[PDPT_ADDR:PDPT_ADDR + 4096] = pdpt

    pd = bytearray(4096)
    for i in range(512):                                  # 512 × 2MB = 1GB
        struct.pack_into("<Q", pd, i * 8, (i << 21) | 0x83)  # Present + RW + PS(2MB)
    guest_mem[PD_ADDR:PD_ADDR + 4096] = pd


def build_boot_state(guest_mem, mem_size, kernel, cmdline, initrd):
    if kernel[0x202:0x206] != b"HdrS":
        sys.exit("不是有效的 bzImage（缺少 HdrS 魔数）")

    xloadflags = kernel[0x236] | (kernel[0x237] << 8)
    use_64bit = bool(xloadflags & 1)
    log(f"    xloadflags=0x{xloadflags:04X}  64位入口: {'是' if use_64bit else '否'}")

    setup_sects = kernel[0x1F1] or 4
    pm_off = (setup_sects + 1) * 512
    code32_start = struct.unpack_from("<I", kernel, 0x214)[0] or 0x100000

    guest_mem[code32_start: code32_start + len(kernel) - pm_off] = kernel[pm_off:]

    if use_64bit:
        entry = code32_start + 0x200                      # 64 位入口 = 32 位入口 + 0x200
    else:
        entry = code32_start

    initrd_addr = (mem_size - len(initrd)) & ~0xFFF
    guest_mem[initrd_addr: initrd_addr + len(initrd)] = initrd
    guest_mem[CMDLINE_ADDR: CMDLINE_ADDR + len(cmdline)] = cmdline

    zp = bytearray(4096)
    zp[0x1F1:0x270] = kernel[0x1F1:0x270]
    zp[0x210] = 0xFF
    zp[0x211] |= 0x01
    struct.pack_into("<I", zp, 0x214, code32_start)
    struct.pack_into("<I", zp, 0x218, initrd_addr)
    struct.pack_into("<I", zp, 0x21C, len(initrd))
    struct.pack_into("<I", zp, 0x228, CMDLINE_ADDR)
    e820 = [(0x0, 0x9F000, 1),
            (0x9F000, 0x100000 - 0x9F000, 2),
            (0x100000, mem_size - 0x100000, 1)]
    zp[0x1E8] = len(e820)
    for i, (base, size, typ) in enumerate(e820):
        struct.pack_into("<QQI", zp, 0x2D0 + 20 * i, base, size, typ)
    guest_mem[ZP_ADDR:ZP_ADDR + 4096] = zp

    # 64 位 GDT：null / 0x08 data(64) / 0x10 code(64)
    gdt = bytes.fromhex("0000000000000000"      # 0x00 null
                        "00CF93000000FFFF"       # 0x08 data: base=0 limit=4G RW
                        "00AF9B000000FFFF"       # 0x10 code: base=0 limit=4G L=1(长模式)
                        "00CF93000000FFFF")      # 0x18 data (内核可能用)
    guest_mem[GDT_ADDR:GDT_ADDR + len(gdt)] = gdt

    if use_64bit:
        build_identity_pagetable(guest_mem, mem_size)

    log(f"    内核 {len(kernel)>>20}MB @ 0x{code32_start:X}  "
        f"entry {'64位' if use_64bit else '32位'} @ 0x{entry:X}  "
        f"initrd {len(initrd)>>10}KB @ 0x{initrd_addr:X}")
    return entry, use_64bit


def setup_cpu(vcpu_fd, entry_addr, long_mode=False):
    sregs = ctypes.create_string_buffer(312)
    fcntl.ioctl(vcpu_fd, KVM_GET_SREGS, sregs, True)

    def seg(off, selector, type_, base=0, limit=0xFFFFF, db=1, g=1, s=1, l=0):
        struct.pack_into("<QIH8B2x", sregs, off,
                         base, limit, selector, type_, 1, 0, db, s, l, g, 0)

    if long_mode:
        seg(0, 0x10, 0x9B, db=0, l=1)                    # CS: 64 位代码段
        for off in (24, 48, 72, 96, 120):
            seg(off, 0x08, 0x93)                          # DS/ES/FS/GS/SS: 数据段
        seg(144, 0, 0xB, limit=0xFFFF, db=0, g=0, s=0)
        struct.pack_into("<QH6x", sregs, 192, GDT_ADDR, 0x1F)   # GDTR
        struct.pack_into("<QH6x", sregs, 208, 0, 0)              # IDTR：空（内核自己装 64 位 IDT）
        struct.pack_into("<Q", sregs, 224, 0x80000033)    # CR0 = PG|PE|MP|ET|NE
        struct.pack_into("<Q", sregs, 240, PML4_ADDR)     # CR3
        struct.pack_into("<Q", sregs, 248, 0x20)          # CR4 = PAE
        struct.pack_into("<Q", sregs, 264, 0xD00)         # EFER = LME|LMA|SCE
    else:
        seg(0, 0x08, 0x9B)
        for off in (24, 48, 72, 96, 120):
            seg(off, 0x10, 0x93)
        seg(144, 0, 0xB, limit=0xFFFF, db=0, g=0, s=0)
        struct.pack_into("<QH6x", sregs, 192, GDT_ADDR, 0x17)
        struct.pack_into("<QH6x", sregs, 208, IDT_ADDR, 32 * 8 - 1)
        struct.pack_into("<Q", sregs, 224, 0x33)
        struct.pack_into("<Q", sregs, 240, 0)
        struct.pack_into("<Q", sregs, 248, 0x20)
        struct.pack_into("<Q", sregs, 264, 0)

    fcntl.ioctl(vcpu_fd, KVM_SET_SREGS, sregs, True)

    regs = ctypes.create_string_buffer(144)
    struct.pack_into("<18Q", regs, 0, *([0] * 18))
    struct.pack_into("<Q", regs, 32, ZP_ADDR)             # RSI = boot_params
    struct.pack_into("<Q", regs, 48, 0x9000)              # RSP
    struct.pack_into("<Q", regs, 128, entry_addr)         # RIP
    struct.pack_into("<Q", regs, 136, 0x2)                # RFLAGS
    fcntl.ioctl(vcpu_fd, KVM_SET_REGS, regs, True)


# ================= 16550 UART =================

class Uart:
    def __init__(self, vm_fd, guest_out):
        self.vm_fd = vm_fd
        self.guest_out = guest_out
        self.rx = bytearray()
        self.irq_level = 0
        self.dlab = 0
        self.ier = 0
        self.lcr = 0
        self.mcr = 0
        self.fcr = 0
        self.scratch = 0
        self.dll = 12
        self.dlm = 0
        self.last_thr = 0
        self.rx_int_pending = False
        self.tx_int_pending = False    # 发送器空中断挂起（tty 用户态输出依赖它！）

    def _irq(self, level):
        if level != self.irq_level:
            self.irq_level = level
            buf = ctypes.create_string_buffer(8)
            struct.pack_into("<II", buf, 0, 4, level)
            fcntl.ioctl(self.vm_fd, KVM_IRQ_LINE, buf, True)

    def _update_irq(self):
        """电平语义：RX/TX 任一挂起且驱动使能对应 IER 位 → 拉高 IRQ4，
        直到驱动读 IIR 应答/清条件才拉低。脉冲式会被 LAPIC LINT0 漏采。"""
        level = 1 if ((self.rx_int_pending and (self.ier & 0x01)) or
                      (self.tx_int_pending and (self.ier & 0x02))) else 0
        self._irq(level)

    def push_input(self, data):
        self.rx.extend(data)
        self.rx_int_pending = True
        self._update_irq()

    def read(self, off):
        if self.dlab and off == 0:
            return self.dll
        if self.dlab and off == 1:
            return self.dlm
        if off == 0:
            if self.mcr & 0x10:
                return self.last_thr
            if self.rx:
                b = self.rx.pop(0)
                if not self.rx:
                    self.rx_int_pending = False
                    self._update_irq()
                return b
            return 0
        if off == 1:
            return self.ier
        if off == 2:                                    # IIR：中断原因
            fifo_bits = 0xC0 if self.fcr & 1 else 0
            if self.rx and self.rx_int_pending:
                return 0x04 | fifo_bits                 # 收到数据中断
            if self.tx_int_pending and (self.ier & 0x02):
                self.tx_int_pending = False             # 读 IIR 应答 TX 中断
                self._update_irq()
                return 0x02 | fifo_bits                 # 发送器空中断
            return 0x01 | fifo_bits                     # 无中断挂起
        if off == 3:
            return self.lcr
        if off == 4:
            return self.mcr
        if off == 5:
            return (0x60 | (0x01 if self.rx else 0)) & 0xFF
        if off == 6:                                    # MSR
            if self.mcr & 0x10:                         # loopback：MCR 低 4 位镜像到 MSR 高 4 位
                return ((self.mcr & 0x0F) << 4) | 0x00  # Linux 8250 探测靠这个判定"是真串口"
            return 0xB0                                 # 非 loopback：CTS|DSR|DCD 在线
        if off == 7:
            return self.scratch
        return 0

    def write(self, off, val):
        if self.dlab and off == 0:
            self.dll = val
            return
        if self.dlab and off == 1:
            self.dlm = val
            return
        if off == 0:
            if self.mcr & 0x10:
                self.last_thr = val
                return
            self.guest_out(bytes([val]))
            # 发送完成 → 若驱动使能了 TX 中断(IER bit1)，挂起"发送器空"中断
            # 用户态 tty 输出靠这个中断推进（没有它驱动睡死——之前 stall 16 字节的根因）
            if self.ier & 0x02 and not self.tx_int_pending:
                self.tx_int_pending = True
                self._update_irq()
        elif off == 1:
            old = self.ier
            self.ier = val
            # 真实 16550 语义：THRE 状态下使能 THRI(位1) → 中断"立即"触发。
            # 用户态 tty 输出全靠它推进（没有它驱动睡死、输出恰好卡 16 字节 FIFO）
            if (val & 0x02) and not (old & 0x02):
                self.tx_int_pending = True
            self._update_irq()          # IER 屏蔽变化会拉低/拉高中断线
        elif off == 2:
            self.fcr = val
        elif off == 3:
            self.lcr = val
            self.dlab = 1 if val & 0x80 else 0
        elif off == 4:
            self.mcr = val
        elif off == 7:
            self.scratch = val


# ================= 主程序 =================

class Timeout(Exception):
    pass


def host_tsc_khz():
    """从 /proc/cpuinfo 估算宿主 TSC 频率（kHz）。"""
    try:
        for line in open("/proc/cpuinfo"):
            if "model name" in line and "@" in line:
                ghz = float(line.split("@")[-1].strip().rstrip("GHz").strip())
                return int(ghz * 1_000_000)
    except Exception:
        pass
    return 2_800_000  # 兜底：假设 2.8GHz


# ================= ACPI 表构建（让内核发现 virtio-mmio 设备） =================

ACPI_RSDP_ADDR = 0xF0000    # RSDP 扫描范围 0xE0000-0xFFFFF 内
ACPI_XSDT_ADDR = 0xF1000
ACPI_FACP_ADDR = 0xF2000
ACPI_DSDT_ADDR = 0xF4000


def _table_checksum(tbl: bytearray):
    tbl[9] = 0                                       # 先清零再求和（否则差一字节）
    tbl[9] = (256 - (sum(tbl) & 0xFF)) & 0xFF


def _table_header(sig, length, oem_table_id=b"LEMUVM01", rev=1):
    h = bytearray(length)
    h[0:4] = sig
    struct.pack_into("<I", h, 4, length)
    h[8] = rev                                       # Revision @8
    h[10:16] = b"LEMU  "                             # OEMID (6)
    h[16:24] = oem_table_id                          # OEM Table ID (8)
    struct.pack_into("<I", h, 24, 1)                 # OEM revision
    h[28:32] = b"LEMU"                               # Creator ID
    struct.pack_into("<I", h, 32, 1)                 # Creator revision
    return h


def build_acpi(guest_mem, dsdt_bytes):
    """布置 RSDP/XSDT/FACP/DSDT，全部落在 E820 保留区 [0x9F000, 0x100000)。"""
    dsdt_len = len(dsdt_bytes)
    # ---- DSDT（compiled AML）----
    dsdt = bytearray(dsdt_bytes)
    dsdt[0:4] = b"DSDT"
    struct.pack_into("<I", dsdt, 4, dsdt_len)
    dsdt[16:24] = b"LEMUVIRT"
    dsdt[28:32] = b"INTL"                       # iasl 默认编译器 ID
    _table_checksum(dsdt)
    guest_mem[ACPI_DSDT_ADDR:ACPI_DSDT_ADDR + dsdt_len] = dsdt

    # ---- FACP（FADT，硬件精简模式）----
    # 规格 4.0+ 字段偏移：FIRMWARE_CTRL@36 DSDT@40 FLAGS@112
    # X_FIRMWARE_CTRL@132 X_DSDT@140 MINOR@131 ARM_BOOT@129
    facp = _table_header(b"FACP", 276, rev=5)
    struct.pack_into("<I", facp, 36, 0)                          # FIRMWARE_CTRL = 0（精简模式无 FACS）
    struct.pack_into("<I", facp, 40, ACPI_DSDT_ADDR)             # DSDT (32 位字段)
    struct.pack_into("<Q", facp, 132, 0)                         # X_FIRMWARE_CTRL = 0
    struct.pack_into("<Q", facp, 140, ACPI_DSDT_ADDR)            # X_DSDT (64 位字段)
    struct.pack_into("<I", facp, 112, (1 << 0) | (1 << 20))      # WBINVD | ACPI_HW_REDUCED_ACPI
    _table_checksum(facp)
    guest_mem[ACPI_FACP_ADDR:ACPI_FACP_ADDR + len(facp)] = facp

    # ---- XSDT（指向 FACP 和 DSDT）----
    xsdt = _table_header(b"XSDT", 36 + 16, rev=1)
    struct.pack_into("<QQ", xsdt, 36, ACPI_FACP_ADDR, ACPI_DSDT_ADDR)
    _table_checksum(xsdt)
    guest_mem[ACPI_XSDT_ADDR:ACPI_XSDT_ADDR + len(xsdt)] = xsdt

    # ---- RSDP（kernel 在 0xE0000-0xFFFFF 扫描 "RSD PTR "）----
    # 布局：签名(8) 校验和(1)@8 OEMID(6)@9 版本(1)@15 RSDT(4)@16 长度(4)@20 XSDT(8)@24 扩展校验和(1)@32
    rsdp = bytearray(36)
    rsdp[0:8] = b"RSD PTR "
    rsdp[8] = 0                                  # 校验和（稍后填）
    rsdp[9:15] = b"LEMU  "                       # OEMID
    rsdp[15] = 2                                 # ACPI 2.0+
    struct.pack_into("<I", rsdp, 16, 0)          # RSDT（无，走 XSDT）
    struct.pack_into("<I", rsdp, 20, 36)         # Length
    struct.pack_into("<Q", rsdp, 24, ACPI_XSDT_ADDR)
    rsdp[8] = (256 - (sum(rsdp[0:20]) & 0xFF)) & 0xFF    # 前 20 字节校验和
    rsdp[32] = (256 - (sum(rsdp[0:36]) & 0xFF)) & 0xFF   # 扩展校验和（全 36 字节）
    guest_mem[ACPI_RSDP_ADDR:ACPI_RSDP_ADDR + 36] = rsdp
    log(f"    ACPI: RSDP@0x{ACPI_RSDP_ADDR:X} XSDT@0x{ACPI_XSDT_ADDR:X} "
        f"FACP@0x{ACPI_FACP_ADDR:X} DSDT({dsdt_len}B)@0x{ACPI_DSDT_ADDR:X}")


def setup_msr_passthrough(vm_fd, kvm_fd):
    """把 MSR_PLATFORM_INFO(0xCE) 和 MSR_FSB_FREQ(0xCD) 路由到用户态。
    内核 TSC 校准读这两个 MSR 拿真实 CPU 频率——没有它们内核会掉进
    '等 jiffies' 的死等（QEMU 靠同样的直通机制存活）。"""
    if fcntl.ioctl(kvm_fd, KVM_CHECK_EXTENSION, 188) != 1:
        log("[lemu] 警告：KVM 不支持 user-space MSR，TSC 校准可能卡住")
        return
    bitmap = bytearray(256)
    bitmap[0] = 0x03                       # 位0=0xCD(RDMSR)，位1=0xCE(RDMSR)
    bmp = ctypes.create_string_buffer(bytes(bitmap), len(bitmap))   # 必须活到 ioctl 之后
    bmp_addr = ctypes.addressof(ctypes.cast(bmp, ctypes.POINTER(ctypes.c_char)))
    filt = bytearray(392)                  # kvm_msr_filter: flags + 16×range
    struct.pack_into("<I", filt, 0, 0)     # 默认动作：允许（KVM 自行处理）
    struct.pack_into("<III", filt, 8, 1, 2, 0xCD)   # range: READ, 2 个 MSR, base=0xCD
    struct.pack_into("<Q", filt, 24, bmp_addr)
    fcntl.ioctl(vm_fd, KVM_SET_MSR_FILTER, filt, True)

    cap = ctypes.create_string_buffer(104)  # kvm_enable_cap
    struct.pack_into("<II", cap, 0, 188, 0)             # KVM_CAP_X86_USER_SPACE_MSR
    struct.pack_into("<Q", cap, 8, 4)                   # args[0] = KVM_MSR_EXIT_REASON_FILTER
    fcntl.ioctl(vm_fd, KVM_ENABLE_CAP, cap, True)
    log("    MSR 直通已启用（0xCE/0xCD → 宿主）")


def msr_read(index, tsc_khz):
    if index == 0xCE:                    # MSR_PLATFORM_INFO：bits 15:8 = 标称频率比(100MHz 单位)
        ratio = max(1, round(tsc_khz / 100_000))
        return (ratio & 0xFF) << 8
    if index == 0xCD:                    # MSR_FSB_FREQ：5 = 100MHz 总线
        return 5
    return 0


def main():
    ap = argparse.ArgumentParser(description="LEMU —— 纯手写 KVM VMM")
    ap.add_argument("-k", "--kernel", required=True)
    ap.add_argument("-i", "--initrd", required=True)
    ap.add_argument("-m", "--mem", type=int, default=256, help="内存 MiB")
    ap.add_argument("-a", "--append", default="", help="附加内核参数")
    ap.add_argument("--max-seconds", type=int, default=0)
    ap.add_argument("--trace", action="store_true", help="打印每次 VM exit 的 RIP")
    ap.add_argument("--net", action="store_true", help="启用 virtio-net 网卡（需 root + tap0 已建）")
    ap.add_argument("--tap", default="tap0", help="TAP 接口名（默认 tap0）")
    ap.add_argument("--cpuid", action="store_true", help="灌入宿主 CPUID 表（剥离 SMAP/SMEP，默认关）")
    ap.add_argument("--msr", action="store_true", help="启用 MSR 直通（默认关）")
    args = ap.parse_args()

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    ROOT_DIR = os.path.dirname(SCRIPT_DIR)

    mem_size = args.mem << 20
    kernel = open(args.kernel, "rb").read()
    initrd = open(args.initrd, "rb").read()
    cmdline = ("console=ttyS0,115200n8 earlyprintk=serial,ttyS0,115200 "
               "rdinit=/init nokaslr " + args.append).encode()
    # 设备发现走 ACPI（DSDT 里描述 LNRO0005）；cmdline 注册方式该内核未编译

    kvm_fd = os.open("/dev/kvm", os.O_RDWR)
    assert fcntl.ioctl(kvm_fd, KVM_GET_API_VERSION, 0) == 12
    vm_fd = fcntl.ioctl(kvm_fd, KVM_CREATE_VM, 0)

    guest_mem = mmap.mmap(-1, mem_size, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
    buf = ctypes.create_string_buffer(32)
    struct.pack_into("<IIQQQ", buf, 0, 0, 0, 0, mem_size,
                     ctypes.addressof(ctypes.c_char.from_buffer(guest_mem)))
    fcntl.ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, buf, True)
    fcntl.ioctl(vm_fd, KVM_CREATE_IRQCHIP, 0)
    # PIT（8254 定时器）：内核校准延迟/TSC 用。没有它早期代码可能死循环等待定时器中断
    pit_buf = ctypes.create_string_buffer(64)
    fcntl.ioctl(vm_fd, 0x4040AE77, pit_buf, True)         # KVM_CREATE_PIT2
    fcntl.ioctl(vm_fd, KVM_SET_TSS_ADDR, 0x40000000)

    # 64 位内核会在 startup_64 里立刻装自己的 IDT，不需要诊断 IDT
    # install_debug_idt(guest_mem)
    log("[*] 布置引导状态...")
    entry, use_64bit = build_boot_state(guest_mem, mem_size, kernel, cmdline, initrd)

    # ---- 极简 PIT 通道 2：给内核 TSC 校准当"合成晶体" ----
    # 客户机把 0xFFFF 装进通道 2（mode 0 一次性倒计时），随后轮询端口 0x61 bit5
    # 等"倒计时归零"，用这段时间差里的 TSC 读数计算 CPU 主频。
    # 我们的 PIT 按宿主真实时钟以 1193182Hz 速率递减 —— 所以校准结果就是真频率。
    PIT_RATE = 1193182.0
    pit2 = {"count": 0, "start": 0.0, "active": False, "phase": 0, "latched": None, "expired": False}

    def pit2_write(v):                          # OUT 0x42：先 LSB 后 MSB 装载计数
        if pit2["phase"] == 0:
            pit2["count"] = v
            pit2["phase"] = 1
        else:
            pit2["count"] |= v << 8
            pit2["phase"] = 0
            pit2["start"] = time.monotonic()
            pit2["active"] = True
            pit2["expired"] = False

    def pit2_remaining():
        # 真实 8254 语义：倒计到 0 后回绕 0xFFFF 继续减（永不冻结）
        if not pit2["active"]:
            return 0
        gone = int((time.monotonic() - pit2["start"]) * PIT_RATE)
        if gone >= pit2["count"]:
            pit2["expired"] = True           # 模式 0：OUT 引脚在终端计数后保持高
        return (pit2["count"] - gone) & 0xFFFF

    netdev = None
    if args.net:
        import virtio_net
        netdev = virtio_net.VirtioNetMmio(guest_mem, mem_size, args.tap)

        def virtio_irq(level):
            vbuf = ctypes.create_string_buffer(8)
            struct.pack_into("<II", vbuf, 0, 5, level)   # ISA IRQ5 = virtio
            fcntl.ioctl(vm_fd, KVM_IRQ_LINE, vbuf, True)

        netdev.on_interrupt = virtio_irq
        # ACPI：DSDT 描述 LNRO0005 设备（MMIO 0xC0000000/4K + IRQ5），内核 ACPI 枚举发现它
        dsdt_bytes = open(os.path.join(ROOT_DIR, "guest", "acpi", "DSDT.aml"), "rb").read()
        build_acpi(guest_mem, dsdt_bytes)
        log(f"    virtio-net 已挂载 @ 0xC0000000（TAP: {args.tap}，MAC 52:54:00:4C:45:4D）")

    tsc_khz = host_tsc_khz()
    if args.msr:
        setup_msr_passthrough(vm_fd, kvm_fd)

    vcpu_fd = fcntl.ioctl(vm_fd, KVM_CREATE_VCPU, 0)

    if args.cpuid:
        # ---- 精选白名单 CPUID（kvmtool 式）：只给内核必需的最小特性集 ----
        # 不透传宿主全表：SMAP/SMEP/UMIP/PKU/OSPKE/uncore 等所有会与
        # 手写环境冲突的特性从源头不存在。叶 0x15/0x16 提供 TSC 频率。
        V = (0x756e6547, 0x6c65746e, 0x49656e69)          # "GenuineIntel"
        entries = [
            # (function, index, EAX, EBX, ECX, EDX)
            (0x0,        0, 0x16, V[0], V[2], V[1]),      # max leaf 0x16
            (0x1,        0,
             0x000806E9,                                   # Family6 Model142 (KBL)
             0x00000800,                                   # CLFLUSH=64B
             0x40000001,                                   # SSE3 + RDRAND
             0x078BFBFF),                                  # FPU..SSE2, HTT 等
            (0x7,        0, 0, 0, 0, 0),                  # 无扩展特性(无SMAP/SMEP/UMIP/PKU)
            (0x15,       0, 100, 2800, 100_000_000, 0),   # TSC = 100MHz*2800/100 = 2.8GHz
            (0x16,       0, 2800, 0, 0, 0),               # 基频 2800MHz
            (0x80000000, 0, 0x80000008, V[0], V[2], V[1]),
            (0x80000001, 0, 0, 0, 0, (1 << 29) | (1 << 27) | (1 << 20)),  # LM|RDTSCP|NX
            (0x80000007, 0, 0, 0, 0, 1 << 8),             # invariant TSC
            (0x80000008, 0, 0x30, 0, 0, 0),               # 物理 48 位
        ]
        MAX_CPUID = len(entries)
        cpuid_buf = ctypes.create_string_buffer(8 + MAX_CPUID * 40)
        struct.pack_into("<I", cpuid_buf, 0, MAX_CPUID)
        for i, (fn, sub, eax, ebx, ecx, edx) in enumerate(entries):
            e = 8 + i * 40
            struct.pack_into("<IIIIIIII", cpuid_buf, e,
                             fn, sub, 0, eax, ebx, ecx, edx, 0)
        fcntl.ioctl(vcpu_fd, 0x4008AE90, cpuid_buf, True)   # KVM_SET_CPUID2
        log(f"    CPUID: 精选白名单 {MAX_CPUID} 条已灌入")

    # CPUID 灌入已默认关闭：实测该内核不灌 CPUID 也能正常引导，
    # 而灌入宿主 CPUID 表后 leaf 0x15 的值会触发内核 TSC 校准除零 oops。

    kvm_run = mmap.mmap(vcpu_fd, fcntl.ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0),
                        mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
    setup_cpu(vcpu_fd, entry, long_mode=use_64bit)

    saved_attrs = None
    if sys.stdin.isatty():
        import termios
        import tty
        saved_attrs = termios.tcgetattr(0)
        tty.setraw(0)
    LOGS_DIR = os.path.join(ROOT_DIR, "logs")
    os.makedirs(LOGS_DIR, exist_ok=True)

    out_buf = sys.stdout.buffer
    logf = open(os.path.join(LOGS_DIR, f"lemu-{datetime.now():%m%d-%H%M%S}.log"), "wb")

    def guest_out(data):
        out_buf.write(data)
        out_buf.flush()
        logf.write(data)
        logf.flush()

    uart = Uart(vm_fd, guest_out)

    # 心跳线程（QEMU 架构核心二：定时器也走独立线程）：
    # 但关键时序原则 —— **客户机编程 PIT 之前绝不注入 IRQ0**！
    # 早期内核的中断向量表还是"拒绝一切"的状态，提前送中断 = 静默 panic。
    # （QEMU 同样：in-kernel PIT 只在客户机编程后才产生 tick）
    import threading
    pit0_programmed = [False]      # 线程间共享标志
    def ticker():
        while True:
            time.sleep(0.01)
            if not pit0_programmed[0]:
                continue
            vbuf = ctypes.create_string_buffer(8)
            struct.pack_into("<II", vbuf, 0, 0, 1)   # IRQ0 → high
            fcntl.ioctl(vm_fd, KVM_IRQ_LINE, vbuf, True)
            struct.pack_into("<II", vbuf, 0, 0, 0)   # IRQ0 → low
            fcntl.ioctl(vm_fd, KVM_IRQ_LINE, vbuf, True)
    threading.Thread(target=ticker, daemon=True).start()

    # 输入线程（QEMU 架构的核心一课：设备 I/O 必须在独立线程，
    # 数据到达后 KVM_IRQ_LINE 会 kick 阻塞中的 vCPU，永不死锁）
    def stdin_reader():
        try:
            while True:
                data = os.read(0, 4096)
                if not data:
                    return                      # 管道 EOF
                uart.push_input(data)
        except OSError:
            return
    threading.Thread(target=stdin_reader, daemon=True).start()

    def dump_state(tag):
        try:
            regs = ctypes.create_string_buffer(144)
            fcntl.ioctl(vcpu_fd, KVM_GET_REGS, regs, True)
            rip, rflags = struct.unpack_from("<QQ", regs, 128)
            rax, rcx, rdx = struct.unpack_from("<3Q", regs, 0)
            sregs = ctypes.create_string_buffer(312)
            fcntl.ioctl(vcpu_fd, KVM_GET_SREGS, sregs, True)
            cr2, cr3 = struct.unpack_from("<QQ", sregs, 232)
            log(f"[{tag}] RIP=0x{rip:X} CR2=0x{cr2:X} CR3=0x{cr3:X}")
            log(f"[{tag}] RAX=0x{rax:X} RCX=0x{rcx:X} RDX=0x{rdx:X}")

            # ---- 取证 1：CR2 页表遍历 ----
            def walk(cr3, va):
                out = []
                idxs = [(va >> 39) & 0x1FF, (va >> 30) & 0x1FF,
                        (va >> 21) & 0x1FF, (va >> 12) & 0x1FF]
                table = cr3 & ~0xFFF
                for level, i in enumerate(idxs):
                    e = struct.unpack_from("<Q", guest_mem, table + i * 8)[0]
                    out.append(f"L{level}[{i}]=0x{e:X}")
                    if not e & 1:
                        return out + ["NOT-PRESENT"]
                    if e & 0x80:
                        width = {1: (1 << 30), 2: (1 << 21)}[level]
                        out.append(f"大页→物理基址0x{e & ~(width-1):X}")
                        return out
                    table = e & ~0xFFF
                out.append(f"PT→物理0x{table:X}")
                return out

            if cr3:
                log("[取证] CR2 页表遍历: " + " | ".join(walk(cr3, cr2)))

            # ---- 取证 2：故障指令字节（RIP 高地址映射 → 物理）----
            if rip >= 0xFFFFFFFF80000000:
                phys = rip - 0xFFFFFFFF81000000 + 0x1000000   # text VA基址0x81000000 ↔ 物理0x1000000
                if 0 <= phys < mem_size - 16:
                    log(f"[取证] RIP 物理 0x{phys:X} 前后字节: "
                        f"{bytes(guest_mem[phys-16:phys+16]).hex()}")
                    open(os.path.join(LOGS_DIR, "fault_code.bin"), "wb").write(
                        bytes(guest_mem[phys-64:phys+64]))
        except Exception as e:
            log(f"[{tag}] 状态转储失败: {e}")

    log("[*] 点火！客户机输出如下：")
    log("-" * 60)
    start = time.monotonic()
    rc = 0
    total_exits = 0
    reason_count = collections.Counter()
    port_count = collections.Counter()
    try:
        while True:
            if total_exits and total_exits % 500000 == 0:
                log(f"    [stats] {total_exits} exits: {dict(reason_count)} "
                    f"top: {port_count.most_common(5)}")
            if args.max_seconds and time.monotonic() - start > args.max_seconds:
                log(f"\n[lemu] 达到 --max-seconds={args.max_seconds}")
                break
            if args.max_seconds:
                signal_alarm(max(1, int(args.max_seconds - (time.monotonic() - start)) + 1))
            fcntl.ioctl(vcpu_fd, KVM_RUN, 0)
            reason = struct.unpack_from("<I", kvm_run, OFF_EXIT_REASON)[0]
            total_exits += 1

            if reason == KVM_EXIT_IO:
                direction, _, port, _, doff = struct.unpack_from("<BBHIQ", kvm_run, OFF_IO)
                reason_count["IO"] += 1
                port_count[f"0x{port:X}{'O' if direction else 'I'}"] += 1
                # KVM_EXIT_IO_IN = 0, KVM_EXIT_IO_OUT = 1（内核头文件定义，之前写反了！）
                if port == 0x61 and direction == 0:
                    # NMI/系统控制口：bit5 = PIT 通道2 输出（倒计时归零后=1）。
                    # 内核 TSC 校准轮询它，必须动态反映到期状态
                    kvm_run[doff] = 0x21 if (pit2["active"] and pit2["expired"]) else 0x01
                    continue
                if port == 0x42:
                    if direction == 1:                  # OUT：装载计数（先 LSB 后 MSB）
                        pit2_write(kvm_run[doff])
                    else:                               # IN：读当前剩余（LSB/MSB 交替）
                        rem = pit2["latched"] if pit2["latched"] is not None else pit2_remaining()
                        kvm_run[doff] = (rem & 0xFF) if pit2["phase"] == 0 else ((rem >> 8) & 0xFF)
                        pit2["phase"] ^= 1
                    continue
                if port == 0x43 and direction == 1:
                    cmd = kvm_run[doff]
                    sel = (cmd >> 6) & 3
                    rw = (cmd >> 4) & 3
                    if sel == 0 and rw != 0:
                        pit0_programmed[0] = True       # 客户机编程了 PIT 通道 0 → 允许 tick
                    if sel == 2:                        # 选中通道 2
                        if rw == 0:                     # 锁存命令：冻结当前计数
                            pit2["latched"] = pit2_remaining()
                        pit2["phase"] = 0               # 下次装载从 LSB 开始
                    continue
                if port == 0x40 and direction == 0:     # 通道 0 计数读：返回递减值近似
                    kvm_run[doff] = 0
                    continue
                if 0x3F8 <= port <= 0x3FF:
                    off = port - 0x3F8
                    if direction == 1:                  # OUT：客户机 → 设备
                        uart.write(off, kvm_run[doff])
                    else:                               # IN：设备 → 客户机
                        kvm_run[doff] = uart.read(off)
                    continue
                if port == 0x61 and direction == 0:
                    # NMI/系统控制口：bit5 = PIT 通道2 输出。TSC 校准轮询它等"倒计时结束"，
                    # 必须返回已到期（否则内核死等 49 万次——本 bug 的元凶）
                    kvm_run[doff] = 0x21                # bit0 gate=1 + bit5 timer2 OUT=1
                    continue
                if direction == 0:                      # IN：非串口端口回 0
                    kvm_run[doff] = 0
                if args.trace:
                    log(f"    [trace] IO port=0x{port:X} dir={direction}")
            elif reason == KVM_EXIT_HLT:
                time.sleep(0.001)
            elif reason == KVM_EXIT_SHUTDOWN:
                log(f"\n[lemu] 三重故障/关机（共 {total_exits} 次 exit）—— 转储现场：")
                dump_state("shutdown")
                rc = 1
                break
            elif reason == KVM_EXIT_FAIL_ENTRY:
                hw = struct.unpack_from("<Q", kvm_run, OFF_IO)[0]
                log(f"\n[lemu] VM-entry 失败：hw_reason=0x{hw:X}")
                rc = 1
                break
            elif reason == KVM_EXIT_INTERNAL_ERROR:
                sub = struct.unpack_from("<I", kvm_run, OFF_IO)[0]
                log(f"\n[lemu] KVM 内部错误 subtype={sub}")
                rc = 1
                break
            elif reason == KVM_EXIT_X86_RDMSR:
                # kvm_run.msr: error@32, index@40, data@48
                idx = struct.unpack_from("<I", kvm_run, OFF_IO + 8)[0]
                val = msr_read(idx, tsc_khz)
                struct.pack_into("<B", kvm_run, OFF_IO, 0)          # error=0
                struct.pack_into("<Q", kvm_run, OFF_IO + 16, val)
                if args.trace:
                    log(f"    [trace] RDMSR 0x{idx:X} -> 0x{val:X}")
            elif reason == KVM_EXIT_X86_WRMSR:
                struct.pack_into("<B", kvm_run, OFF_IO, 1)          # error=1 → 注入 #GP（忽略写）
            elif reason == KVM_EXIT_MMIO:
                addr = struct.unpack_from("<Q", kvm_run, OFF_IO)[0]   # addr @ union 基址
                data = bytes(kvm_run[OFF_IO + 8:OFF_IO + 16])
                length = struct.unpack_from("<I", kvm_run, OFF_IO + 16)[0]
                is_write = kvm_run[OFF_IO + 20]
                if netdev and 0xC0000000 <= addr < 0xC0000000 + 0x1000:
                    off = addr - 0xC0000000
                    if is_write:
                        val = int.from_bytes(data[:length], "little")
                        netdev.mmio_write(off, val)
                    else:
                        val = netdev.mmio_read(off)
                        kvm_run[OFF_IO + 8:OFF_IO + 8 + length] = \
                            val.to_bytes(length, "little")
                else:
                    log(f"\n[lemu] 未处理的 MMIO @ 0x{addr:X} write={is_write}")
            elif args.trace and reason != KVM_EXIT_INTR:
                log(f"    [trace] exit_reason={reason}")
    except KeyboardInterrupt:
        log("\n[lemu] Ctrl+C —— 关闭")
    except Timeout:
        log(f"\n[lemu] 超时/客户机停摆 —— 转储现场：")
        dump_state("timeout")
    finally:
        try:
            signal_alarm(0)
        except Exception:
            pass
        if saved_attrs is not None:
            import termios
            termios.tcsetattr(0, termios.TCSANOW, saved_attrs)
        logf.close()
        log(f"[lemu] 会话日志: {logf.name}")
    return rc


def signal_alarm(seconds):
    import signal
    def handler(signum, frame):
        raise Timeout()
    signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)


if __name__ == "__main__":
    sys.exit(main())
