# kvm-demo —— 基于 /dev/kvm 的手写 VMM 学习仓库

> 从"三句 ioctl 造一台机器"到"引导 Linux 6.6 并登录 shell"的完整学习仓库。
> 零 QEMU 依赖（QEMU 仅作对照参考）。所有代码可在 WSL2（嵌套虚拟化）中运行。

## 目录结构

```
kvm-demo/
├── README.md                  # 本文件
├── LEMU_JOURNEY.md            # ⭐ 21 个 bug 的完整踩坑记录（课堂讲义）
├── vm.bat / vm-attach.bat     # Windows 双击入口（QEMU 对照版）
├── scripts/
│   ├── lemu.sh                # LEMU 交互启动（纯手写 VMM）
│   ├── vm.sh                  # QEMU 对照启动
│   ├── net_setup.sh           # 创建 tap0（网络实验前置，root）
│   └── win_bridge.py          # 8080 → 10.0.2.15:80 转发器（Windows 访问）
├── vmm/                       # 手写 VMM 源码
│   ├── lemu.py                # ⭐ 主项目：64 位引导协议 + 16550 + CPUID 白名单
│   │                          #    + MSR 直通 + virtio-mmio 网卡 + 多线程架构
│   ├── virtio_net.py          # virtio-net-mmio 网卡 + TAP 桥（lemu 的网络设备）
│   ├── kvm_minivm.py          # 入门①：130 行最小 VMM，三句 ioctl 造机器
│   └── vm_logger.py           # 入门②：VM exit 字节捕获落盘演示
├── guest/                     # 客户机构建
│   ├── initramfs_build.sh     # rootfs 构建（busybox + eth0 + httpd + shell）
│   └── acpi/dsdt.dsl          # ACPI DSDT 源码（LNRO0005 virtio 设备描述）
├── images/
│   ├── bzImage-wsl            # 客户机内核（WSL2 官方 6.6，16MB）
│   └── initramfs.cpio.gz     # 构建 rootfs（busybox + eth0 + httpd）
├── probes/                    # 调试探针（LEMU_JOURNEY 中的诊断工具）
│   ├── offsets_kvmrun.c       # kvm_run 偏移实测（gcc offsetof）
│   ├── offsets_sregs.c        # sregs/regs 全量偏移
│   ├── offsets_msr.c          # MSR 过滤器 ioctl 编号
│   ├── probe_sregs.py         # 段寄存器三方对照
│   └── probe_entry.py         # VM-entry 隔离测试 + 诊断 IDT
└── logs/                      # 运行产物（会话记录、崩溃取证）
```

## 快速开始

```bash
wsl                              # 进入 WSL2 Ubuntu
cd /mnt/e/ProjBuild/QIUZHAO/Sandbox/e2b-lab/kvm-demo

# ① 入门：三句 ioctl 造一台最小机器
python3 vmm/kvm_minivm.py

# ② 入门：截获 VM 的每个输出字节
python3 vmm/vm_logger.py

# ③ 主线：手写 VMM 引导 Linux 到交互 shell（Ctrl+C 退出）
./scripts/lemu.sh

# ④ 网络：shell 里 ifconfig eth0 && ping 10.0.2.2（宿主侧 tap0=10.0.2.2）
# ⑤ QEMU 对照（同一内核/initramfs）
bash scripts/vm.sh
```

前置：`/dev/kvm` 可用（WSL2 嵌套虚拟化）+ `qemu-system-x86`（仅 vm.sh 需要）
+ root 或 kvm 组权限。

## 学习路径建议

```
① kvm_minivm.py      三句 ioctl、kvm_run 共享页、VM exit 循环
        ↓
② vm_logger.py       设备模拟的第一课：截获端口写入
        ↓
③ LEMU_JOURNEY.md    21 个 bug 的完整踩坑记录（强烈建议对照 lemu.py 源码读）
        ↓
④ lemu.py            64 位引导协议 / CPUID 白名单 / MSR 直通 / UART 中断
        ↓
⑤ virtio_net.py      virtio-mmio + TAP：真·设备驱动的另一半
        ↓
⑥ QEMU 源码 / Firecracker  工业级参照（同一 ioctl 集合的工程化）
```

## 当前状态（2026-09-19）

| 里程碑 | 状态 |
|---|---|
| 基础 VMM（实模式机器码） | ✅ |
| VM 输出捕获落盘 | ✅ |
| 引导 Linux 6.6 到 busybox shell（串口交互） | ✅ |
| TSC 频率检测（CPUID leaf 0x15） | ✅ 2800 MHz |
| virtio-net 枚举（ACPI LNRO0005） | 🔶 MMIO 偏移修复后待复验 |
| eth0 + httpd + Windows 访问 | ⏳ 依赖上一项 |

已知问题与调试线索见 `LEMU_JOURNEY.md` §5 和 `probes/`。
