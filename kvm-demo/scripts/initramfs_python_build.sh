#!/usr/bin/env bash
# ============================================================
# 制作带 CPython 的 initramfs（在 WSL 里以 root 运行）：
#   busybox + 宿主 python3(动态链接) + 依赖库 + 裁剪标准库 + demo.py
# 产物：images/initramfs-py.cpio.gz
# 用法：sudo bash scripts/initramfs_python_build.sh   （mknod 需要 root）
# ============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/images/initramfs-py.cpio.gz"
DEMO="$ROOT/guest/demo.py"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

PYREAL=$(readlink -f /usr/bin/python3)
PYVER=$("$PYREAL" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "[*] 解释器: $PYREAL (CPython $PYVER)"

# ---- 骨架 ----
mkdir -p "$WORK"/{bin,dev,proc,sys,tmp,opt,usr/bin,usr/lib}
cp /bin/busybox "$WORK/bin/"
mknod -m 600 "$WORK/dev/console" c 5 1

# ---- 解释器 + 依赖库 + 动态链接器（保持绝对路径布局）----
cd /
cp --parents "$PYREAL" "$WORK"
for lib in $(ldd "$PYREAL" | awk '$3 ~ /^\// {print $3}'); do
    cp --parents "$lib" "$WORK"
done
cp --parents /lib64/ld-linux-x86-64.so.2 "$WORK"     # 符号链接会被展开为实体文件

# ---- 标准库（裁掉与运行无关的大件）----
PYLIB=$("$PYREAL" -c 'import sysconfig;print(sysconfig.get_paths()["stdlib"])')
cp -r "$PYLIB" "$WORK/usr/lib/python$PYVER"
cd "$WORK/usr/lib/python$PYVER"
rm -rf config-* test idlelib lib2to3 tkinter turtledemo ensurepip \
        __pycache__ site-packages pydoc_data asyncio multiprocessing \
        curses sqlite3 2>/dev/null || true
find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find . -name '*.pyc' -delete 2>/dev/null || true

# ---- 便捷入口 /bin/python3 + 演示代码 ----
ln -s "/usr/bin/python$PYVER" "$WORK/bin/python3"
cp "$DEMO" "$WORK/opt/demo.py"

# ---- /init：挂载基本文件系统 -> 跑 Python 演示 -> 交出 shell ----
cat > "$WORK/init" <<EOF
#!/bin/busybox sh
/bin/busybox --install -s /bin
mkdir -p /proc /sys /dev /tmp
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
echo ""
echo "=============================================="
echo " LEMU + CPython：真 Python 即将在虚拟机里运行"
echo "=============================================="
python3 -u /opt/demo.py </dev/console >/dev/console 2>&1
echo "=============================================="
echo " 演示结束。提示符下敲 python3 可进入交互解释器"
echo "=============================================="
exec sh </dev/console >/dev/console 2>&1
EOF
chmod +x "$WORK/init"

# ---- 打包 ----
cd "$WORK"
find . | cpio -o -H newc --quiet | gzip -6 > "$DEST"
ORIG=$(du -sm "$WORK" | awk '{print $1}')
echo "[*] 原始体积 ${ORIG}MB -> 压缩后 $(ls -l "$DEST" | awk '{printf "%.1fMB", $5/1048576}')"
echo "[*] 完成: $DEST"
echo "[*] 启动: python3 vmm/lemu.py -k images/bzImage-wsl -i images/initramfs-py.cpio.gz -m 512"
