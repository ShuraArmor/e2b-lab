#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modes.py —— x86 三种模式切换演示（裸机，无内核、无 ACPI、无中断芯片）

一台 vCPU，三幕实验，每幕重设 CPU 状态并装载对应的裸机小程序：
  第一幕  实模式(16位)    段:偏移寻址，把 'X' 写到物理 0x80000
  第二幕  保护模式(32位)  平铺 4G 数据段，同一地址写 'Y'（突破段式拼地址）
  第三幕  长模式(64位)    写 64 位立即数 0x1122334455667788（64 位寄存器/寻址）

宿主每幕结束后直接读客户机物理内存做校验——同一块 mmap，地址即证据。

用法：python3 vmm/modes.py   （无需参数，无需 root）
"""
import ctypes
import fcntl
import mmap
import os
import struct
import sys

# ---------------- KVM ioctl ----------------
KVM_GET_API_VERSION = 0xAE00
KVM_CREATE_VM = 0xAE01
KVM_CREATE_VCPU = 0xAE41
KVM_GET_VCPU_MMAP_SIZE = 0xAE04
KVM_SET_USER_MEMORY_REGION = 0x4020AE46
KVM_GET_SREGS = 0x8138AE83
KVM_SET_SREGS = 0x4138AE84
KVM_GET_REGS = 0x8090AE81
KVM_SET_REGS = 0x4090AE82
KVM_RUN = 0xAE80

# kvm_regs 字段偏移（18 个 u64）
OFF_RSP, OFF_RIP, OFF_RFLAGS = 48, 128, 136
# kvm_sregs 布局（x86_64，共 312 字节）
SEG_OFFS = {"cs": 0, "ds": 24, "es": 48, "fs": 72, "gs": 96, "ss": 120}
OFF_GDTR = 192          # kvm_dtable: base u64 + limit u16 + pad
OFF_CR0, OFF_CR2, OFF_CR3, OFF_CR4 = 224, 232, 240, 248
OFF_EFER = 264

MEM_SIZE = 1 << 20          # 1MB —— 实模式的极限，正好当"地址边界"教学
PROG_ADDR = 0x8000          # 每幕程序装载地址
TEST_ADDR = 0x80000         # 每幕的写入实验地址（512KB 处）
STACK_TOP = 0x90000         # 第二/三幕的栈顶
GDT_ADDR = 0x1000
PML4_ADDR = 0x2000          # 第三幕恒等映射：PML4@0x2000, PDPT@0x3000

KVM_EXIT_IO = 2
KVM_EXIT_HLT = 5
KVM_EXIT_SHUTDOWN = 8


def log(msg):
    print(msg, flush=True)


def set_seg(sregs, name, selector, base, limit, type_, db=0, l=0, g=0, s=1):
    """kvm_segment 24 字节：base(8) limit(4) selector(2)
    type present dpl db s l g avl (各1字节) 补齐(2)"""
    struct.pack_into("<QIH8B2x", sregs, SEG_OFFS[name],
                     base, limit, selector, type_, 1, 0, db, s, l, g, 0)


def main():
    kvm_fd = os.open("/dev/kvm", os.O_RDWR)
    assert fcntl.ioctl(kvm_fd, KVM_GET_API_VERSION, 0) == 12
    vm_fd = fcntl.ioctl(kvm_fd, KVM_CREATE_VM, 0)

    # 1MB 物理内存：故意只有 1MB，三种模式都够用，边界清晰
    gm = mmap.mmap(-1, MEM_SIZE, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
    buf = ctypes.create_string_buffer(32)
    struct.pack_into("<IIQQQ", buf, 0, 0, 0, 0, MEM_SIZE,
                     ctypes.addressof(ctypes.c_char.from_buffer(gm)))
    fcntl.ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, buf, True)

    vcpu_fd = fcntl.ioctl(vm_fd, KVM_CREATE_VCPU, 0)
    run = mmap.mmap(vcpu_fd, fcntl.ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0),
                    mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)

    def load_gdt(sregs, entries):
        """entries: 8 字节描述符原始值列表；写入 GDT 并设置 GDTR（第 0 项为 null）"""
        g = struct.pack("<Q", 0) + b"".join(struct.pack("<Q", e) for e in entries)
        gm[GDT_ADDR:GDT_ADDR + len(g)] = g
        struct.pack_into("<QH6x", sregs, OFF_GDTR, GDT_ADDR, len(g) - 1)

    def setup_mode(mode):
        sregs = ctypes.create_string_buffer(312)
        fcntl.ioctl(vcpu_fd, KVM_GET_SREGS, sregs, True)

        if mode == "real":
            # 实模式：CS.base=0 使 线性地址=RIP=0x8000；ES 运行时被程序改为 0x8000<<4
            set_seg(sregs, "cs", 0x0000, 0, 0xFFFF, 0xB)
            for n in ("ds", "es", "ss", "fs", "gs"):
                set_seg(sregs, n, 0x0000, 0, 0xFFFF, 0x3)
            struct.pack_into("<Q", sregs, OFF_CR0, 0x10)      # ET，PE=0

        elif mode == "pm32":
            # 平铺 4G 段：代码(0x08, DB=1) + 数据(0x10)，无分页
            load_gdt(sregs, [0x00CF9A000000FFFF, 0x00CF92000000FFFF])
            set_seg(sregs, "cs", 0x08, 0, 0xFFFFF, 0xB, db=1, g=1)
            for n in ("ds", "es", "ss", "fs", "gs"):
                set_seg(sregs, n, 0x10, 0, 0xFFFFF, 0x3, db=1, g=1)
            struct.pack_into("<Q", sregs, OFF_CR0, 0x11)      # PE|ET

        elif mode == "lm64":
            # 恒等映射 1GB：PML4[0] -> PDPT@0x3000, PDPT[0] = 1GB 页 (PS|RW|P)
            struct.pack_into("<Q", gm, PML4_ADDR, 0x3000 | 0x3)
            struct.pack_into("<Q", gm, 0x3000, 0x83)
            # 64 位段：代码 L=1；数据段在 64 位下仅要求"可用"
            load_gdt(sregs, [0x00209A0000000000, 0x0000920000000000])
            set_seg(sregs, "cs", 0x08, 0, 0xFFFFF, 0xB, l=1, g=1)
            for n in ("ds", "es", "ss", "fs", "gs"):
                set_seg(sregs, n, 0x10, 0, 0xFFFFF, 0x3, g=1)
            struct.pack_into("<Q", sregs, OFF_CR0, 0x80000011)   # PG|PE|ET
            struct.pack_into("<Q", sregs, OFF_CR3, PML4_ADDR)
            struct.pack_into("<Q", sregs, OFF_CR4, 0x20)         # PAE
            struct.pack_into("<Q", sregs, OFF_EFER, 0xD00)       # LME|LMA|NXE

        fcntl.ioctl(vcpu_fd, KVM_SET_SREGS, sregs, True)

        regs = ctypes.create_string_buffer(144)
        struct.pack_into("<18Q", regs, 0, *([0] * 18))
        struct.pack_into("<Q", regs, OFF_RSP, STACK_TOP)
        struct.pack_into("<Q", regs, OFF_RIP, PROG_ADDR)
        struct.pack_into("<Q", regs, OFF_RFLAGS, 0x2)
        fcntl.ioctl(vcpu_fd, KVM_SET_REGS, regs, True)

    def run_act(no, title, mode, prog, expect_desc, expect_check):
        log(f"\n{'=' * 58}")
        log(f"  第{no}幕  {title}")
        log(f"{'=' * 58}")
        gm[PROG_ADDR:PROG_ADDR + len(prog)] = prog
        setup_mode(mode)

        serial = bytearray()
        while True:
            fcntl.ioctl(vcpu_fd, KVM_RUN, 0)
            reason = struct.unpack_from("<I", run, 8)[0]
            if reason == KVM_EXIT_IO:
                d, _, port, _, doff = struct.unpack_from("<BBHIQ", run, 32)
                if d == 1 and port == 0x3F8:        # OUT 0x3F8 —— 串口输出
                    serial.append(run[doff])
                    continue
                if d == 0:                           # IN 未知端口 —— 给 0
                    run[doff] = 0
                continue
            if reason == KVM_EXIT_HLT:
                break                                # 程序正常停机
            if reason == KVM_EXIT_SHUTDOWN:
                regs = ctypes.create_string_buffer(144)
                fcntl.ioctl(vcpu_fd, KVM_GET_REGS, regs, True)
                rip = struct.unpack_from("<Q", regs, OFF_RIP)[0]
                log(f"  [!] 三重故障 RIP={rip:#x}（模式状态配错或程序越界）")
                break
            log(f"  [!] 意外退出 reason={reason}")
            break

        text = bytes(serial).decode("ascii", errors="replace")
        ok = expect_check()
        log(f"  串口输出 : {text!r}")
        log(f"  内存校验 : {expect_desc} -> {'✅ 通过' if ok else '❌ 失败'}")
        return ok

    def check_bytes(expected):
        return gm[TEST_ADDR:TEST_ADDR + len(expected)] == expected

    # ---------- 第一幕：实模式（16 位，段:偏移） ----------
    prog_real = bytes([
        0xBA, 0xF8, 0x03,             # mov dx, 0x3F8
        0xB0, 0x52, 0xEE,             # mov al,'R'; out
        0xB0, 0x4D, 0xEE,             # mov al,'M'; out
        0xB8, 0x00, 0x80,             # mov ax, 0x8000
        0x8E, 0xC0,                   # mov es, ax          -> ES.base = 0x80000
        0x26, 0xC6, 0x06, 0x00, 0x00, 0x58,   # mov byte [es:0], 'X'
        0xF4,                         # hlt
    ])
    ok1 = run_act("一", "实模式 (16 位) —— 段:偏移寻址", "real", prog_real,
                  "0x80000 == 'X'", lambda: check_bytes(b"X"))

    # ---------- 第二幕：保护模式（32 位，平铺 4G 段） ----------
    prog_pm32 = bytes([
        0xBA, 0xF8, 0x03, 0x00, 0x00, # mov edx, 0x3F8   (32位下 BA 吃 imm32!)
        0xB0, 0x50, 0xEE,             # mov al,'P'; out
        0xB0, 0x4D, 0xEE,             # mov al,'M'; out
        0xB8, 0x00, 0x00, 0x08, 0x00, # mov eax, 0x00080000 (直接 32 位线性地址)
        0xC6, 0x00, 0x59,             # mov byte [eax], 'Y'
        0xF4,                         # hlt
    ])
    ok2 = run_act("二", "保护模式 (32 位) —— 平铺 4G 段", "pm32", prog_pm32,
                  "0x80000 == 'Y'", lambda: check_bytes(b"Y"))

    # ---------- 第三幕：长模式（64 位，分页 + 64 位寄存器） ----------
    prog_lm64 = bytes([
        0xBA, 0xF8, 0x03, 0x00, 0x00, # mov edx, 0x3F8   (64位下同样 imm32!)
        0xB0, 0x4C, 0xEE,             # mov al,'L'; out
        0xB0, 0x4D, 0xEE,             # mov al,'M'; out
        0x48, 0xB8, 0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11,
                                      # movabs rax, 0x1122334455667788
        0x48, 0xA3, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00,
                                      # mov [0x80000], rax   (64 位一次写 8 字节)
        0xF4,                         # hlt
    ])
    ok3 = run_act("三", "长模式 (64 位) —— 64 位寄存器写入", "lm64", prog_lm64,
                  "0x80000..7 == 11 22 33 44 55 66 77 88",
                  lambda: check_bytes(bytes([0x88, 0x77, 0x66, 0x55,
                                             0x44, 0x33, 0x22, 0x11])))

    # ---------- 总结 ----------
    log(f"\n{'=' * 58}")
    log("  总结：同一块物理内存 0x80000，三种模式的写入实验")
    log(f"{'=' * 58}")
    rows = [("实模式 16 位", ok1, "ES:0 -> 段值<<4 + 偏移 拼 20 位地址"),
            ("保护模式 32 位", ok2, "平铺数据段，mov [eax] 直接给 32 位线性地址"),
            ("长模式 64 位", ok3, "恒等分页，一次写 64 位（8 字节）")]
    for name, ok, note in rows:
        log(f"  {'✅' if ok else '❌'} {name:<10} {note}")
    log("\n  模式改变的是'地址如何形成、有多大'，物理内存始终是同一块。")
    return 0 if all([ok1, ok2, ok3]) else 1


if __name__ == "__main__":
    sys.exit(main())
