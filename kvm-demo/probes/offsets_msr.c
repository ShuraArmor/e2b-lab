#include <stdio.h>
#include <stddef.h>
#include <linux/kvm.h>
int main(void) {
    printf("KVM_X86_SET_MSR_FILTER = 0x%08lX\n", (unsigned long)KVM_X86_SET_MSR_FILTER);
    printf("KVM_ENABLE_CAP          = 0x%08lX\n", (unsigned long)KVM_ENABLE_CAP);
    printf("sizeof(kvm_msr_filter)  = %zu\n", sizeof(struct kvm_msr_filter));
    printf("sizeof(kvm_enable_cap)  = %zu\n", sizeof(struct kvm_enable_cap));
    printf("range: flags=%zu nmsrs=%zu base=%zu bitmap=%zu range_size=%zu\n",
        offsetof(struct kvm_msr_filter_range, flags),
        offsetof(struct kvm_msr_filter_range, nmsrs),
        offsetof(struct kvm_msr_filter_range, base),
        offsetof(struct kvm_msr_filter_range, bitmap),
        sizeof(struct kvm_msr_filter_range));
    printf("KVM_CAP_X86_USER_SPACE_MSR = %d\n", KVM_CAP_X86_USER_SPACE_MSR);
    printf("KVM_MSR_FILTER_READ = %d\n", KVM_MSR_FILTER_READ);
    printf("KVM_MSR_EXIT_REASON_FILTER = %d\n", KVM_MSR_EXIT_REASON_FILTER);
    return 0;
}
