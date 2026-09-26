#!/usr/bin/env bash
# ============================================================
# 制作 Alpine(musl) + CPython 的 initramfs（在 WSL 里以 root 运行）
#
# 为什么不用宿主 Ubuntu 的 Python：WSL glibc 按 x86-64-v3 编译，要求
# CPUID 报 AVX2 + 内核开 XSAVE(YMM)，而本机 WSL 内核的 KVM 把 CPUID
# leaf 0xD 所有子叶都服务成 0xD.0（索引匹配失效），内核 XSAVE 自检失败
# 关掉 OSXSAVE → glibc 拒载。musl 无 ISA 检查，整条链路全绕开。
#
# 流程：TUNA 下 Alpine minirootfs -> chroot 里 apk add python3（自动解依赖）
#       -> 裁剪 -> 塞进 initramfs + demo.py + /init
# 产物：images/initramfs-py.cpio.gz
# ============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/images/initramfs-py.cpio.gz"
DEMO="$ROOT/guest/demo.py"
MIRROR="https://mirrors.tuna.tsinghua.edu.cn/alpine"
VER="v3.19"
MINI="alpine-minirootfs-3.19.1-x86_64.tar.gz"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if [ -s "$ROOT/images/mini.tar.gz" ]; then
    echo "[*] 复用已下载的 $ROOT/images/mini.tar.gz"
    cp "$ROOT/images/mini.tar.gz" "$WORK/$MINI"
else
    echo "[*] 下载 Alpine minirootfs ($MINI) ..."
    curl -sL --max-time 180 --retry 2 -o "$WORK/$MINI" "$MIRROR/$VER/releases/x86_64/$MINI"
fi
ls -l "$WORK/$MINI"

AROOT="$WORK/alpine"
mkdir -p "$AROOT"
tar -xzf "$WORK/$MINI" -C "$AROOT"
cp /etc/resolv.conf "$AROOT/etc/resolv.conf"
echo "$MIRROR/$VER/main" > "$AROOT/etc/apk/repositories"

echo "[*] chroot 安装 python3（apk 自动解依赖）..."
chroot "$AROOT" /sbin/apk add --no-cache python3 pciutils >/dev/null
chroot "$AROOT" /usr/bin/python3 -V

echo "[*] 裁剪 ..."
rm -rf "$AROOT"/var/cache/apk/* "$AROOT"/etc/apk/{keys,protected_paths.d} \
       "$AROOT"/usr/lib/python3*/{test,idlelib,lib2to3,__pycache__,ensurepkg,ensurepip} \
       "$AROOT"/usr/lib/python3*/config-* 2>/dev/null || true
find "$AROOT" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "[*] 塞入演示代码和 /init ..."
cp "$DEMO" "$AROOT/opt/demo.py" 2>/dev/null || { mkdir -p "$AROOT/opt"; cp "$DEMO" "$AROOT/opt/demo.py"; }
cp "$ROOT/guest/pci_test.py" "$AROOT/opt/pci_test.py"
cp "$ROOT/guest/acc_driver.py" "$AROOT/opt/acc_driver.py"
cp "$ROOT/guest/acc_test.py" "$AROOT/opt/acc_test.py"
cat > "$AROOT/init" <<'EOF'
#!/bin/busybox sh
/bin/busybox --install -s /bin
mkdir -p /proc /sys /dev /tmp
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
echo INIT-MARK-1-START
/bin/busybox --install -s /bin
echo INIT-MARK-2-INSTALL-OK
mkdir -p /proc /sys /dev /tmp
python3 /opt/acc_test.py || echo ACC-TEST-FAILED
echo ""
echo "=============================================="
echo " LEMU + Alpine/musl + CPython"
echo "=============================================="
python3 -u /opt/demo.py </dev/console >/dev/console 2>&1
echo "=============================================="
echo " 演示结束。提示符下敲 python3 可进入交互解释器"
echo "=============================================="
exec sh </dev/console >/dev/console 2>&1
EOF
chmod +x "$AROOT/init"

echo "[*] 打包 ..."
cd "$AROOT"
find . | cpio -o -H newc --quiet | gzip -6 > "$DEST"
echo "[*] 原始 $(du -sm "$AROOT" | awk '{print $1}')MB -> $(ls -l "$DEST" | awk '{printf "%.1fMB", $5/1048576}')"
echo "[*] 完成: $DEST"
echo "[*] 启动: python3 vmm/lemu.py -k images/bzImage-wsl -i images/initramfs-py.cpio.gz -m 512"
