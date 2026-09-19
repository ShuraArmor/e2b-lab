# -*- coding: utf-8 -*-
"""
本地对照实验：不用任何 E2B 服务和 API Key，用 Docker 模拟"给 agent 一个沙盒"，
亲眼看一遍 E2B 帮你自动化的那些事：隔离、断网、限资源、超时强杀、用完即焚。

与 E2B 的关系：
  * E2B 沙盒 = Firecracker 微虚拟机（独立内核），本实验 = Docker 容器（共享宿主内核）。
    容器隔离弱于微虚拟机，但原理同构，足够你理解"沙盒服务"在干什么。
  * 这里每一步都是 E2B SDK 一行代码就能做到的事（对照见每步注释）。

运行：.venv 里的 python 直接跑即可（需要 Docker Desktop 在运行）。
"""
import subprocess
import time
import uuid

IMAGE = "python:3.12-slim"


def step(title: str):
    print(f"\n{'=' * 60}\n>> {title}\n{'=' * 60}")


def hardened_flags(name: str) -> list:
    """给 agent 生成的代码准备的"紧箍咒"参数，每一条都是一道锁。"""
    return [
        "docker", "run",
        "--rm",                      # 用完即焚（E2B: kill / timeout 到期销毁）
        "--name", name,
        "-i",                        # 代码从 stdin 喂进去，免挂载
        "--network=none",            # 断网（E2B: allow_internet_access=False）
        "--memory=256m",             # 内存上限（E2B: 创建时选 RAM 规格）
        "--cpus=1",                  # CPU 上限
        "--pids-limit=128",          # 防 fork 炸弹
        "--cap-drop=ALL",            # 丢弃全部 Linux capabilities
        "--security-opt=no-new-privileges",
        "--read-only",               # 根文件系统只读，只能写 /tmp
        "--tmpfs", "/tmp:rw,size=32m",
        IMAGE, "python", "-",
    ]


def run_code_in_sandbox(code: str, timeout: float = 20.0):
    name = f"mini-sbx-{uuid.uuid4().hex[:8]}"
    cmd = hardened_flags(name)
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, input=code.encode("utf-8"),
                           capture_output=True, timeout=timeout)
        return {
            "stdout": p.stdout.decode("utf-8", errors="replace"),
            "stderr": p.stderr.decode("utf-8", errors="replace"),
            "exit_code": p.returncode,
            "elapsed": time.perf_counter() - t0,
        }
    except subprocess.TimeoutExpired:
        # 超时强杀：对失控的 agent 代码，宿主机必须始终握有生杀权
        subprocess.run(["docker", "kill", name], capture_output=True)
        return {"stdout": "", "stderr": f"超时({timeout}s)，已强制终止",
                "exit_code": -1, "elapsed": time.perf_counter() - t0}


def main():
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True)
    except Exception:
        print("Docker 不可用：请先启动 Docker Desktop 再运行本脚本。")
        return

    step("0. 准备镜像（首次会自动拉取 ~45MB）")
    subprocess.run(["docker", "pull", IMAGE], capture_output=True)

    step("1. 良性代码：正常执行（E2B: sbx.run_code(code)）")
    r = run_code_in_sandbox(
        "import platform\n"
        "print('我在', platform.system(), platform.machine(), '里')\n"
        "print('1 + 1 =', 1 + 1)"
    )
    print(f"   {r['stdout'].strip()}")
    print(f"   (exit={r['exit_code']}, 耗时 {r['elapsed']:.2f}s —— 注意是 Linux，不是你的 Windows)")

    step("2. 失控代码 A：agent 想联网外传数据 -> 网络已锁死")
    r = run_code_in_sandbox(
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('https://e2b.dev', timeout=5)\n"
        "    print('居然联网成功了')\n"
        "except Exception as e:\n"
        "    print('联网被拒绝:', type(e).__name__)"
    )
    print(f"   {r['stdout'].strip()}")

    step("3. 失控代码 B：agent 想破坏文件系统 -> 根目录只读")
    r = run_code_in_sandbox(
        "try:\n"
        "    open('/etc/passwd', 'a').write('hacked')\n"
        "    print('写入成功？!')\n"
        "except Exception as e:\n"
        "    print('写入被拒绝:', type(e).__name__, e)"
    )
    print(f"   {r['stdout'].strip()}")

    step("4. 失控代码 C：agent 写了死循环 -> 超时强杀（E2B: timeout 机制）")
    r = run_code_in_sandbox("while True: pass", timeout=8.0)
    print(f"   {r['stderr'].strip()}  (耗时 {r['elapsed']:.2f}s，宿主机安然无恙)")

    step("5. 复盘：你刚手动做的每一件事，E2B 都封装成了一行 API")
    print("""
   本实验                          E2B 对应物
   ----------------------------------------------------------
   docker run --rm                 Sandbox.create() / kill() / timeout 到期自动销毁
   --network=none                  allow_internet_access=False
   --memory / --cpus               创建时选择 vCPU / RAM 规格
   --read-only + --tmpfs           沙盒内文件系统隔离 + /home/user 可写区
   超时 docker kill                 run_code(timeout=..) / 沙盒 timeout 护栏
   容器(共享宿主内核)               Firecracker microVM(独立内核，隔离更强)
   每次冷启动 ~1s                  模板快照恢复，resume 仅几百毫秒
   ----------------------------------------------------------
   E2B 的价值 = 把这套纪律做成多租户、毫秒级、带编排和计费的商品。
""")


if __name__ == "__main__":
    main()
