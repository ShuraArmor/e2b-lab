# LEMU 手记：从三句 ioctl 到引导 Linux 内核的完整踩坑记录

> 这不是教程，是一份**实验日志**——记录一个纯 Python 手写 VMM（LEMU）从
> "能打印 16 字节" 到 "引导真 Linux 6.6 内核" 的全过程。每一步失败都是
> 一个内存级别的知识点：VMCS 布局、EPT、SMAP、PCID、8254 回绕、16550
> 中断语义……全部来自真实的寄存器现场，不是背书。

---

## 0. 成果总览

| 里程碑 | 文件 | 状态 |
|---|---|---|
| 三句 ioctl 造 VM，跑 16 字节实模式机器码 | `vmm/kvm_minivm.py` | ✅ |
| 宿主截获 VM exit 字节落盘（A→Z 循环，78 exit/30ms） | `vmm/vm_logger.py` | ✅ |
| 手写 VMM + 64 位引导协议 + 串口，**引导真 Linux 6.6 到 shell** | `vmm/lemu.py` | ✅（基础功能）|
| virtio-net 网卡 + TAP + httpd + Windows 访问 | `vmm/virtio_net.py` 等 | 🔶 进行中 |
| 对照参考：QEMU+KVM 跑同一内核 | `scripts/vm.sh` | ✅ |

核心认知（先记住，后面反复验证）：

> **KVM 只负责 CPU、内存、中断三样"硬货"；设备、时钟、引导协议 100% 是
> VMM（你）的工作。QEMU 的几百万行代码，就是这份工作清单。**

---

## 1. 一个 VMM 要为 Linux 做什么（完整清单）

LEMU 最终形态的职责划分——这也是所有 x86_64 直启 VMM（QEMU -kernel、
kvmtool、Firecracker）的公共骨架：

```
① CPU 状态     实模式→保护模式→长模式的初始状态、段描述符、栈
② 内存         guest 物理内存（一块 mmap）+ E820 内存表
③ 引导协议     zero page（boot_params）、cmdline、initrd 位置、GDT
④ 页表         恒等映射（若走 64 位入口）或 16 位实模式入口
⑤ 中断         PIC/PIT（in-kernel irqchip 一句话创建）+ 设备中断注入
⑥ 设备         至少一个串口（控制台）+ 可选 virtio 磁盘/网卡
⑦ 时钟         客户机编程 PIT 后按真实速率"滴答"
⑧ CPUID/MSR    CPU 特性报告（SMAP/PKU 等位直接决定内核行为！）
```

**KVM 只给你 ①③⑤ 的底层机制**（CPU 状态切换靠 VT-x、地址翻译靠 EPT、
中断芯片靠 in-kernel irqchip），**其余全是 VMM 的活**。

---

## 2. 骨架：三句 ioctl（kvm_minivm.py）

```python
kvm_fd = open("/dev/kvm")
vm_fd   = ioctl(kvm_fd, KVM_CREATE_VM)      # ① 造机器
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, …) # ② 划内存（你的 mmap = 它的内存条）
vcpu_fd = ioctl(vm_fd, KVM_CREATE_VCPU)     # ③ 造 vCPU（宿主的一个线程）
while True:
    ioctl(vcpu_fd, KVM_RUN)                 # ④ 通电！CPU 进入非根模式执行
    # 每次 VM exit 返回，检查原因，处理后再放行
```

第一批成果：16 字节实模式机器码（`mov al,'A'; out 0x3F8; inc al; …`）
在 CPU 非根模式里循环打串口，宿主逐字节截获——**"HI KVM" 打出来那一刻，
三句 ioctl 的骨架就被证明是完整的**。

---

## 3. Bug 图鉴（按发现顺序，附诊断方法）

### A. KVM 接口层

#### Bug 1：kvm_run 的字段偏移猜错了
- **症状**：读 exit_reason/IO 字段全是乱码
- **根因**：`kvm_run` 是个大 union，x86 专有字段（cr2 等）插在通用字段和
  io 结构之间——**io 字段实际在偏移 32，exit_reason 在 8**，网上教程
  常写的 16 是错的
- **诊断**：写 10 行 C 用 `offsetof(struct kvm_run, io)` 编译实测
  （`probes/offsets_kvmrun.c`）
