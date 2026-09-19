#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kvm_linux.py —— "稍微完整功能"的 KVM VMM：引导一个真的 Linux 内核，串口交互。

在 kvm_minivm.py（三句 ioctl 骨架）之上新增三大件：
  1. Linux 32 位引导协议：直接把 bzImage 装进内存，构造 zero page（启动参数 +
     E820 内存表 + cmdline + initrd 位置）和扁平 GDT，CPU 从内核 32 位入口点火。
     —— 这就是 QEMU -kernel / Firecracker 的"直接内核引导"。
  2. KVM_CREATE_IRQCHIP：中断控制器（PIC/PIT）由内核态模拟 —— 时钟 tick 白送，
     不用我们自己写定时器设备。
  3. 16550 UART 模拟（端口 0x3F8-0x3FF）：内核用 console=ttyS0 把启动日志和
     shell 都放到串口上。客户机 OUT 0x3F8 = 输出；我们往 0x3F8 塞字节 = 键盘输入；
     有输入时拉 IRQ4 通知客户机。你在宿主机敲命令，shell 在虚拟机里执行。

运行（WSL2，需要 root 或 kvm 组）：
  printf 'uname -a\nls /\n' | python3 kvm_linux.py --kernel bzImage-wsl \
      --initrd initramfs.cpio.gz --mem 256 --max-seconds 120
