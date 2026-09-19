#include <stdio.h>
#include <stddef.h>
#include <linux/kvm.h>
int main(void) {
    printf("exit_reason=%zu\n", offsetof(struct kvm_run, exit_reason));
    printf("io=%zu direction=%zu size=%zu port=%zu count=%zu data_offset=%zu\n",
        offsetof(struct kvm_run, io),
        offsetof(struct kvm_run, io.direction),
        offsetof(struct kvm_run, io.size),
        offsetof(struct kvm_run, io.port),
        offsetof(struct kvm_run, io.count),
        offsetof(struct kvm_run, io.data_offset));
    return 0;
}