- **教训**：**结构体偏移不猜，问编译器**。此后所有偏移都用这个方法实测。

#### Bug 2：Python ioctl 的整型参数只有有符号 32 位
- **症状**：`KVM_SET_TSS_ADDR 0xFFFBD000` → OverflowError
- **修复**：换成 < 2^31 的页对齐地址（0x40000000）
- **教训**：`fcntl.ioctl` 的整型参数按 C int 传递；大地址要么拆、要么用
  ctypes 的缓冲区形式。

#### Bug 3：KVM_IRQ_LINE 的编号
- **症状**：ENOTTY（不认识的 ioctl）
- **根因**：`KVM_IRQ_LINE=0x4008AE61` 与 `KVM_IRQ_LINE_STATUS=0xC008AE67`
  记混；且新版内核把宏名改成了 `KVM_X86_SET_MSR_FILTER` 一类的名字
- **诊断**：gcc -E 展开头文件 + 逐个编号探测
- **教训**：**KVM ioctl 编号以你机器的 `/usr/include/linux/kvm.h` 为准**，
  编号错 = ENOTTY，不会更明显。

#### Bug 4：WSL2 嵌套虚拟化
- `/dev/kvm` 存在于 WSL2 里（Windows 本体没有 KVM）；Docker Desktop 装
  在 `F:\Docker`；Git Bash 调 wsl.exe 必须 `MSYS_NO_PATHCONV=1`，否则
  `/mnt/e/...` 被改写成 Git 安装路径。

### B. 引导协议层（lemu 的核心难点）

Linux bzImage 的 64 位直启需要引导器准备的全部东西——**缺一个就是
三重故障（KVM_EXIT_SHUTDOWN），而且常常一句话都不打印**。

#### Bug 5：段描述符结构体少写一个 short → 全体错位
- **症状**：`KVM_EXIT_FAIL_ENTRY, hw_reason=0x80000021`（VM-entry：
  invalid guest state）
- **诊断**：`probes/probe_sregs.py` 做三方对照——复位值 / 我们写的 /
  内核实际保存的。一眼看出所有字段错位一个字节
- **根因**：kvm_segment 布局是 `base(8) limit(4) selector(2) 标志位(8) 补齐(2)`
  = 24 字节，我写成了 `<QIBBBBBBBB2x`（漏了 selector 的 H）
- **教训**：**VMX 对 guest state 逐字段做硬件检查**（CS.type、TR 必须
  busy-TSS、LDT、GDT 对齐……），错一个 bit 就是 0x80000021。
- **配套知识**：正确的 64 位入口段状态——CS：base=0, limit=0xFFFFF,
  type=0x9B（已访问代码段）, L=1, DB=0, G=1；数据段 type=0x93。

#### Bug 6：RSP=0 → 第一条指令就三重故障
- **症状**：修完段后立刻 SHUTDOWN，RIP 停在入口第一条指令
- **根因**：内核入口立即 `push`/`call`，RSP=0 压栈即 #SS → 无 IDT →
  #DF → 三重故障
- **修复**：入口前给临时栈（0x9000），内核稍后会自设真正的栈
- **教训**：**手写 VMM 的每个寄存器都要当真**——guest 是"真空中的机器"，
  没有引导器替你准备任何东西。

#### Bug 7：32 位入口死路 → 64 位直启
- **症状**：32 位入口（`code32_start`）反复三重故障
- **转机**：读 `xloadflags` 发现 `XLF_KERNEL_64`——**64 位内核支持从
  64 位入口直接启动**：引导器自建长模式页表 + 64 位 GDT + CR0.PG+
  CR4.PAE + EFER.LME|LMA，`RIP=code32_start+0x200, RSI=zero_page`
- **对比**：QEMU `-kernel`、kvmtool 都走这条路；32 位入口是给老引导器
  （LILO 时代）的兼容路径
- **实现**（lemu.py `build_boot_state`）：
  - 内核保护模式部分装到 `code32_start`（0x100000）
  - zero page（boot_params）：复制 setup_header，填 `type_of_loader`、
    `loadflags`、`cmd_line_ptr`、`ramdisk_image/size`、E820 表
  - initrd 放物理内存顶部（页对齐）
  - 恒等映射页表：PML4@0x2000 → PDPT@0x3000 → PD@0x4000（2MB 大页×512）