交互模式（不加管道）直接进入"虚拟机串口终端"，Ctrl+C 退出。
"""
import argparse
import ctypes
import fcntl
import mmap
import os
import select
import struct
import sys
import time

# ---------------- ioctl 命令号（linux/kvm.h） ----------------
KVM_GET_API_VERSION = 0xAE00
KVM_CREATE_VM = 0xAE01
KVM_GET_VCPU_MMAP_SIZE = 0xAE04
KVM_CREATE_IRQCHIP = 0xAE60          # 新增：内核态 PIC/PIT/IOAPIC/LAPIC
KVM_IRQ_LINE = 0x4008AE61            # 新增：给 ISA 中断线（如串口 IRQ4）拉电平
                                     # （编号以 /usr/include/linux/kvm.h 为准：0x61，不是 0x67）
KVM_CREATE_VCPU = 0xAE41
KVM_SET_USER_MEMORY_REGION = 0x4020AE46
KVM_SET_TSS_ADDR = 0xAE47
KVM_GET_SREGS = 0x8138AE83
KVM_SET_SREGS = 0x4138AE84
KVM_GET_REGS = 0x8090AE81
KVM_SET_REGS = 0x4090AE82
KVM_RUN = 0xAE80

KVM_EXIT_IO = 2
KVM_EXIT_HLT = 5
KVM_EXIT_MMIO = 6
KVM_EXIT_SHUTDOWN = 8
KVM_EXIT_FAIL_ENTRY = 9
KVM_EXIT_INTERNAL_ERROR = 17

# kvm_run 结构偏移（gcc offsetof 在 x86_64 内核 6.6 上实测）
OFF_EXIT_REASON = 8
OFF_IO = 32

# ---------------- 客户机物理内存布局 ----------------
GDT_ADDR = 0x1000
ZP_ADDR = 0x10000        # boot_params（zero page）
CMDLINE_ADDR = 0x20000
KERNEL_ADDR = 0x100000   # 32 位内核入口约定位置


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ================= 引导协议部分 =================

def build_boot_state(guest_mem, mem_size, kernel, cmdline, initrd):
    """按 Linux boot protocol（32 位入口）布置 zero page / E820 / GDT / initrd。"""
    if kernel[0x202:0x206] != b"HdrS":
        sys.exit("不是有效的 bzImage（缺少 HdrS 魔数）")
    setup_sects = kernel[0x1F1] or 4
    pm_off = (setup_sects + 1) * 512
    code32_start = struct.unpack_from("<I", kernel, 0x214)[0] or 0x100000

    # 1) 保护模式内核装到 1MB 处
    guest_mem[code32_start : code32_start + len(kernel) - pm_off] = kernel[pm_off:]

    # 2) initrd 放到内存顶部（页对齐，协议要求尽量高）
    initrd_addr = (mem_size - len(initrd)) & ~0xFFF
    guest_mem[initrd_addr : initrd_addr + len(initrd)] = initrd

    # 3) cmdline
    guest_mem[CMDLINE_ADDR : CMDLINE_ADDR + len(cmdline)] = cmdline

    # 4) zero page：复制内核自带的 setup_header，再填引导器字段
    zp = bytearray(mem_size and 4096)
    zp[0x1F1:0x270] = kernel[0x1F1:0x270]
    zp[0x210] = 0xFF                                   # type_of_loader：未定义引导器
    zp[0x211] |= 0x01                                  # LOADED_HIGH（不设 KEEP_SEGMENTS，
                                                       #  让内核用自己的 boot_gdt 重载段——
                                                       #  这是 QEMU/kvmtool 验证过的路径）
    struct.pack_into("<I", zp, 0x214, code32_start)    # code32_start
    struct.pack_into("<I", zp, 0x218, initrd_addr)     # ramdisk_image
    struct.pack_into("<I", zp, 0x21C, len(initrd))     # ramdisk_size
    struct.pack_into("<I", zp, 0x228, CMDLINE_ADDR)    # cmd_line_ptr
    # E820 内存表（偏移 0x1E8 是条目数，0x2D0 起是表）
    e820 = [(0x0, 0x9F000, 1), (0x9F000, 0x100000 - 0x9F000, 2),
            (0x100000, mem_size - 0x100000, 1)]
    zp[0x1E8] = len(e820)
    for i, (base, size, typ) in enumerate(e820):
        struct.pack_into("<QQI", zp, 0x2D0 + 20 * i, base, size, typ)
    guest_mem[ZP_ADDR : ZP_ADDR + 4096] = zp

    # 5) 扁平 GDT：0x08 = 4G 代码段，0x10 = 4G 数据段（KEEP_SEGMENTS 模式内核直接沿用）
    gdt = bytes.fromhex("0000000000000000"      # null
                        "FFFF0000009BCF00"      # 0x08 code, base=0 limit=4G, 32bit
                        "FFFF00000093CF00")     # 0x10 data
    guest_mem[GDT_ADDR : GDT_ADDR + len(gdt)] = gdt

    log(f"    内核 {len(kernel)>>20}MB 装载于 0x{code32_start:X}，"
        f"initrd {len(initrd)>>10}KB @ 0x{initrd_addr:X}")
    return code32_start


def setup_cpu(vm_fd, vcpu_fd, entry_addr):
    """设置 vCPU 的段寄存器（指向我们的 GDT）与入口状态：保护模式、关分页。"""
    sregs = ctypes.create_string_buffer(312)
    fcntl.ioctl(vcpu_fd, KVM_GET_SREGS, sregs, True)

    def seg(off, selector, type_, base=0, limit=0xFFFFF, db=1, g=1, s=1):
        # kvm_segment 布局: base(8) limit(4) selector(2) 8个标志位(8) 补齐(2)
        struct.pack_into("<QIH8B2x", sregs, off,
                         base, limit, selector, type_, 1, 0, db, s, 0, g, 0)

    seg(0, 0x08, 0x9B)    # CS
    for off in (24, 48, 72, 96, 120):                  # DS ES FS GS SS
        seg(off, 0x10, 0x93)
    # TR：VMX 硬件检查要求 type 为 3（可用 TSS）或 11（忙碌 TSS），复位值常不合规
    seg(144, 0, 0xB, limit=0xFFFF, db=0, g=0, s=0)     # TR = 忙碌 32 位 TSS
    struct.pack_into("<QH6x", sregs, 192, GDT_ADDR, 0x17)   # GDTR
    struct.pack_into("<Q", sregs, 224, 0x33)                # CR0 = PE|MP|ET|NE（无分页）
    struct.pack_into("<Q", sregs, 240, 0)                   # CR3
    struct.pack_into("<Q", sregs, 248, 0)                   # CR4
    struct.pack_into("<Q", sregs, 264, 0)                   # EFER
    fcntl.ioctl(vcpu_fd, KVM_SET_SREGS, sregs, True)

    regs = ctypes.create_string_buffer(144)
    struct.pack_into("<18Q", regs, 0, *([0] * 18))
    struct.pack_into("<Q", regs, 32, ZP_ADDR)    # RSI = boot_params 地址（32 位入口约定）
    struct.pack_into("<Q", regs, 128, entry_addr)  # RIP
    struct.pack_into("<Q", regs, 136, 0x2)         # RFLAGS
    fcntl.ioctl(vcpu_fd, KVM_SET_REGS, regs, True)


# ================= 16550 UART 模拟 =================

class Uart:
    """最小 16550：只服务 console=ttyS0。OUT 0x3F8=打印；IN=喂输入；输入触发 IRQ4。"""

    def __init__(self, vm_fd, guest_out):
        self.vm_fd = vm_fd
        self.guest_out = guest_out          # 回调：把客户机输出的字节写到宿主 stdout
        self.rx = bytearray()               # 待交给客户机的输入
        self.irq_level = 0
        self.dlab = 0
        self.ier = 0
        self.lcr = 0
        self.mcr = 0
        self.fcr = 0
        self.scratch = 0
        self.dll = 12                       # 除数默认 115200 波特
        self.dlm = 0
        self.last_thr = 0
        self.rx_int_pending = False

    def _irq(self, level):
        if level != self.irq_level:
            self.irq_level = level
            buf = ctypes.create_string_buffer(8)
            struct.pack_into("<II", buf, 0, 4, level)   # ISA IRQ4 = COM1
            fcntl.ioctl(self.vm_fd, KVM_IRQ_LINE, buf, True)

    def push_input(self, data):
        self.rx.extend(data)
        self.rx_int_pending = True
        self._irq(1)

    def read(self, off):
        if self.dlab and off == 0:
            return self.dll
        if self.dlab and off == 1:
            return self.dlm
        if off == 0:                                    # RBR
            if self.mcr & 0x10:                         # loopback 模式（8250 探测用）
                return self.last_thr
            if self.rx:
                b = self.rx.pop(0)
                if not self.rx:
                    self.rx_int_pending = False
                    self._irq(0)
                return b
            return 0
        if off == 1:
            return self.ier
        if off == 2:                                    # IIR
            if self.rx and self.rx_int_pending:
                return 0x04 | (0xC0 if self.fcr & 1 else 0)  # 收到数据中断
            return 0x01 | (0xC0 if self.fcr & 1 else 0)      # 无中断挂起
        if off == 3:
            return self.lcr
        if off == 4:
            return self.mcr
        if off == 5:                                    # LSR：收满位 + 发空位
            return (0x60 | (0x01 if self.rx else 0)) & 0xFF
        if off == 6:
            return 0xB0                                 # MSR：CTS|DSR|DCD 一直在线
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
        if off == 0:                                    # THR
            if self.mcr & 0x10:                         # loopback：自发自收
                self.last_thr = val
                return
            self.guest_out(bytes([val]))
        elif off == 1:
            self.ier = val
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--initrd", required=True)
    ap.add_argument("--mem", type=int, default=256, help="内存 MiB（默认 256）")
    ap.add_argument("--max-seconds", type=int, default=0, help="0 = 不限时（交互模式）")
    args = ap.parse_args()

    mem_size = args.mem << 20
    kernel = open(args.kernel, "rb").read()
    initrd = open(args.initrd, "rb").read()
    cmdline = (b"console=ttyS0,115200n8 earlyprintk=serial,ttyS0,115200 "
               b"rdinit=/init nokaslr")

    # ---- KVM 三件套 + 中断芯片 ----
    kvm_fd = os.open("/dev/kvm", os.O_RDWR)
    assert fcntl.ioctl(kvm_fd, KVM_GET_API_VERSION, 0) == 12
    vm_fd = fcntl.ioctl(kvm_fd, KVM_CREATE_VM, 0)

    guest_mem = mmap.mmap(-1, mem_size, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
    buf = ctypes.create_string_buffer(32)
    struct.pack_into("<IIQQQ", buf, 0, 0, 0, 0, mem_size,
                     ctypes.addressof(ctypes.c_char.from_buffer(guest_mem)))
    fcntl.ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, buf, True)

    fcntl.ioctl(vm_fd, KVM_CREATE_IRQCHIP, 0)     # 内核态 PIC/PIT：时钟中断免费
    fcntl.ioctl(vm_fd, KVM_SET_TSS_ADDR, 0x40000000)

    log("[*] 布置引导状态（32 位引导协议）...")
    build_boot_state(guest_mem, mem_size, kernel, cmdline, initrd)

    vcpu_fd = fcntl.ioctl(vm_fd, KVM_CREATE_VCPU, 0)
    run_size = fcntl.ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0)
    kvm_run = mmap.mmap(vcpu_fd, run_size, mmap.MAP_SHARED,
                        mmap.PROT_READ | mmap.PROT_WRITE)

    setup_cpu(vm_fd, vcpu_fd, KERNEL_ADDR)

    # ---- 交互输入：tty 进裸模式；管道按字节泵 ----
    saved_attrs = None
    if sys.stdin.isatty():
        import termios
        import tty
        saved_attrs = termios.tcgetattr(0)
        tty.setraw(0)                       # 逐键直通，不缓冲不回显（回显由客户机 tty 负责）
    stdin_dead = False

    def pump_stdin():
        nonlocal stdin_dead
        if stdin_dead:
            return
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            data = os.read(0, 4096)
            if data:
                uart.push_input(data)
            else:
                stdin_dead = True   # 管道关闭（EOF）不算错误

    out_buf = sys.stdout.buffer

    def guest_print(data):
        out_buf.write(data)
        out_buf.flush()

    uart = Uart(vm_fd, guest_print)

    log("[*] 点火！客户机输出如下（串口 ttyS0）：")
    log("-" * 60)
    start = time.monotonic()
    try:
        while True:
            if args.max_seconds and time.monotonic() - start > args.max_seconds:
                log(f"\n[vmm] 达到 --max-seconds={args.max_seconds}，关闭虚拟机")
                return 0
            pump_stdin()
            fcntl.ioctl(vcpu_fd, KVM_RUN, 0)
            reason = struct.unpack_from("<I", kvm_run, OFF_EXIT_REASON)[0]

            if reason == KVM_EXIT_IO:
                direction, _, port, _, data_off = struct.unpack_from("<BBHIQ", kvm_run, OFF_IO)
                if 0x3F8 <= port <= 0x3FF:
                    off = port - 0x3F8
                    if direction == 0:                  # OUT：客户机 -> 设备
                        uart.write(off, kvm_run[data_off])
                    else:                               # IN：设备 -> 客户机
                        kvm_run[data_off] = uart.read(off)
                    continue
                # 其余端口：没模拟的设备，IN 回 0、OUT 忽略
                if direction == 1:
                    kvm_run[data_off] = 0
            elif reason == KVM_EXIT_HLT:
                time.sleep(0.001)                       # 客户机空转，让出 CPU
            elif reason == KVM_EXIT_SHUTDOWN:
                log("\n[vmm] 客户机三重故障/重启（shell 退出后内核 panic 属正常现象）")
                return 0
            elif reason == KVM_EXIT_FAIL_ENTRY:
                log(f"\n[vmm] 进入客户机失败：hw_reason=0x"
                    f"{struct.unpack_from('<Q', kvm_run, OFF_IO)[0]:X}")
                return 1
            elif reason == KVM_EXIT_INTERNAL_ERROR:
                log(f"\n[vmm] KVM 内部错误：subtype="
                    f"{struct.unpack_from('<I', kvm_run, OFF_IO)[0]}")
                return 1
            elif reason == KVM_EXIT_MMIO:
                pass                                    # 不该发生，忽略
    except KeyboardInterrupt:
        log("\n[vmm] 收到 Ctrl+C，关闭虚拟机")
        return 0
    finally:
        if saved_attrs is not None:
            import termios
            termios.tcsetattr(0, termios.TCSANOW, saved_attrs)


if __name__ == "__main__":
    sys.exit(main())
