#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vm_logger.py —— 让一台 KVM 虚拟机"写日志"到宿主机的磁盘上。

三个知识点的实物演示：
 1. 内存里塞的是 x86 实模式机器码（KVM 不解析内容，CPU 模式匹配就能执行）；
 2. 客户机没有真设备，它 `out 0x3F8`（往"串口"写字节）触发 VM exit，
    控制权回到本脚本 —— 这就是"VM 连接宿主机 IO"的唯一方式：你模拟设备；
 3. 宿主机侧截获每个字节、盖时间戳、追加写进 vm_output.log ——
    你磁盘上的这个文件就是"VM 运行结果"的实物证据。

运行（WSL2）：python3 vm_logger.py
"""
import ctypes
import fcntl
import mmap
import os
import struct
import time
from datetime import datetime

# ---- KVM ioctl（linux/kvm.h，编号已在本机验证）----
KVM_CREATE_VM = 0xAE01
KVM_CREATE_VCPU = 0xAE41
KVM_SET_USER_MEMORY_REGION = 0x4020AE46
KVM_SET_TSS_ADDR = 0xAE47
KVM_GET_SREGS = 0x8138AE83
KVM_SET_SREGS = 0x4138AE84
KVM_SET_REGS = 0x4090AE82
KVM_GET_VCPU_MMAP_SIZE = 0xAE04
KVM_RUN = 0xAE80

# ---- 塞进虚拟机内存的代码：x86 实模式机器码，A→Z 循环往串口打字 ----
GUEST = bytes([
    0xB0, 0x41,     # mov al, 'A'
    0xBA, 0xF8, 0x03,  # mov dx, 0x3F8      (串口数据端口)
    0xEE,           # out dx, al          (触发 VM exit，宿主截获)
    0xFE, 0xC0,     # inc al
    0x3C, 0x5B,     # cmp al, '['         (超过 'Z' 了吗)
    0x75, 0x02,     # jnz +2
    0xB0, 0x41,     # mov al, 'A'         (回到 'A')
    0xEB, 0xF2,     # jmp 循环顶
])

HERE = os.path.dirname(os.path.abspath(__file__))            # .../kvm-demo/vmm
ROOT = os.path.dirname(HERE)                                  # .../kvm-demo
os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
LOG = os.path.join(ROOT, "logs", "vm_output.log")
MAX_EVENTS = 26 * 3          # 捕获 3 轮 A-Z
MAX_SECONDS = 10

# ---- 造机器（和 kvm_minivm.py 相同的六步）----
kvm_fd = os.open("/dev/kvm", os.O_RDWR)
vm_fd = fcntl.ioctl(kvm_fd, KVM_CREATE_VM, 0)
MEM = 1 << 20
gm = mmap.mmap(-1, MEM, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
gm[0:len(GUEST)] = GUEST
buf = ctypes.create_string_buffer(32)
struct.pack_into("<IIQQQ", buf, 0, 0, 0, 0, MEM,
                 ctypes.addressof(ctypes.c_char.from_buffer(gm)))
fcntl.ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, buf, True)
fcntl.ioctl(vm_fd, KVM_SET_TSS_ADDR, 0x40000000)
vcpu_fd = fcntl.ioctl(vm_fd, KVM_CREATE_VCPU, 0)
run = mmap.mmap(vcpu_fd, fcntl.ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0),
                mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
sregs = ctypes.create_string_buffer(312)
fcntl.ioctl(vcpu_fd, KVM_GET_SREGS, sregs, True)
struct.pack_into("<Q", sregs, 0, 0)      # CS.base = 0
struct.pack_into("<H", sregs, 12, 0)     # CS.selector = 0
fcntl.ioctl(vcpu_fd, KVM_SET_SREGS, sregs, True)
regs = ctypes.create_string_buffer(144)
struct.pack_into("<18Q", regs, 0, *([0] * 18))
fcntl.ioctl(vcpu_fd, KVM_SET_REGS, regs, True)

# ---- 通电，捕获输出，落盘 ----
logf = open(LOG, "a", buffering=1)
def w(line):
    logf.write(line + "\n")

w(f"===== VMM 启动 {datetime.now():%Y-%m-%d %H:%M:%S} =====")
print(f"虚拟机已启动，正在捕获它的输出 -> {LOG}")
n = exits = 0
t0 = time.time()
try:
    while n < MAX_EVENTS and time.time() - t0 < MAX_SECONDS:
        fcntl.ioctl(vcpu_fd, KVM_RUN, 0)
        exits += 1
        reason = struct.unpack_from("<I", run, 8)[0]
        if reason == 2:                                   # KVM_EXIT_IO
            _, _, port, _, doff = struct.unpack_from("<BBHIQ", run, 32)
            if port == 0x3F8:
                ch = chr(run[doff])
                n += 1
                line = (f"{datetime.now():%H:%M:%S.%f}  "
                        f"VM输出: '{ch}'   (第 {exits} 次 VM exit)")
                w(line)
                print(line)
        else:
            w(f"意外退出，exit_reason = {reason}")
            break
finally:
    w(f"===== VMM 关闭：共 {exits} 次 VM exit，捕获 {n} 字节 =====")
    logf.close()
    print(f"\n完成。去看磁盘上的日志文件：{LOG}")