#### Bug 8：E820 的价值
- 内核通过 `boot_params+0x2D0` 的 E820 表认识物理内存。我们给了三段：
  `[0,0x9F000) 可用 / [0x9F000,0x100000) 保留 / [0x100000,512MB) 可用`
  ——**坑**：[0x9F000,0x100000) 这个"保留洞"之后成了 ACPI 表和诊断数据
  的安全区（内核不会碰）。

### C. 设备模拟层（无底洞，但每层都透明）

#### Bug 9：串口只写不读（IO direction 反了）
- **症状**：earlyprintk 输出正常，但内核读串口寄存器全错
- **根因**：`kvm_run` 的 IO 方向定义 **`KVM_EXIT_IO_IN=0, KVM_EXIT_IO_OUT=1`**
  ——我凭直觉写成 0=OUT
- **教训**：**方向枚举查内核头文件，勿凭记忆**。这个 bug 让 RX 路径
  完全失效，而 TX 路径"碰巧"正常，极具迷惑性。

#### Bug 10：UART 缺 TX 中断 → 用户态输出恰好卡 16 字节
- **症状**：内核 printk（轮询模式）全部正常；/init 里第一个 `echo`
  只输出 16 个字符就永远停住
- **根因**：内核 printk 走**轮询**路径（直接写 THR）；用户态 tty 写走
  **中断驱动**路径——驱动写满 16 字节 FIFO 后使能 IER.THRI（发送中断
  使能），睡眠等"发送器空"中断来发下一批。我们的 UART 从不报告
  IIR=0x02（TX 中断）→ 驱动睡死
- **修复**：THRE 状态下（恒真）使能 THRI 的瞬间挂起 TX 中断
- **教训**：**printk 正常 ≠ 串口正常**。轮询和中断是两条完全独立的
  路径，必须分别测试。

#### Bug 11：中断必须用电平语义（脉冲会被 LAPIC 漏采）
- **症状**：IRQ4 拉高几微秒就拉低，客户机收不到
- **根因**：LAPIC 的 LINT0 引脚是**电平采样**的，中断线必须保持到
  驱动读 IIR 应答
- **修复**：`_update_irq()`——RX/TX 任一挂起且 IER 使能 → 拉高；
  IIR 应答/条件消失 → 拉低

#### Bug 12：端口 0x61 bit5——TSC 校准的生死门
- **症状**：内核死循环读端口 0x61 共 49 万次
- **根因**：内核 TSC 校准（`quick_pit_calibrate`）用 PIT 通道 2 做
  65536 次倒计时（55ms），**轮询端口 0x61 的 bit5（通道 2 OUT 引脚）
  等它变高**。我们的模拟永远返回 0 → 永远等不到
- **修复**：实现通道 2 倒计时，`0x61` 读动态返回 bit5（到期后置位并
  锁存）
- **教训**：**VMM 模拟的每个端口位都可能绑着内核的一条死等循环**。

#### Bug 13：PIT 通道 2 的回绕语义
- **症状**：修完 bit5 后，内核测得"计数差=0" → 除零 oops
- **根因**：8254 计到 0 后**回绕 0xFFFF 继续减**（永不冻结）；我们的
  `max(0, count-gone)` 钳在 0 → 内核读到恒定值 → 差值 0 → 除零
- **修复**：`(count - gone) & 0xFFFF` + `expired` 标志锁存（模式 0 的
  OUT 引脚到期后保持高）

### D. CPU 特性层（CPUID/MSR——内核行为的开关）

#### Bug 14：CPUID 叶号写错
- **症状**：消毒循环从未命中，TSC 叶原样带着 0 值
- **根因**：CPUID **叶号就是 0x15**（十进制 21），我写成了
  0x40000015（和 KVM ioctl 编号规则记混了）
- **教训**：CPUID 叶号、MSR 索引、KVM ioctl 编号是三套独立编号空间，
  千万别互相污染。

#### Bug 15：CPUID leaf 0x15 除零
- **症状**：`divide error` oops 在 tsc_init
- **根因**：KVM 默认表里 leaf 0x15 的分母/分子为 0，内核
  `tsc_khz = crystal * numerator / denominator` 除零
- **修复**：填真实语义——EAX=100（分母）、EBX=2800（分子）、
  ECX=100MHz（晶体）→ TSC=2.8GHz

