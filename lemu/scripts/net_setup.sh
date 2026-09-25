#!/usr/bin/env bash
# 创建 TAP 接口并配置 WSL 侧地址（root 运行）
set -e
ip tuntap add dev tap0 mode tap 2>/dev/null || true
ip addr add 10.0.2.2/24 dev tap0 2>/dev/null || true
ip link set tap0 up
echo "tap0 就绪: $(ip -4 addr show tap0 | grep inet)"
