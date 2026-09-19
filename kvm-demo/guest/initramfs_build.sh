#!/usr/bin/env bash
# 制作迷你 initramfs：busybox + 一个 /init（挂载基本文件系统后起 shell）
# 在 WSL2 里以 root 运行：bash initramfs_build.sh
set -euo pipefail
DEST="$(cd "$(dirname "$0")/.." && pwd)/images/initramfs.cpio.gz"
WORK=$(mktemp -d)
mkdir -p "$WORK"/{bin,dev,proc,sys,tmp}
cp /bin/busybox "$WORK/bin/"
mknod -m 600 "$WORK/dev/console" c 5 1
cat > "$WORK/init" <<'EOF'
#!/bin/busybox sh
/bin/busybox --install -s /bin
mkdir -p /proc /sys /dev /tmp /www
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null
ifconfig eth0 10.0.2.15 netmask 255.255.255.0 up
route add default gw 10.0.2.2
echo "<h1>HELLO FROM LEMU!</h1><p>纯手写 VMM LEMU 托管的 Linux</p>" > /www/index.html
httpd -p 80 -h /www
echo "================================================"
echo " LEMU Linux 就绪: eth0=10.0.2.15  httpd=:80"
echo "================================================"
exec sh </dev/console >/dev/console 2>&1
EOF
chmod +x "$WORK/init"
cd "$WORK"
find . | cpio -o -H newc --quiet | gzip -1 > "$DEST"
echo "initramfs 完成: $(ls -l "$DEST" | awk '{print $5}') bytes"
