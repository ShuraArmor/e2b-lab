# 成功引导 Linux 内核的完整前置条件清单（按层次）

> 以 LEMU 引导 Linux 6.6（x86_64 直启）为样本整理。
> 每一层的"失败模式"全部来自本项目调试中真实踩过的坑。
> 层与层的关系：**下层是上层存在的物理前提；诊断时从报错的那一层往下找**。

---

## 总览：五层世界

```
第 5 层  客户机自己的世界     内核自建页表/驱动/init —— 接手后与 VMM 无关
第 4 层  持续的物理世界模拟   VMM 主循环必须持续"扮演"的硬件与时钟
第 3 层  vCPU 开机姿势        寄存器状态（VMX 逐字段硬件验收）
第 2 层  写进内存的固件与内核   引导协议的全部数据结构
第 1 层  KVM 原语造出的机器    CPU/内存/中断芯片的物理构成
第 0 层  宿主前提              没有这一层，一切免谈
```

---

## 第 0 层：宿主前提

| # | 条件 | 缺失症状 |
|---|---|---|
| 0.1 | CPU 硬件虚拟化 + 上层暴露嵌套（WSL2 场景：Hyper-V 对 L1 开 VMX） | `/dev/kvm` 不存在，什么都不会开始 |
| 0.2 | 内核镜像与 initramfs 内容本身正确 | 任何 VMM 都救不了（用 QEMU 对照验证）|
| 0.3 | 工具链：gcc（offsetof 探针）、iasl（ACPI 编译）、正确的内核头文件 | 结构体偏移/编号只能靠猜 |

**验证命令**：`ls /dev/kvm` + `grep -oE "vmx|svm" /proc/cpuinfo`

---

## 第 1 层：KVM 原语造出的"机器硬件"

| # | 原语 | 造出什么 | 顺序约束 |
|---|---|---|---|
| 1.1 | `open("/dev/kvm")` + API 版本检查 | 工厂许可证 | 最先 |
| 1.2 | `KVM_CREATE_VM` | 机器壳（vm_fd）| |
| 1.3 | `mmap` + `KVM_SET_USER_MEMORY_REGION` | 物理内存（GPA 0~512MB = 你的 mmap）| **必须先于第一次 RUN**，否则取指即 EPT violation |
| 1.4 | `KVM_CREATE_IRQCHIP` | PIC + LAPIC + IOAPIC（内核态）| **必须先于 CREATE_VCPU**，否则 vCPU 的 LAPIC 无法初始化 |
| 1.5 | `KVM_CREATE_PIT2` | 8254 时钟（内核态 tick 源）| |
| 1.6 | `KVM_SET_TSS_ADDR` | 任务状态段地址（x86 强制）| **必须先于 CREATE_VCPU** |
| 1.7 | `KVM_CREATE_VCPU` + kvm_run mmap | vCPU（= 一个线程）+ 通话页 | |

**缺失症状**：无 IRQCHIP → vCPU 无 LAPIC，中断体系全瘫；无内存 region → 取指即异常。

---

## 第 2 层：写进物理内存的"固件与内核"（引导协议数据）

全部通过普通 Python 内存写入完成——**这是 VMM 的"体力活"，也是引导协议的正文**。

### 2.1 恒等映射页表（3 张表，12KB）

| 表 | 地址 | 内容 |
|---|---|---|
| PML4 | 0x2000 | 项[0] → PDPT（Present+RW）|
| PDPT | 0x3000 | 项[0] → PD |
| PD | 0x4000 | 512 项 × 2MB 大页（P|RW|PS），覆盖 0~1GB |

- 无第四级（PT）：2MB 大页跳过
- 只填 PML4[0]：引导期恒等映射只需要低 1GB
- **高地址映射（0xFFFFFFFF8…）不由你建**——解压后内核自建并切 CR3

### 2.2 zero page（boot_params @ 0x10000，4096 字节）

