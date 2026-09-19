# -*- coding: utf-8 -*-
"""
01 快速上手：创建沙盒 -> 执行命令 -> 执行代码 -> 看文件系统 -> 销毁。

运行前：确认 00_check_env.py 全部 PASS。

观察点：
  * 打开 https://e2b.dev/dashboard 的 Sandboxes 页面，脚本运行时你会看到
    一行新的沙盒记录实时出现，kill 之后消失（或标记为 terminated）。
  * 沙盒是 Linux —— 虽然你在 Windows 上写的代码，uname 输出的是沙盒里的 Linux 内核。
  * 记一下 create() 的耗时：这是云端冷启动一个完整微虚拟机的时间（通常 2~5 秒），
    而 pause 后 resume 只要几百毫秒（04 会演示）。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from e2b_code_interpreter import Sandbox  # noqa: E402


def step(title: str):
    print(f"\n{'=' * 60}\n>> {title}\n{'=' * 60}")


def main():
    step("1. 创建沙盒（云端一台完整 Linux 微虚拟机）")
    t0 = time.perf_counter()
    sbx = Sandbox.create(timeout=300, metadata={"demo": "01_quickstart"})
    print(f"   创建耗时 {time.perf_counter() - t0:.2f}s")
    print(f"   sandbox_id = {sbx.sandbox_id}   <- 记住它，任何机器都能靠这个 id 重连")

    step("2. 执行 shell 命令：看看沙盒里是什么世界")
    r = sbx.commands.run("uname -a && whoami && python3 --version && nproc")
    print(f"   {r.stdout.strip()}")
    print(f"   (exit_code={r.exit_code}；沙盒里是 Linux + 预装 Python，不是你的 Windows)")

    step("3. 用 run_code 执行 Python（沙盒里预置了 Jupyter 内核）")
    exec_result = sbx.run_code(
        "import sys, platform\n"
        "print('hello from', platform.python_version())\n"
        "print('sandbox os:', sys.platform)"
    )
    # exec.logs 是 print 的输出；exec.text 是“最后一个表达式的值”
    for line in exec_result.logs.stdout:
        print("   [stdout]", getattr(line, "line", line))
    if exec_result.error:
        print("   [error]", exec_result.error.name, exec_result.error.value)

    step("4. 看一眼沙盒的文件系统（一个真实的 Linux 根目录）")
    entries = sbx.files.list("/")
    print("   /", "  ".join(e.name for e in entries[:12]), "...")

    step("5. 销毁沙盒（不做这步它也会在 timeout 后被云端回收，但要及时主动清理）")
    sbx.kill()
    print(f"   is_running = {sbx.is_running()}")
    print("\n完成。去 dashboard 刷新看看，这条沙盒记录已经结束了。")


if __name__ == "__main__":
    main()