#### Bug 16：**SMAP——整个项目最隐蔽的根因**
- **症状**：修完上面所有，内核在 `text+0x1BF` 确定性 #PF，访问
  0x3A00000；页表遍历显示该项**存在且 2MB、用户位=1、可读写、已置脏**
- **根因**：KVM 默认 CPUID 透传宿主特性 → 内核看到 SMAP → 开启
  CR4.SMAP → 而 LEMU 建的早期恒等映射页带 `_PAGE_USER` → **CPL=0 访问
  用户页且 EFLAGS.AC=0 → SMAP 保护性故障**（不是缺页！页表遍历证明
  映射有效）
- **修复**：CPUID leaf 7 剥离 SMAP(bit20)/SMEP(bit7)/UMIP(ECX bit2)
- **教训**：#PF ≠ 缺页。**页存在时的 #PF 是权限/保护语义**——SMAP/
  SMEP/NX/WP 每一个都能产生它。判断依据是错误码，不是 CR2。
- **教训 2**：CPUID 透传是手写 VMM 的头号隐形杀手——你让客户机以为
  它有一块真 CPU，它就会按真 CPU 的规则给自己开保护，然后被你的
  "不完美物理世界"绊倒。**kvmtool 式白名单（只给最小特性集）是正解**。

#### Bug 17：MSR 直通（过滤器机制）
- 内核 TSC 校准还需 MSR 0xCE/0xCD（Platform Info / FSB 频率），
  KVM 默认对未知 MSR 注入 #GP
- **机制**：`KVM_ENABLE_CAP(KVM_CAP_X86_USER_SPACE_MSR=188, reason=
  KVM_MSR_EXIT_REASON_FILTER=4)` + `KVM_X86_SET_MSR_FILTER=0x4188AEC6`
  （位图：base=0xCD, 2 项, READ）→ RDMSR 触发 exit reason 27，用户态
  填 `kvm_run.msr.data`
- **坑**：位图指针必须活过 ioctl（临时 mmap 被 GC → EFAULT）

#### Bug 18：MSR 0xE01 写入警告
- `skl_uncore_msr_init_box` 写 MSR 0xE01（Skylake uncore 寄存器）——
  KVM 不模拟 → 打印警告但**非致命**（内核容错跳过）
- **教训**：区分"致命 #GP"和"警告后继续"——KVM 对多数未知 MSR 是
  报告后继续，内核也有容错路径。**不是每个警告都要修**。

### E. 架构层

#### Bug 19：单线程 stdin 死锁
- **症状**：shell 起来后输入永远无响应
- **根因**：主线程阻塞在 `KVM_RUN`（guest 阻塞读输入），永远轮不到
  读 stdin → 输入永远进不去 → 死锁
- **修复**：**输入走独立线程**，`KVM_IRQ_LINE` 拉线自带 vCPU kick，
  能强制唤醒阻塞的 KVM_RUN
- **教训**：**这就是 QEMU 多线程架构存在的原因**——设备 I/O 必须与
  vCPU 执行解耦。

#### Bug 20：IRQ0 心跳提前注入 → 早期静默 panic
- **症状**：加时钟线程后引导反而死得更早（连 earlyprintk 都没有）
- **根因**：客户机编程 PIT **之前**就脉冲 IRQ0——早期内核的 IDT/
  PIC 掩码没就绪，"不该来的中断" = 静默灾难
- **修复**：ticker 门控——**检测到客户机写 PIT 通道 0 编程命令后才开始**
  注入（真实硬件的时序就是如此）
- **教训**：**中断的"时序合法性"和"中断本身"一样重要**。

#### Bug 21：cmdline 的 nokaslr 丢了
- **症状**：修好一批 bug 后死点变成随机化地址
- **根因**：多轮修改中 `nokaslr` 参数从 cmdline 里被挤掉了 → KASLR
  把内核加载到随机地址，与最小环境的假设冲突
- **教训**：**引导参数是实验变量的一部分，改动要留痕**。建一个
  "golden cmdline" 常量，每次 diff。

---

## 4. 诊断工具箱（比修复更值钱的部分）

