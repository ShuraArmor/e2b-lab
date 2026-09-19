# mini-KVM Linux 实验室

在你的 Windows 机器上（WSL2 + 嵌套虚拟化），亲手造 KVM 虚拟机、引导真 Linux 内核、
并以交互终端的方式使用它。从"三句 ioctl 造机器"到"开箱即用的虚拟机程序"的完整学习路径。

> 前置：WSL2（Ubuntu）、`/dev/kvm` 可用、`qemu-system-x86`（VMM 工具）。
> 使用者的宿主机角色：WSL2 内核 6.6 / CPU VT-x 嵌套虚拟化。

## 目录结构

```
kvm-demo/
├── vm.bat / vm-attach.bat   # Windows 双击入口：启动新机 / 接管后台会话
├── scripts/
│   ├── vm.sh                # 交互启动器（QEMU 版，对照参考）
│   └── lemu.sh              # 交互启动器（LEMU 纯手写 VMM 版）✅
├── vmm/                     # 手写 VMM（无 QEMU，纯 /dev/kvm ioctl）
│   ├── lemu.py              # ⭐ 完整手写 VMM：64位引导协议+长模式+16550+CPUID/MSR ✅
│   ├── kvm_minivm.py        # 130 行最小 VMM：造 VM，跑 16 字节客户机程序 ✅
│   ├── vm_logger.py         # VM 输出捕获落盘（串口截获 → 时间戳 → 日志文件）✅
│   └── kvm_linux.py         # lemu.py 的前身（32位入口路线，已被 64 位方案取代）
├── guest/
│   └── initramfs_build.sh   # 构建客户机 rootfs（busybox + /init，产物进 images/）
├── images/                  # 虚拟机的"硬件随机附带物"
│   ├── bzImage-wsl          # 16MB，WSL2 官方内核 6.6（作客户机内核）
│   └── initramfs.cpio.gz    # 1.1MB 迷你 rootfs（构建产物）
├── probes/                  # 调试探针（排查 kvm_linux.py 引导问题的工具）
│   ├── offsets_kvmrun.c     # gcc offsetof 实测 kvm_run 结构偏移
│   ├── offsets_sregs.c      # 同上，sregs/regs 全量偏移
│   ├── probe_sregs.py       # 段寄存器三方对照（复位值/写入值/留存值）
│   └── probe_entry.py       # VM-entry 隔离测试 + 诊断 IDT（可读出异常向量号）
└── logs/                    # 运行产物（serial-*.log 会话记录、vm_output.log）
```

## 快速开始

双击 `vm.bat`（或在 WSL 里 `./scripts/vm.sh`）→ 约 2 秒引导进 busybox shell → 敲命令 →
`Ctrl+A` 后按 `X` 退出。每次会话自动记录到 `logs/`。

双击 `vm-attach.bat` 接管已在后台运行的虚拟机（tmux 会话 `vm`）。

## 三个层次的学习路径

| 层次 | 文件 | 你会看到 |
|---|---|---|
| ① KVM 原语 | `vmm/kvm_minivm.py` | 三句 ioctl 造 VM，16 字节客户机程序在 CPU 非根模式执行 |
| ② 宿主截获 IO | `vmm/vm_logger.py` | 客户机 out 指令 → VM exit → 宿主写日志文件 |
| ③ 完整虚拟机（QEMU 版） | `scripts/vm.sh` | 真 Linux 内核 2 秒引导、交互 shell |
| ④ 完整虚拟机（**纯手写**） | `scripts/lemu.sh` → `vmm/lemu.py` | 零 QEMU：自写 64 位引导协议 + 16550 + CPUID/MSR 直通，引导同一内核 |

`vmm/lemu.py` 是本项目核心成果——**QEMU 角色的纯手写实现**，包含：64 位引导协议
（恒等映射页表/CR3/EFER.LMA）、KVM_CREATE_IRQCHIP + 手动 PIT tick、16550 UART
（TX/RX 中断 + 电平触发 IRQ + loopback 探测兼容）、CPUID 表灌入、MSR 直通过滤器
（0xCE/0xCD 路由到宿主）、stdin 独立线程（kick 阻塞 vCPU）。`probes/` 里的调试
工具记录了每个 bug 的定位过程。

## 常用命令

```bash
# 重建客户机 rootfs（改 /init 脚本后）
sudo bash guest/initramfs_build.sh

# 手动启动（等价 vm.sh 的核心命令）
qemu-system-x86_64 -accel kvm -m 256 -smp 1 \
  -kernel images/bzImage-wsl -initrd images/initramfs.cpio.gz \
  -append "console=ttyS0 rdinit=/init nokaslr" -nographic -no-reboot

# 调整配置：-m 1024（内存 MiB）  -smp 2（vCPU 数）  -cpu host（暴露真实 CPU 特性）
```

## 实测参考数据

| 项目 | 数值 |
|---|---|
| 内核引导到 shell | ~2 秒（KVM 加速；纯软件模拟需数十倍） |
| VM exit 往返 | ~0.4ms（Python VMM 逐字节捕获时） |
| 空闲宿主开销 | vCPU 线程 0.1% CPU（客户机 hlt 睡眠时不耗宿主资源） |
| 整台机器 footprint | 16MB 内核 + 1.1MB rootfs，无磁盘，销毁即无痕 |
