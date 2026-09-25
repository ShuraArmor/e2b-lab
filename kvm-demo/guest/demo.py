#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo.py —— 运行在手写 VMM（LEMU）里的 Linux 上的 CPython 演示

你看到的每一个字都是这个链条产出的：
  Windows 终端 <- LEMU(Python/宿主) <- 16550串口 <- ttyS0 <- init <- CPython <- KVM <- Linux 6.6
"""
import sys
import os
import time
import math
import random
import platform
from collections import Counter
from functools import lru_cache

T0 = time.monotonic()


def head(title):
    print(flush=True)
    print("=" * 62, flush=True)
    print(f"  {title}", flush=True)
    print("=" * 62, flush=True)


# ---------- 第 1 幕：我是谁 ----------
head("1. 解释器身份")
print(f"  CPython 版本 : {platform.python_version()}", flush=True)
print(f"  实现平台     : {sys.platform} / {platform.machine()}", flush=True)
print(f"  解释器路径   : {sys.executable}", flush=True)
print(f"  CPU 核心数   : {os.cpu_count()}", flush=True)
print(f"  内建模块数   : {len(sys.builtin_module_names)}", flush=True)
print(f"  stdlib 位置  : {sys.prefix}", flush=True)


# ---------- 第 2 幕：浮点与复数 —— ASCII 曼德博集合 ----------
head("2. 曼德博分形（纯浮点计算，在虚拟 CPU 上逐点迭代）")

def mandelbrot(width=66, height=24, max_iter=40):
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            re = -2.1 + 2.8 * x / width
            im = -1.2 + 2.4 * y / height
            z = complex(re, im)
            n = 0
            while abs(z) < 2.0 and n < max_iter:
                z = z * z + complex(re, im)
                n += 1
            row.append(" .:-=+*#%@"[min(n * 10 // max_iter, 9)])
        rows.append("".join(row))
    return rows

t = time.monotonic()
for line in mandelbrot():
    print("  " + line, flush=True)
print(f"  [耗完 {time.monotonic()-t:.2f}s，{(66*24):,} 个点 * 最多 40 次复数迭代]", flush=True)


# ---------- 第 3 幕：面向对象 —— 康威生命游戏 ----------
head("3. 生命游戏（类 + 集合运算，看它在串口上活起来）")

class Life:
    NEIGHBORS = [(-1, -1), (-1, 0), (-1, 1), (0, -1),
                 (0, 1), (1, -1), (1, 0), (1, 1)]

    def __init__(self, cells):
        self.cells = set(cells)

    def step(self):
        counts = Counter(
            (r + dr, c + dc)
            for (r, c) in self.cells
            for (dr, dc) in self.NEIGHBORS
        )
        self.cells = {
            cell
            for cell, n in counts.items()
            if n == 3 or (n == 2 and cell in self.cells)
        }

    def render(self, rows=14, cols=44):
        return ["  " + "".join("@" if (r, c) in self.cells else "."
                               for c in range(cols))
                for r in range(rows)]

# 滑翔机 + R 五连块
seed = {(1, 2), (2, 3), (3, 1), (3, 2), (3, 3),          # R-pentomino
        (1, 30), (2, 31), (3, 29), (3, 30), (3, 31)}      # 滑翔机
game = Life(seed)
for gen in range(8):
    print(f"  第 {gen} 代  活细胞: {len(game.cells)}", flush=True)
    for line in game.render():
        print(line, flush=True)
    time.sleep(0.15)
    game.step()


# ---------- 第 4 幕：算法 —— 素数筛 + 记忆化递归 ----------
head("4. 素数筛（20 万以内）+ 斐波那契（lru_cache 记忆化）")

def sieve(n):
    is_prime = bytearray([1]) * n
    is_prime[0:2] = b"\x00\x00"
    for p in range(2, int(math.isqrt(n)) + 1):
        if is_prime[p]:
            is_prime[p * p::p] = bytearray(len(is_prime[p * p::p]))
    return sum(is_prime)

t = time.monotonic()
count = sieve(200_000)
dt = time.monotonic() - t
print(f"  200,000 以内素数: {count} 个  [{dt:.2f}s]", flush=True)

@lru_cache(maxsize=None)
def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)

t = time.monotonic()
result = fib(300)
print(f"  fib(300) = {result}", flush=True)
print(f"  [63 位十进制大整数，{time.monotonic()-t:.4f}s —— 任意精度算术]", flush=True)


# ---------- 第 5 幕：随机的味道 ----------
head("5. 伪随机数（内核 getrandom 系统调用供熵）")
random.seed(42)
rolls = Counter(random.randint(1, 6) for _ in range(60_000))
for face in sorted(rolls):
    bar = "#" * (rolls[face] // 300)
    print(f"  骰子 {face}: {rolls[face]:>6}  {bar}", flush=True)


# ---------- 收尾 ----------
head("完成")
data = open(__file__, "rb").read()
try:
    import hashlib
    sha = hashlib.sha256(data).hexdigest()[:16]
    print(f"  本文件 SHA256 前 16 位: {sha}", flush=True)
except ImportError:
    print(f"  本文件大小: {len(data)} 字节", flush=True)
print(f"  总耗时: {time.monotonic() - T0:.2f}s", flush=True)
print(flush=True)
print("  这段输出由运行在手写 VMM (LEMU) 里的 CPython 生成：", flush=True)
print("  宿主 Windows -> WSL2 -> LEMU(ioctl) -> /dev/kvm -> VT-x/EPT -> 本进程", flush=True)
print(flush=True)