| 偏移 | 字段 | 必须的值 |
|---|---|---|
| 0x1F1-0x270 | setup_header 原样复制（含 "HdrS" 魔数、版本、initrd_addr_max）| |
| 0x210 | type_of_loader = 0xFF | |
| 0x211 | loadflags：LOADED_HIGH(0x01)【**必须**】| |
| 0x214 | code32_start = 0x100000 | |
| 0x218/0x21C | ramdisk_image / ramdisk_size | |
| 0x228 | cmd_line_ptr | |
| 0x1E8 / 0x2D0 | e820_entries / e820_table（每条 20B：addr,size,type）| |

### 2.3 E820 三段（最小合法形态）

```
[0x0, 0x9F000)                  usable     ← 低内存
[0x9F000, 0x100000)             reserved   ← 传统 BIOS/EBDA 区（也是你的安全区）
[0x100000, mem_size)            usable     ← 主内存
```

规则：不得重叠；usable 必须覆盖内核/initrd/页表实际写入的位置；**谎报 usable = 延迟爆炸**（引导期正常，触碰到时崩）。

### 2.4 其余内容

- 内核保护模式部分 → code32_start（0x100000）
- cmdline（null 结尾）→ 0x20000
- GDT @ 0x1000（空 / 64 位代码 0x08 / 64 位数据 0x10）

### 2.5 initrd → 内存顶部，页对齐

---

## 第 3 层：vCPU 开机姿势（VMX 逐字段硬件验收）

**必须在首次 KVM_RUN 前完成。任何字段不合格 → `KVM_EXIT_FAIL_ENTRY (0x80000021)`，一个字都不执行。**

### 段寄存器（每段 24 字节：base,limit,selector,8 标志）

| 段 | selector | 关键属性 |
|---|---|---|
| CS | 0x08 | base=0, limit=4G, type=0x9B（已访问代码）, L=1, DB=0, G=1 |
| DS/ES/FS/GS/SS | 0x10 | type=0x93（已访问数据），US 位=0（**SMAP 的伏笔**）|
| TR | 0（selector 无效亦可）| **type 必须 0xB（busy TSS）**——复位值不合规是经典坑 |
| GDTR | base=0x1000, limit=0x17 | 指向 3 项 GDT |
| IDTR | 清零 | 内核自己装 64 位 IDT；此前的异常=三重故障 |

### 控制寄存器（长模式四件套）

| 寄存器 | 值 | 位含义 |
|---|---|---|
| CR0 | 0x80000033 | PG(31) 分页开 + PE(0) 保护模式 + MP/ET/NE |
| CR3 | PML4_ADDR (0x2000) | 指向你的页表 |
| CR4 | 0x20 | PAE——**长模式强制要求**，缺了 #GP |
| EFER | 0xD00 | LME(8)+LMA(10)+NXE(11) |

### 通用寄存器

| 寄存器 | 值 | 合同依据 |
|---|---|---|
| RSI | zero page 地址 | **64 位入口：RSI 必须 = boot_params** |
| RIP | code32_start + 0x200 | 64 位入口 = 保护入口 + 0x200 |
| RSP | 有效栈顶（如 0x9000）| 内核开头就 push/call，**RSP=0 即死** |
| RFLAGS | 0x2 | bit1 恒 1，IF=0（中断先关）|

### 结构体偏移（gcc offsetof 实测，勿凭记忆）

```
kvm_run: exit_reason@8, io@32, msr{error@32,index@40,data@48}, mmio{addr@32,data@40,len@48}
kvm_segment(24B): base@0 limit@8 selector@12 type@14 present@15 dpl@16 db@17 s@18 l@19 g@20 avl@21
kvm_regs(144B): rax@0 rsi@32 rsp@48 rip@128 rflags@136
kvm_sregs(312B): cs@0 ds@24 … tr@144 ldt@168 gdt@192 idt@208 cr0@224 cr3@240 cr4@248 efer@264
```

---

## 第 4 层：运行时的"物理世界"（主循环必须持续扮演）

第一次 KVM_RUN 之后，VMM 的主循环就是这台机器的"物理定律执行者"：

