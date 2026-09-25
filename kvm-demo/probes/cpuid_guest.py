#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe: 让 vCPU 在实模式执行 CPUID(0xD, 0/1/2)，结果写物理内存，宿主读回。
回答的问题：KVM 的 CPUID 出口到底把我们的子叶表服务成什么样。"""
import ctypes, fcntl, mmap, os, struct

KVM_CREATE_VM, KVM_CREATE_VCPU = 0xAE01, 0xAE41
KVM_GET_VCPU_MMAP_SIZE = 0xAE04
KVM_SET_USER_MEMORY_REGION = 0x4020AE46
KVM_GET_SREGS, KVM_SET_SREGS = 0x8138AE83, 0x4138AE84
KVM_GET_REGS, KVM_SET_REGS = 0x8090AE81, 0x4090AE82
KVM_RUN = 0xAE80
KVM_SET_CPUID2 = 0x4008AE90

MEM = 1 << 16
kvm_fd = os.open("/dev/kvm", os.O_RDWR)
vm_fd = fcntl.ioctl(kvm_fd, KVM_CREATE_VM, 0)
gm = mmap.mmap(-1, MEM, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
buf = ctypes.create_string_buffer(32)
struct.pack_into("<IIQQQ", buf, 0, 0, 0, 0, MEM,
                 ctypes.addressof(ctypes.c_char.from_buffer(gm)))
fcntl.ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, buf, True)
vcpu_fd = fcntl.ioctl(vm_fd, KVM_CREATE_VCPU, 0)
run = mmap.mmap(vcpu_fd, fcntl.ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0),
                mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)

# ---- 与 lemu.py 完全一致的 0xD 子叶表（flags=SIGNIFCANT_INDEX）----
entries = [
    (0x0, 0, 0x16, 0, 0, 0),
    (0xD, 0, 0x7, 0x340, 0x340, 0),
    (0xD, 1, 0x1, 0x340, 0, 0),
    (0xD, 2, 0x100, 0x240, 0, 0),
]
cpuid_buf = ctypes.create_string_buffer(8 + len(entries) * 40)
struct.pack_into("<I", cpuid_buf, 0, len(entries))
for i, (fn, sub, eax, ebx, ecx, edx) in enumerate(entries):
    flags = 0x2 if fn == 0xD else 0
    struct.pack_into("<IIIIIIII", cpuid_buf, 8 + i * 40,
                     fn, sub, flags, eax, ebx, ecx, edx, 0)
fcntl.ioctl(vcpu_fd, KVM_SET_CPUID2, cpuid_buf, True)

# ---- 实模式小程序：三次 CPUID，结果写 0x400/0x410/0x420 ----
prog = bytes([
    0xB8, 0x0D, 0x00,             # mov ax, 0x000D   (imm16 小端!)
    0xB9, 0x00, 0x00,             # mov cx, 0
    0x0F, 0xA2,                   # cpuid
    0x66, 0xA3, 0x00, 0x04,       # mov [0x400], eax
    0x66, 0x89, 0x1E, 0x04, 0x04, # mov [0x404], ebx   (89 /r, modrm 1E)
    0x66, 0x89, 0x0E, 0x08, 0x04, # mov [0x408], ecx   (modrm 0E)
    0x66, 0x89, 0x16, 0x0C, 0x04, # mov [0x40C], edx   (modrm 16)
    0xB8, 0x0D, 0x00,             # mov ax, 0x000D
    0xB9, 0x01, 0x00,             # mov cx, 1
    0x0F, 0xA2,                   # cpuid
    0x66, 0xA3, 0x10, 0x04,       # mov [0x410], eax
    0x66, 0x89, 0x1E, 0x14, 0x04, # mov [0x414], ebx
    0x66, 0x89, 0x0E, 0x18, 0x04, # mov [0x418], ecx
    0x66, 0x89, 0x16, 0x1C, 0x04, # mov [0x41C], edx
    0xB8, 0x0D, 0x00,             # mov ax, 0x000D
    0xB9, 0x02, 0x00,             # mov cx, 2
    0x0F, 0xA2,                   # cpuid
    0x66, 0xA3, 0x20, 0x04,       # mov [0x420], eax
    0x66, 0x89, 0x1E, 0x24, 0x04, # mov [0x424], ebx
    0x66, 0x89, 0x0E, 0x28, 0x04, # mov [0x428], ecx
    0x66, 0x89, 0x16, 0x2C, 0x04, # mov [0x42C], edx
    0xF4,                         # hlt
])
gm[0x8000:0x8000 + len(prog)] = prog

# ---- 实模式状态（同 modes.py 第一幕）----
sregs = ctypes.create_string_buffer(312)
fcntl.ioctl(vcpu_fd, KVM_GET_SREGS, sregs, True)
# base(8) limit(4) selector(2) type present dpl db s l g avl
for off in (24, 48, 72, 96, 120):                      # 数据段
    struct.pack_into("<QIH8B2x", sregs, off,
                     0, 0xFFFF, 0, 0x3, 1, 0, 0, 1, 0, 0, 0)
struct.pack_into("<QIH8B2x", sregs, 0,                 # CS
                 0, 0xFFFF, 0, 0xB, 1, 0, 0, 1, 0, 0, 0)
struct.pack_into("<Q", sregs, 224, 0x10)
fcntl.ioctl(vcpu_fd, KVM_SET_SREGS, sregs, True)
regs = ctypes.create_string_buffer(144)
struct.pack_into("<18Q", regs, 0, *([0] * 18))
struct.pack_into("<Q", regs, 128, 0x8000)     # RIP
struct.pack_into("<Q", regs, 136, 0x2)        # RFLAGS
fcntl.ioctl(vcpu_fd, KVM_SET_REGS, regs, True)

fcntl.ioctl(vcpu_fd, KVM_RUN, 0)
reason = struct.unpack_from("<I", run, 8)[0]
print(f"退出原因: {reason} (5=HLT 正常)")

def get(base):
    return struct.unpack_from("<4I", gm, base)

names = ["0xD.0", "0xD.1", "0xD.2"]
for i, nm in enumerate(names):
    eax, ebx, ecx, edx = get(0x400 + i * 16)
    print(f"客户机看到的 {nm}: EAX={eax:#010x} EBX={ebx:#010x} "
          f"ECX={ecx:#010x} EDX={edx:#010x}")
