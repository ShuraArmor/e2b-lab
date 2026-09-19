DefinitionBlock ("", "DSDT", 2, "LEMU", "LEMUVIRT", 0x00000001)
{
    Scope (\_SB_)
    {
        Device (VIO0)
        {
            Name (_HID, "LNRO0005")     // Linux virtio-mmio 的 ACPI HID
            Name (_UID, 0x00)

            Method (_CRS, 0, NotSerialized)
            {
                Name (RBUF, ResourceTemplate ()
                {
                    Memory32Fixed (ReadWrite,
                        0xC0000000,         // 寄存器窗口基址
                        0x00001000,         // 4KB
                        )
                    Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive)
                        { 5 }               // IRQ5
                })
                Return (RBUF)
            }
        }
    }
}
