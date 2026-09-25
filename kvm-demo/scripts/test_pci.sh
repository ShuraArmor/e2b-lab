#!/usr/bin/env bash
# 现代 PCI 路径 + 加速卡 验证运行器
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GCMDS='echo ===PCI-TEST-BEGIN===
lspci -nn
dmesg | grep -iE "pci 0000|mcfg|pnp0a08|host bridge" | head -8
python3 /opt/acc_test.py
echo ===PCI-TEST-END==='

(sleep 40; printf '%s\n' "$GCMDS"; sleep 20) | \
    timeout 110 python3 vmm/lemu.py -k images/bzImage-wsl \
        -i images/initramfs-py.cpio.gz -m 512 --max-seconds 85
