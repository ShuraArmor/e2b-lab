# -*- coding: utf-8 -*-
"""
一键启动器：python start_lab.py
  * 没有 Key  -> 自动打开 E2B 注册页，然后在这里盯着 .env，等你粘贴好自动开跑
  * 已有 Key  -> 直接依次运行 00 自检 + 01~05 全部实验，最后给出汇总报告

你唯一要做的事：注册账号并把 API Key 粘贴到 .env（这一步只能本人做，账号是你的）。
"""
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
DEMOS = ["01_quickstart", "02_commands_files", "03_code_interpreter", "04_lifecycle", "05_agent_pattern"]
DASHBOARD = "https://e2b.dev/dashboard"
POLL_SECONDS = 3
WAIT_LIMIT = 15 * 60


def read_key() -> str:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    return (os.environ.get("E2B_API_KEY") or "").strip()


def ensure_env_file():
    envp = ROOT / ".env"
    if not envp.exists():
        example = ROOT / ".env.example"
        envp.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return envp


def banner(text: str):
    print(f"\n{'#' * 66}\n# {text}\n{'#' * 66}")


def run_script(py_file: Path) -> int:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(py_file)], env=env).returncode


def main():
    no_wait = "--no-wait" in sys.argv
    envp = ensure_env_file()

    banner("E2B 实验工作台 · 一键启动")
    print(f"   项目目录 : {ROOT}")
    print(f"   配置文件 : {envp}")

    key = read_key()
    if not key:
        banner("缺少 API Key —— 已为你打开注册页，请完成这两步")
        print("   1. 注册/登录 E2B（免费，送一次性 $100 额度，无需信用卡）")
        print("   2. 在 Dashboard 的 API Keys 页创建密钥并复制")
        print(f"   3. 把密钥粘贴到 {envp} 里 E2B_API_KEY= 的后面，保存")
        print("      （我每 3 秒自动检测一次，检测到就立刻开跑，无需重启本脚本）")
        webbrowser.open(DASHBOARD)
        if no_wait:
            print("\n[--no-wait] 不等待，退出。")
            return 0
        print()
        deadline = time.time() + WAIT_LIMIT
        while time.time() < deadline:
            time.sleep(POLL_SECONDS)
            key = read_key()
            if key:
                print(f"\n>> 检测到 Key（{key[:8]}...），2 秒后自动开始全部实验 ...")
                time.sleep(2)
                break
            print("   等待 Key 中 ...", end="\r")
        else:
            print(f"\n等待超时（{WAIT_LIMIT // 60} 分钟）。贴好 Key 后重新运行本脚本即可。")
            return 1

    # ---- Key 就绪，开跑 ----
    banner("Step 0/5 · 环境自检")
    if run_script(ROOT / "demos" / "00_check_env.py") != 0:
        print("自检未通过，停止。")
        return 1

    results = {}
    for i, name in enumerate(DEMOS, start=1):
        banner(f"实验 {i}/5 · {name}  （边跑边看 https://e2b.dev/dashboard 的 Sandboxes 页）")
        rc = run_script(ROOT / "demos" / f"{name}.py")
        results[name] = "PASS" if rc == 0 else f"FAIL(exit={rc})"
        if rc != 0:
            print(f"\n!! {name} 运行出错，已停在半程。把上面的报错发我即可定位。")
            break

    banner("汇总报告")
    for name, rc in results.items():
        print(f"   [{rc:14s}] {name}")
    print("\n   本机产物在 out/ 目录（csv/图表）。")
    print("   成本提醒：所有脚本结尾都已自动 kill 沙盒；去 dashboard 可核对无遗留沙盒。")
    print("   概念复习：README.md —— 核心概念速查表 + 要点与坑 + 面试速记。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
