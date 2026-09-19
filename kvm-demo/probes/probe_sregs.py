#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sregs 诊断：复位值 -> 我们写的 -> SET 后内核留存的，三方对照。"""
import ctypes, fcntl, mmap, os, struct

kvm_fd = os.open("/dev/kvm", os.O_RDWR)
vm_fd = fcntl.ioctl(kvm_fd, 0xAE01, 0)
mem = mmap.mmap(-1, 1 << 28, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
buf = ctypes.create_string_buffer(32)
struct.pack_into("<IIQQQ", buf, 0, 0, 0, 0, 1 << 28,
                 ctypes.addressof(ctypes.c_char.from_buffer(mem)))
fcntl.ioctl(vm_fd, 0x4020AE46, buf, True)
fcntl.ioctl(vm_fd, 0xAE60, 0)          # CREATE_IRQCHIP
fcntl.ioctl(vm_fd, 0xAE47, 0x40000000) # SET_TSS_ADDR
vcpu_fd = fcntl.ioctl(vm_fd, 0xAE41, 0)

SEG_NAMES = ["cs", "ds", "es", "fs", "gs", "ss", "tr", "ldt"]

def get_sregs():
    b = ctypes.create_string_buffer(312)
    fcntl.ioctl(vcpu_fd, 0x8138AE83, b, True)
    return b

def show(tag, b):
    print(f"--- {tag} ---")
    for i, name in enumerate(SEG_NAMES):
        off = i * 24
        base, limit, sel = struct.unpack_from("<QIH", b, off)
        type_, present, dpl, db, s, l, g, avl = struct.unpack_from("8B", b, off + 14)
        print(f"  {name:3s} sel=0x{sel:04X} base=0x{base:X} limit=0x{limit:X} "
              f"type=0x{type_:X} p={present} dpl={dpl} db={db} s={s} l={l} g={g} avl={avl}")
    gbase, glimit = struct.unpack_from("<QH", b, 192)
    print(f"  gdt base=0x{gbase:X} limit=0x{glimit:X}")
    ibase, ilimit = struct.unpack_from("<QH", b, 208)
    print(f"  idt base=0x{ibase:X} limit=0x{ilimit:X}")
    for off, name in [(224, "cr0"), (240, "cr3"), (248, "cr4"), (264, "efer")]:
        print(f"  {name} = 0x{struct.unpack_from('<Q', b, off)[0]:X}")

before = get_sregs()
show("复位值", before)

def seg(b, off, selector, type_, base=0, limit=0xFFFFF, db=1, g=1, s=1):
    struct.pack_into("<QIBBBBBBBB2x", b, off, base, limit, selector, type_,
                     1, 0, db, s, g, 0)

seg(before, 0, 0x08, 0x9B)
for off in (24, 48, 72, 96, 120):
    seg(before, off, 0x10, 0x93)
seg(before, 144, 0, 0xB, limit=0xFFFF, db=0, g=0, s=0)
struct.pack_into("<QH6x", before, 192, 0x1000, 0x17)
struct.pack_into("<Q", before, 224, 0x33)
struct.pack_into("<Q", before, 240, 0)
struct.pack_into("<Q", before, 248, 0)
struct.pack_into("<Q", before, 264, 0)
show("我们要写的", before)

fcntl.ioctl(vcpu_fd, 0x4138AE84, before, True)   # SET_SREGS
after = get_sregs()
show("SET 后内核留存", after)
