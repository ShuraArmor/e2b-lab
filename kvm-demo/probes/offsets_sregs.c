#include <stdio.h>
#include <stddef.h>
#include <linux/kvm.h>
int main(void) {
    printf("sizeof(sregs)=%zu sizeof(regs)=%zu sizeof(kvm_segment)=%zu\n",
        sizeof(struct kvm_sregs), sizeof(struct kvm_regs), sizeof(struct kvm_segment));
    printf("cs=%zu ds=%zu es=%zu fs=%zu gs=%zu ss=%zu tr=%zu ldt=%zu\n",
        offsetof(struct kvm_sregs, cs), offsetof(struct kvm_sregs, ds),
        offsetof(struct kvm_sregs, es), offsetof(struct kvm_sregs, fs),
        offsetof(struct kvm_sregs, gs), offsetof(struct kvm_sregs, ss),
        offsetof(struct kvm_sregs, tr), offsetof(struct kvm_sregs, ldt));
    printf("seg: base=%zu limit=%zu selector=%zu type=%zu present=%zu dpl=%zu db=%zu s=%zu l=%zu g=%zu avl=%zu\n",
        offsetof(struct kvm_segment, base), offsetof(struct kvm_segment, limit),
        offsetof(struct kvm_segment, selector), offsetof(struct kvm_segment, type),
        offsetof(struct kvm_segment, present), offsetof(struct kvm_segment, dpl),
        offsetof(struct kvm_segment, db), offsetof(struct kvm_segment, s),
        offsetof(struct kvm_segment, l), offsetof(struct kvm_segment, g),
        offsetof(struct kvm_segment, avl));
    printf("gdt=%zu gdt.base=%zu gdt.limit=%zu idt=%zu\n",
        offsetof(struct kvm_sregs, gdt), offsetof(struct kvm_sregs, gdt.base),
        offsetof(struct kvm_sregs, gdt.limit), offsetof(struct kvm_sregs, idt));
    printf("cr0=%zu cr2=%zu cr3=%zu cr4=%zu cr8=%zu efer=%zu apic_base=%zu\n",
        offsetof(struct kvm_sregs, cr0), offsetof(struct kvm_sregs, cr2),
        offsetof(struct kvm_sregs, cr3), offsetof(struct kvm_sregs, cr4),
        offsetof(struct kvm_sregs, cr8), offsetof(struct kvm_sregs, efer),
        offsetof(struct kvm_sregs, apic_base));
    printf("regs: rax=%zu rip=%zu rflags=%zu rsi=%zu\n",
        offsetof(struct kvm_regs, rax), offsetof(struct kvm_regs, rip),
        offsetof(struct kvm_regs, rflags), offsetof(struct kvm_regs, rsi));
    return 0;
}
