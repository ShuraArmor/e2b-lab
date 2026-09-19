# -*- coding: utf-8 -*-
"""
kvm_minivm.py —— 世界上最小的"虚拟机管理器"之一，纯 Python + ioctl。

它做的事和 Firecracker/QEMU 的骨架完全一样，只是设备数量为 0、代码量为它们的万分之一：

    open("/dev/kvm")
      -> ioctl(KVM_CREATE_VM)                 造一台虚拟机
      -> ioctl(KVM_SET_USER_MEMORY_REGION)    把自己进程里的一块内存划给它当"物理内存"
      -> ioctl(KVM_CREATE_VCPU) + mmap kvm_run 造一个 vCPU，映射"通话记录本"
      -> ioctl(KVM_RUN) 循环                  点火！CPU 切进非根模式跑客户机代码，
                                              每次硬件 VM exit 返回，我们看一眼再放行

在 WSL2（Linux）里运行：python3 kvm_minivm.py
需要 /dev/kvm 权限（kvm 组或 root）。

"客户机"里跑的代码是我们手写的 x86 实模式机器码：向端口 0xE9 打印 "HI KVM"，然后 hlt 停机。
"""
import mmap
import os
import struct
import sys
import fcntl

# ---------- /dev/kvm 的 ioctl 命令号（来自内核头文件 linux/kvm.h） ----------
KVM_GET_API_VERSION = 0xAE00
KVM_CREATE_VM = 0xAE01
KVM_GET_VCPU_MMAP_SIZE = 0xAE04
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

# ---------- 客户机的"物理内存"：我们进程里的 1MB ----------
MEM_SIZE = 1 << 20

# 客户机代码：x86 实模式机器码（手工汇编）
#   mov al, '字符'  ->  B0 xx
#   out 0xE9, al    ->  E6 E9     （往调试端口写一个字节 = "打印"）
#   hlt             ->  F4        （停机，触发 KVM_EXIT_HLT）
def mov_out(c):
    return bytes([0xB0, ord(c), 0xE6, 0xE9])

GUEST_CODE = (mov_out("H") + mov_out("I") + mov_out(" ")
              + mov_out("K") + mov_out("V") + mov_out("M") + b"\xF4")

# ---------- 第 0 步：打开 KVM ----------
kvm_fd = os.open("/dev/kvm", os.O_RDWR)
api = fcntl.ioctl(kvm_fd, KVM_GET_API_VERSION, 0)
print(f"[1] /dev/kvm 已打开，KVM API 版本 = {api}")

# ---------- 第 1 句 ioctl：创建虚拟机 ----------
vm_fd = fcntl.ioctl(kvm_fd, KVM_CREATE_VM, 0)
print(f"[2] 虚拟机已创建（vm_fd = {vm_fd}）")
fcntl.ioctl(vm_fd, KVM_SET_TSS_ADDR, 0x40000000)  # x86 需要一块固定 TSS 地址（本 demo 用不到，避开那 1MB 客户机内存即可）

# ---------- 把宿主内存划给客户机当物理内存 ----------
guest_mem = mmap.mmap(-1, MEM_SIZE, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
guest_mem[0 : len(GUEST_CODE)] = GUEST_CODE  # 代码放在客户机物理地址 0 处
import ctypes

buf = ctypes.create_string_buffer(32)
struct.pack_into("<IIQQQ", buf, 0, 0, 0, 0, MEM_SIZE, ctypes.addressof(ctypes.c_char.from_buffer(guest_mem)))
fcntl.ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, buf, True)
print(f"[3] 宿主内存 0x{MEM_SIZE:X} 字节已划给客户机（EPT 从此由内核 KVM 接管）")

# ---------- 第 2 句 ioctl：创建 vCPU（它就是宿主里的一个线程） ----------
vcpu_fd = fcntl.ioctl(vm_fd, KVM_CREATE_VCPU, 0)
run_size = fcntl.ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0)
kvm_run = mmap.mmap(vcpu_fd, run_size, mmap.MAP_SHARED,
                    mmap.PROT_READ | mmap.PROT_WRITE)
print(f"[4] vCPU 已创建（vcpu_fd = {vcpu_fd}），kvm_run 共享页 {run_size} 字节已映射")

# ---------- 初始化 CPU 状态：实模式、CS 归零、RIP=0 ----------
sregs = ctypes.create_string_buffer(312)
fcntl.ioctl(vcpu_fd, KVM_GET_SREGS, sregs, True)
struct.pack_into("<Q", sregs, 0, 0)     # CS.base = 0（复位后默认 0xFFFF0000，改到 0）
struct.pack_into("<H", sregs, 12, 0)    # CS.selector = 0；其余属性保留 KVM 复位时的实模式值
fcntl.ioctl(vcpu_fd, KVM_SET_SREGS, sregs, True)

regs = ctypes.create_string_buffer(144)
struct.pack_into("<18Q", regs, 0, *([0] * 18))
struct.pack_into("<Q", regs, 128, 0)      # RIP = 0
struct.pack_into("<Q", regs, 136, 0x2)    # RFLAGS = 0x2（bit1 恒为 1）
fcntl.ioctl(vcpu_fd, KVM_SET_REGS, regs, True)
print("[5] CPU 状态就绪：实模式，从客户机物理地址 0 开始执行")

# ---------- 第 3 句 ioctl：KVM_RUN 循环 —— 虚拟化的心脏 ----------
print("[6] 点火！以下输出来自'客户机'（CPU 正在非根模式执行我们的机器码）：")
print("    ┌─────────────────────────")
for _ in range(100):
    fcntl.ioctl(vcpu_fd, KVM_RUN, 0)
    exit_reason = struct.unpack_from("<I", kvm_run, 8)[0]  # kvm_run.exit_reason

    if exit_reason == KVM_EXIT_IO:
        # io 字段真实偏移由 gcc offsetof 实测（x86_64, 内核 6.6）：io 在 kvm_run+32
        direction, size, port, count, data_off = struct.unpack_from("<BBHIQ", kvm_run, 32)
        value = kvm_run[data_off]
        print(f"    │ VM exit: 客户机向端口 0x{port:X} 输出字符 '{chr(value)}' —— 放行，继续跑")
    elif exit_reason == KVM_EXIT_HLT:
        print("    │ VM exit: 客户机执行 hlt 停机")
        print("    └─────────────────────────")
        break
    else:
        print(f"    │ 意外退出，exit_reason = {exit_reason}")
        print("    └─────────────────────────")
        break

regs = ctypes.create_string_buffer(144)
fcntl.ioctl(vcpu_fd, KVM_GET_REGS, regs, True)
rip = struct.unpack_from("<Q", regs, 128)[0]
print(f"\n[7] 收工。客户机停在 RIP = 0x{rip:X}；它从头到尾不知道自己住在 {sys.platform} 的一块 mmap 里。")
