DefinitionBlock ("", "DSDT", 2, "LEMU", "LEMUVIRT", 0x00000002)
{
    Scope (\_SB_)
    {
        // ---- 现代路径：ACPI 声明的 PCIe 根桥（不再是 legacy type1 盲扫）----
        Device (PCI0)
        {
            Name (_HID, EisaId ("PNP0A08"))     // PCIe Root Bridge
            Name (_CID, EisaId ("PNP0A03"))     // 兼容声明为 PCI 桥
            Name (_SEG, Zero)                   // PCI segment 0
            Name (_BBN, Zero)                   // 根总线号 0

            // 根总线资源窗口：只认领我们设备的 MMIO 区
            // （0xC0000000~0xC0000FFF 归下面的 VIO0 平台设备，不重叠）
            Method (_CRS, 0, NotSerialized)
            {
                Name (RBUF, ResourceTemplate ()
                {
                    WordBusNumber (ResourceProducer, MinFixed, MaxFixed,
                        PosDecode, 0x0, 0x0, 0x0, 0x0, 0x1)
                    DWordMemory (ResourceProducer, PosDecode, MinFixed, MaxFixed,
                        NonCacheable, ReadWrite,
                        0x0, 0xC0001000, 0xC000FFFF, 0x0, 0x0000F000)
                })
                Return (RBUF)
            }

            // PCI 中断路由：设备 1 的 INTA -> GSI 5
            // （包元素: {地址, 针脚(0=A), 源(0=直连GSI), GSI}）
            Name (_PRT, Package ()
            {
                Package () { 0x0001FFFF, 0, 0, 5 }
            })
        }

        // 注意：VIO0 (LNRO0005, virtio-net) 暂时移出 DSDT——
        // ACPI 枚举它 + serial8250_init 会触发 "overflow" oops（待查，见记忆）。
        // --net 模式将来用 dsdt_net.aml 变体恢复。
    }
}
