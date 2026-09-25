#!/usr/bin/env python3
"""探针：SET_CPUID2 后立即 GET_CPUID2 读回，核对 0xD 子叶与 flags 是否真正入库"""
import ctypes, fcntl, mmap, os, struct

KVM_GET_API_VERSION = 0xAE00
KVM_CREATE_VM = 0xAE01
KVM_CREATE_VCPU = 0xAE41
KVM_SET_CPUID2 = 0x4008AE90
KVM_GET_CPUID2 = 0xC008AE91     # _IOWR(KVMIO, 0x91, ptr)：方向位与 SET 不同

kvm_fd = os.open("/dev/kvm", os.O_RDWR)
vm_fd = fcntl.ioctl(kvm_fd, KVM_CREATE_VM, 0)
vcpu_fd = fcntl.ioctl(vm_fd, KVM_CREATE_VCPU, 0)

entries = [
    (0x0, 0, 0x16, 0, 0, 0),
    (0xD, 0, 0x7, 0x340, 0x340, 0),
    (0xD, 1, 0x1, 0x340, 0, 0),
    (0xD, 2, 0x100, 0x240, 0, 0),
]
N = len(entries)
buf = ctypes.create_string_buffer(8 + 64 * 40)
struct.pack_into("<I", buf, 0, N)
for i, (fn, sub, eax, ebx, ecx, edx) in enumerate(entries):
    flags = 0x2 if fn == 0xD else 0      # SIGNIFCANT_INDEX
    e = 8 + i * 40
    struct.pack_into("<IIIIIIII", buf, e, fn, sub, flags, eax, ebx, ecx, edx, 0)
    print(f"打包 entry[{i}]: fn={fn:#x} sub={sub} flags={flags} eax={eax:#x} ebx={ebx:#x}")
fcntl.ioctl(vcpu_fd, KVM_SET_CPUID2, buf, True)

# 读回
rb = ctypes.create_string_buffer(8 + 64 * 40)
struct.pack_into("<I", rb, 0, 64)
fcntl.ioctl(vcpu_fd, KVM_GET_CPUID2, rb, True)
nent = struct.unpack_from("<I", rb, 0)[0]
print(f"\n内核返回 nent={nent}")
for i in range(min(nent, 10)):
    fn, sub, flags, eax, ebx, ecx, edx = struct.unpack_from("<7I", rb, 8 + i * 40)
    mark = "  <-- 0xD 子叶" if fn == 0xD else ""
    print(f"GET[{i}]: fn={fn:#x} sub={sub} flags={flags:#x} "
          f"eax={eax:#x} ebx={ebx:#x} ecx={ecx:#x} edx={edx:#x}{mark}")