### 4.1 IO exit 分发（端口语义）

| 端口 | 设备 | 必须实现的语义 |
|---|---|---|
| 0x3F8-0x3FF | 16550 UART | LSR=0x60/0x61、IIR(RX=0x04/TX=0x02)、THRI 使能即触发、loopback、电平 IRQ4 |
| 0x40-0x43 | PIT 通道计数 | 递减/回绕/锁存（TSC 校准用）|
| 0x61 | 系统控制口 | **bit5 = PIT ch2 OUT**（TSC 校准死等它）|
| 0xCF8/0xCFC | PCI 配置 | 返回 0 = "空总线"（内核安全跳过）|
| 未知端口 | — | IN 回 0、OUT 忽略（内核探测会摸一切）|

### 4.2 中断注入（三个方向）

| 中断 | 来源 | 注入方式 | 时序约束 |
|---|---|---|---|
| IRQ0 | ticker 线程（100Hz）| `KVM_IRQ_LINE(0, 脉冲)` | **必须等客户机编程 PIT 通道 0 后才开始**——提前注入 = 早期静默 panic |
| IRQ4 | 串口有 RX 数据 | `KVM_IRQ_LINE(4, 1)`，驱动读空后拉低 | 电平语义，脉冲会被 LAPIC 漏采 |
| IRQ5 | （暂无，virtio 预留）| 同上 | |

### 4.3 两个服务线程（QEMU 架构必需）

- **ticker 线程**：100Hz 心跳（guest halt 时 kick 阻塞的 vCPU）
- **stdin 线程**：读输入 → UART RX → 注入（单线程主循环会死锁）

### 4.4 CPUID 白名单（--cpuid，强烈建议）

不灌表 = KVM 透传宿主全特性 = SMAP/PKU/OSPKE 等高级位全暴露 → 内核开保护 → 撞上早期恒等映射的 USER 位页 → 确定性 #PF。剥离 leaf7 的 SMAP/SMEP/UMIP 位后正常。

---

## 第 5 层：客户机自己的世界（内核接手后）

```
解压 → 自建页表（CR3=0x366E000 切走，弃用你的脚手架）
     → CPUID 感知（SMAP 已剥离 → 不开 → 恒等 USER 页可访问）
     → 串口驱动探测（loopback ✓ → ttyS0 注册 → console 切换）
     → /init → eth0 → httpd → shell
```

**这一层开始，VMM 退居幕后**：只在"设备端口被读写"时被动响应。客户机的内部（页表、进程、调度）对 VMM 结构性不可见——能看的只有内存字节和 exit 事件流。

---

## 附：本项目踩坑 → 层次对照表

| 层 | 踩过的坑 | 症状 |
|---|---|---|
| 0 | WSL 嵌套、/dev/kvm 权限 | 进程起不来 |
| 1 | IRQCHIP/PIT 顺序、TSS 地址符号溢出 | ENOTTY/OverflowError |
| 2 | E820 造假、initrd 对齐、leaf 0x15 叶号写错、CPUID 未消毒 | 除零 oops / 引导死 |
| 3 | 段格式错位（漏 H）、RSP=0、TR 不合规、CR4.PAE 缺 | FAIL_ENTRY 0x80000021 |
| 4 | IO direction 反、THRI 即触发缺、IRQ4 脉冲漏采、0x61 bit5 死等 49 万次 | 串口失聪/卡 16 字节/死循环 |
| 5 | nokaslr 丢失 | 随机地址卡死 |

---

## 一句话总结

> **引导 = 按合同准备世界**：第 1-2 层把"硬件和内核"放进内存，第 3 层把 CPU 摆成"刚跳入 64 位入口"的姿势，第 4 层用永久在线的线程持续扮演时钟和设备，第 5 层开始客户机就是自己的主人。**每一层的验收标准由下一层的消费者定义**——VMX 验收你的寄存器，内核验收你的 E820 和串口，shell 验收你的 tty——这就是虚拟化工程的全部结构。
