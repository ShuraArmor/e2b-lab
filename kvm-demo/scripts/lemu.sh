#!/usr/bin/env bash
# ============================================================
#  LEMU —— 纯手写 VMM 交互启动器（零 QEMU，纯 /dev/kvm ioctl）
#  用法：在 WSL 里  ./scripts/lemu.sh
#  退出：Ctrl+C
# ============================================================
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -e /dev/kvm ] && [ -w /dev/kvm ] || { echo "[错误] /dev/kvm 不可用"; exit 1; }
for f in vmm/lemu.py images/bzImage-wsl images/initramfs.cpio.gz; do
    [ -f "$f" ] || { echo "[错误] 缺少 $f"; exit 1; }
done

clear
cat <<'BANNER'
+========================================================+
|                                                        |
|    LEMU  --  纯手写 VMM（无 QEMU）即将引导 Linux        |
|                                                        |
|   栈  : 本脚本 -> lemu.py(ioctl+设备模拟) -> /dev/kvm   |
|         -> VT-x/EPT -> Linux 6.6 (客户机)               |
|   设备 : 16550 串口(带 TX/RX 中断) + 内核态PIC/PIT      |
|   退出 : Ctrl+C                                         |
|                                                        |
+========================================================+
BANNER
echo "[*] 点火 ..."
sleep 1
exec python3 vmm/lemu.py \
    -k images/bzImage-wsl \
    -i images/initramfs.cpio.gz \
    -m 512
