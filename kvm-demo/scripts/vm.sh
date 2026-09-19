#!/usr/bin/env bash
# ============================================================
#  mini-KVM Linux 交互式启动器
#  用法：在 WSL 里  ./scripts/vm.sh
#  退出：Ctrl+A 松开后再按 X
# ============================================================
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ---- 环境自检 ----
if [ ! -e /dev/kvm ]; then
    echo "[错误] 找不到 /dev/kvm —— 需要 WSL2 且嵌套虚拟化可用"
    exit 1
fi
if [ ! -w /dev/kvm ]; then
    echo "[错误] 没有 /dev/kvm 权限 —— 用 'sudo ./scripts/vm.sh' 或把用户加入 kvm 组"
    exit 1
fi
for f in images/bzImage-wsl images/initramfs.cpio.gz; do
    [ -f "$f" ] || { echo "[错误] 缺少 $f"; exit 1; }
done
command -v qemu-system-x86_64 >/dev/null || { echo "[错误] 未安装 qemu-system-x86"; exit 1; }

# ---- 横幅 ----
clear
cat <<'BANNER'
+========================================================+
|                                                        |
|      mini-KVM Linux  --  一台真实的虚拟机已就绪         |
|                                                        |
|   内核 : WSL2 6.6 (images/bzImage-wsl, 16MB)           |
|   根fs : busybox initramfs (1.1MB, 内存盘)             |
|   加速 : /dev/kvm  (CPU 硬件虚拟化 VT-x)               |
|   命令 : 试试 uname -a  /  ls /  /  cat /proc/cpuinfo  |
|   退出 : Ctrl+A 松开后再按 X                           |
|   日志 : 每次会话自动记录到 logs/serial-*.log          |
|                                                        |
+========================================================+
BANNER
echo "[*] 正在启动虚拟机 ..."
sleep 1

# ---- 启动（stdio 直通串口 + tee 自动记录会话）----
mkdir -p logs
LOGFILE="logs/serial-$(date +%m%d-%H%M%S).log"
qemu-system-x86_64 \
    -accel kvm \
    -m 256 -smp 1 \
    -kernel images/bzImage-wsl \
    -initrd images/initramfs.cpio.gz \
    -append "console=ttyS0 rdinit=/init nokaslr" \
    -nographic -no-reboot 2>&1 | tee "$LOGFILE"

# ---- 关机后 ----
echo
echo "[*] 虚拟机已关闭。本次会话已记录到: $LOGFILE"
