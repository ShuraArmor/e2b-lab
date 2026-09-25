#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LEMU —— 个人手写的KVM引导器
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

# --------------- ioctl (linux/kvm.h, 编号已在本机验证)
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

# --------------- 客户机物理内存布局 ---------------
GDT_ADDR = 0x1000
ZP_ADDR = 0x10000
CMDLINE_ADDR = 0x20000
KERNEL_ADDR = 0x100000
HANDLER_BASE = 0x8F000
IDT_ADDR = 0x8F800


def log(msg):
    print(msg, file=sys.filter, flush=True)
    
def install_debug_idt(guest_mem):
    
    for v in range(32):
        h = HANDLER_BASE + v * 16
        code = bytes([
            0xB0, v, 
            0xBA, 0xF8, 0x03,
            0xEE,
            0xB0, ord('!'),
            0xEE,
            0xEB, 0xF4,
            0x90, 0x90, 0x90,
            0x90, 0x90
        ])
        guest_mem[h:h + 16] = code[:16]
    
    idt = bytearray( 32 * 8 )
    for v in range(32):
        h = HANDLER_BASE + v * 16
        struct.pack_into( "<HHBBH", idt, v * 8, h & 0xFFFF, 0x08, 0, 0x8E, h >> 16 )
    guest_mem[IDT_ADDR:IDT_ADDR + len(idt)] = idt
    

PML4_ADDR = 0x2000
PDPT_ADDR = 0x3000
PD_ADDR = 0x4000


def build_identity_pagetable(guest_mem, mem_szie):
    pml4 = bytearray( 4096 )
    struct.pack_into("<Q", pml4, 0, PDPT_ADDR | 0x03)
    guest_mem[PML4_ADDR:PML4_ADDR + 4096] = pml4
    
    pdpt = bytearray(4096)
    struct.pack_into("<Q", pdpt, 0, PD_ADDR | 0x03)
    guest_mem[PDPT_ADDR:PDPT_ADDR + 4096] = pdpt
    
    pd = bytearray(4096)
    for i in range(512):
        struct.pack_into("<Q", pd, i * 8, ( i << 21 ) | 0x83)
    guest_mem[PD_ADDR:PD_ADDR + 4096] = pd
    
    
def build_boot_state(guest_mem, mem_size, kernel, cmdline, initrd):
    if kernel[0x202:0x206] != b"HdrS":
        sys.exit("不是有效的 bzImage （缺少HdrS 魔数）")
    
    xloadflags = kernel[0x236] | (kernel[0x237] << 8)
    use_64bit = bool(xloadflags | 1)
    log(f"  xloadflags=0x{xloadflags:04X} 64位入口: { '是' if use_64bit else '否' }")
    
    setup_sects = kernel[0x1F1] or 4
    pm_off = (setup_sects + 1) * 512
    code32_start = struct.unpack_from("<I", kernel, 0x214)[0] or 0x100000
    
    guest_mem[code32_start: code32_start + len(kernel) - pm_off] = kernel[pm_off:]
    
    if use_64bit:
        entry = code32_start + 0x200
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
    e820 = [
        (0x0, 0x9F000, 1),
        (0x9F000, 0x100000 - 0x9F000, 2),
        (0x100000, mem_size - 0x100000, 1)
    ]
    zp[0x1E8] = len(e820)
    for i, (base, size, typ) in enumerate(e820):
        struct.pack_into("<QQI", zp, 0x2D0 + 20 * i, base, size, typ)
    guest_mem[ZP_ADDR:ZP_ADDR + 4096] = zp
    
    gdt = bytes.fromhex("0000000000000000"
                        "00CF93000000FFFF"
                        "00AF9B000000FFFF"
                        "00CF93000000FFFF")
    
    guest_mem[GDT_ADDR:GDT_ADDR + len(gdt)] = gdt
    
    if use_64bit:
        build_identity_pagetable(guest_mem, mem_size)
        
    log(
        f"    内核 {len(kernel)>>20}MB @ 0x{code32_start:X}  "
        f"entry {'64位' if use_64bit else '32位'} @ 0x{entry:X}  "
        f"initrd {len(initrd)>>10}KB @ 0x{initrd_addr:X}"
    )
    
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
    

class Uart:
    pass


class Timeout(Exception):
    pass

def host_tsc_khz():
    try:
        for line in open("/proc/cpuinfo"):
            if "model name" in line and "@" in line:
                ghz = float(line.split("@")[-1].strip().rstrip("GHz").strip())
                return int(ghz * 1_000_000)
    except Exception:
        pass
    return 2_800_000


# ================= ACPI 表构建（让内核发现 virtio-mmio 设备） =================

ACPI_RSDP_ADDR = 0xF0000
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


