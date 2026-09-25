#!/usr/bin/env bash
# ============================================================
#  LEMU-PY —— 引导带 Alpine/musl CPython 的 initramfs
#  用法：./scripts/lemu-py.sh
#  进 shell 后可敲 python3 进交互解释器；退出 Ctrl+C
# ============================================================
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -e /dev/kvm ] && [ -w /dev/kvm ] || { echo "[错误] /dev/kvm 不可用"; exit 1; }
for f in vmm/lemu.py images/bzImage-wsl images/initramfs-py.cpio.gz; do
    [ -f "$f" ] || { echo "[错误] 缺少 $f （先跑 scripts/initramfs_alpine_build.sh）"; exit 1; }
done

echo "[*] LEMU + Alpine/musl CPython 点火 ..."
exec python3 vmm/lemu.py \
    -k images/bzImage-wsl \
    -i images/initramfs-py.cpio.gz \
    -m 512
