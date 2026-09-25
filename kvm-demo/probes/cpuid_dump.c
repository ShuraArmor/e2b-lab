/* dump CPUID leaf 0xD (XSTATE) 全部子叶 + leaf1 ECX + leaf7 EBX —— 供 LEMU 白名单照抄 */
#include <stdio.h>
#include <stdint.h>

static inline void cpuid(uint32_t fn, uint32_t sub,
                         uint32_t *a, uint32_t *b, uint32_t *c, uint32_t *d)
{
    __asm__ volatile("cpuid"
                     : "=a"(*a), "=b"(*b), "=c"(*c), "=d"(*d)
                     : "a"(fn), "c"(sub));
}

int main(void)
{
    uint32_t a, b, c, d;
    cpuid(1, 0, &a, &b, &c, &d);
    printf("leaf 0x1        EAX=%08X EBX=%08X ECX=%08X EDX=%08X\n", a, b, c, d);
    cpuid(7, 0, &a, &b, &c, &d);
    printf("leaf 0x7.0      EAX=%08X EBX=%08X ECX=%08X EDX=%08X\n", a, b, c, d);
    cpuid(0x80000001, 0, &a, &b, &c, &d);
    printf("leaf 0x80000001 EAX=%08X EBX=%08X ECX=%08X EDX=%08X\n", a, b, c, d);
    for (uint32_t s = 0; s < 4; s++) {
        cpuid(0xD, s, &a, &b, &c, &d);
        printf("leaf 0xD.%u     EAX=%08X EBX=%08X ECX=%08X EDX=%08X\n", s, a, b, c, d);
    }
    return 0;
}