| 工具 | 作用 | 位置 |
|---|---|---|
| `probes/offsets_*.c` | gcc offsetof 实测结构体偏移 | 拒绝猜测 |
| `probes/probe_sregs.py` | sregs 三方对照（复位/写入/留存） | 定位"写没写进去" |
| `probes/probe_entry.py` | 裸机桩 + 诊断 IDT | 读出"哪个向量" fault |
| lemu `--trace` | 逐 exit 打印 RIP/原因 | 观察执行进度 |
| `dump_state()` | 关机自动页表遍历 + 故障指令字节转储 | 定位 #PF 根因 |
| QEMU 对照 | 同一内核+initramfs 能引导 → 排除内核侧问题 | 二分"谁的锅" |
| 会话日志 `logs/lemu-*.log` | 字节级记录全部串口流量 | 事后分析 |

**诊断心法**：
1. **先隔离**：裸机桩（8 字节机器码）能跑 → 环境没问题，查协议
2. **再看现场**：RIP/CR2/CR3 + 页表遍历（宿主直接读 guest 内存即可）
3. **不猜偏移**：offsetof 实测；不猜编号：头文件实测
4. **二分变量**：一次只改一个，golden cmdline 保持不变

---

## 5. 当前未解问题（诚实记录）

**现象**：nokaslr 下内核引导到 `text+0x1BF`（WRMSR EFER）确定性三重
故障，CR2=0x3A00000；带 `--cpuid --msr --net` 时死点移到 1.297s
（serial8250_init 的 overflow oops，CR2=direct map 64MB）。**两种死法
都无控制台输出**（earlyprintk 未及初始化）。

**已排除**：MSR 直通（有无均崩）、cmdline 参数、initramfs 内容、
ACPI 表（内核已接受）、内存大小。

**分析线索**：
- CR2=0x3A00000 的页表遍历显示该项**存在、2MB、USER 位=1、已置脏**——
  若是 CPL=0 数据访问则不应 #PF；**若是取指令则是 NX/SMEP 语义**——
  而 CR4 现场显示 OSPKE 开、PKE 关的不一致组合（CPUID 透传遗留），
  PTI 开启。下一步 = 64 位诊断 IDT（记录向量号）+ 逐 exit RIP 追踪，
  或换 Alpine/Firecracker 内核（对最小 VMM 更宽容）对照。

**备选路线**：`--cpuid` 白名单已去掉全部高级特性；可进一步把
`virtio_mmio` 从 ACPI 换成 Firecracker 内核 + cmdline 直启组合
（Firecracker 内核为极简 VMM 量身定做，qemu -kernel 直启验证过无数次）。

---

## 6. 内存级知识点索引（本项目的"课本"）

- **VMCS / VM-entry checks**：Bug 5——VMX 对 guest state 的逐字段
  硬件校验
- **kvm_run 共享页**：KVM 与用户态的"通话记录本"，偏移实测（Bug 1）
- **EPT 与 MMIO**：guest 访问未映射 GPA → KVM_EXIT_MMIO（virtio-mmio
  的通信基础）
- **SMAP/SMEP/US 位**：Bug 16——页表 USER 位 + CR4 位 + EFLAGS.AC 的
  三方博弈
- **8254 回绕**：Bug 13——16 位计数器的真实语义
- **16550 THRI**：Bug 10——"使能即触发"的中断语义与 16 字节 FIFO
- **LAPIC LINT0 电平采样**：Bug 11——脉冲会被漏采
- **in-kernel irqchip 的 hlt 行为**：HLT 不再产生 exit（内核态睡死）
  ——探针设计的关键约束
- **PCID / CR3 低 12 位**：页表遍历时必须 `cr3 & ~0xFFF`
- **PTI（页表隔离）**：内核/用户 CR3 双页表切换——异常 CR2 的来源
  判断

---

## 7. 快速命令

```bash
# 交互启动（LEMU 手写 VMM）
wsl && cd /mnt/e/ProjBuild/QIUZHAO/Sandbox/e2b-lab/kvm-demo && ./scripts/lemu.sh

# 带 QEMU 对照
./scripts/vm.sh

# 教学演示（16 字节机器码）
python3 vmm/kvm_minivm.py

# VM 输出落盘演示
python3 vmm/vm_logger.py
```

---

*记录时间：2026-09-19。当前断点与续接命令见仓库根 README 与会话记忆。*
