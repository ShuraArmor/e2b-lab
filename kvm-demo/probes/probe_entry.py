#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM-entry 状态隔离测试：不引导内核，只让 CPU 在客户机模式执行 8 字节桩代码。"""
import ctypes, fcntl, mmap, os, struct

kvm_fd = os.open("/dev/kvm", os.O_RDWR)
vm_fd = fcntl.ioctl(kvm_fd, 0xAE01, 0)
MEM = 1 << 24
mem = mmap.mmap(-1, MEM, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
buf = ctypes.create_string_buffer(32)
struct.pack_into("<IIQQQ", buf, 0, 0, 0, 0, MEM,
                 ctypes.addressof(ctypes.c_char.from_buffer(mem)))
fcntl.ioctl(vm_fd, 0x4020AE46, buf, True)
fcntl.ioctl(vm_fd, 0xAE60, 0)
fcntl.ioctl(vm_fd, 0xAE47, 0x40000000)
vcpu_fd = fcntl.ioctl(vm_fd, 0xAE41, 0)
run = mmap.mmap(vcpu_fd, fcntl.ioctl(kvm_fd, 0xAE04, 0), mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE)

# 桩：循环向串口 0x3F8 输出 'A'（有 irqchip 时 hlt 会在内核里睡死，必须用 IO exit 回报）
STUB_ADDR = 0x80000
stub = bytes([0xB0, 0x41,          # mov al, 'A'
              0xBA, 0xF8, 0x03,    # mov dx, 0x3F8
              0xEE,                # out dx, al
              0xEB, 0xFD])         # jmp -2（死循环）
mem[STUB_ADDR:STUB_ADDR+len(stub)] = stub

# 诊断 IDT：向量 v 的异常 -> 跳到 handler_v，handler 往串口输出字节 v
# 这样发生任何异常，宿主都能从串口看到向量号（0x0E=缺页 0x0D=GP 0x06=UD...）
HANDLER_BASE = 0x90000
for v in range(32):
    h = HANDLER_BASE + v * 16
    code = bytes([0xB0, v,                # mov al, v
                  0xBA, 0xF8, 0x03,       # mov dx, 0x3F8
                  0xEE,                   # out dx, al
                  0xEB, 0xFE])            # jmp -2（原地打转）
    mem[h:h+len(code)] = code

IDT_ADDR = 0x98000
idt = bytearray(32 * 8)
for v in range(32):
    h = HANDLER_BASE + v * 16
    struct.pack_into("<HHBBBB", idt, v * 8, h & 0xFFFF, 0x08, 0, 0x8E, h >> 16)
mem[IDT_ADDR:IDT_ADDR+len(idt)] = idt

sregs = ctypes.create_string_buffer(312)
fcntl.ioctl(vcpu_fd, 0x8138AE83, sregs, True)

def seg(off, selector, type_, base=0, limit=0xFFFFF, db=1, g=1, s=1):
    struct.pack_into("<QIH8B2x", sregs, off, base, limit, selector, type_,
                     1, 0, db, s, 0, g, 0)

seg(0, 0x08, 0x9B)
for off in (24, 48, 72, 96, 120):
    seg(off, 0x10, 0x93)
seg(144, 0, 0xB, limit=0xFFFF, db=0, g=0, s=0)
struct.pack_into("<QH6x", sregs, 192, 0x1000, 0x17)   # GDTR
struct.pack_into("<QH6x", sregs, 208, IDT_ADDR, 32*8 - 1)  # IDTR（诊断用）
struct.pack_into("<Q", sregs, 224, 0x33)
fcntl.ioctl(vcpu_fd, 0x4138AE84, sregs, True)

regs = ctypes.create_string_buffer(144)
struct.pack_into("<18Q", regs, 0, *([0] * 18))
struct.pack_into("<Q", regs, 128, STUB_ADDR)   # RIP
struct.pack_into("<Q", regs, 136, 0x2)         # RFLAGS
fcntl.ioctl(vcpu_fd, 0x4090AE82, regs, True)

got = 0
for _ in range(50):
    fcntl.ioctl(vcpu_fd, 0xAE80, 0)                # KVM_RUN
    reason = struct.unpack_from("<I", run, 8)[0]
    if reason == 2:
        got += 1
        if got == 1:
            print("串口收到字符:", chr(run[struct.unpack_from('<Q', run, 40)[0]]))
        if got >= 3:
            break
    elif reason == 9:
        print("FAIL_ENTRY hw_reason = 0x%X" % struct.unpack_from("<Q", run, 32)[0])
        break
    else:
        print("exit_reason =", reason)
        break
print("PASS: VM-entry 状态合法，客户机代码在真实执行" if got >= 3 else "FAIL")
