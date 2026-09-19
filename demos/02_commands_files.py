# -*- coding: utf-8 -*-
"""
02 命令与文件：SDK 的另外两大基本功 —— 控制进程、传输文件。

这是搭建"agent 工具"最常用的两类操作：
  * commands.run()  -> 每次一个全新进程（跑完即走，进程状态不保留）
  * run_code()      -> Jupyter 内核（变量状态跨调用保留）—— 03 演示
  * files.write/read-> 你本机和沙盒之间搬运文件

观察点：
  * 在沙盒里 pip install 的包只存在于这个沙盒，kill 后蒸发 —— 沙盒默认非持久。
  * 上传下载的本质：files.write 传 bytes 走 HTTPS，不存在"共享文件夹"。
"""
import io
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from e2b_code_interpreter import Sandbox  # noqa: E402


def step(title: str):
    print(f"\n{'=' * 60}\n>> {title}\n{'=' * 60}")


def main():
    sbx = Sandbox.create(timeout=300, metadata={"demo": "02_commands_files"})
    print(f"sandbox_id = {sbx.sandbox_id}")

    step("1. 带环境变量和工作目录执行命令")
    r = sbx.commands.run("echo $GREETING from $(pwd)", envs={"GREETING": "ni hao"}, cwd="/tmp")
    print(f"   {r.stdout.strip()}   (exit={r.exit_code})")

    step("2. 在沙盒里安装软件包（它有完整网络和 root 权限）")
    print("   ", sbx.commands.run("pip install humanize -q", timeout=120).stdout.strip() or "安装完成(无输出)")
    exec_result = sbx.run_code("import humanize; print(humanize.naturaltime(__import__('datetime').timedelta(seconds=3661)))")
    for line in exec_result.logs.stdout:
        print("    humanize 输出:", getattr(line, "line", line))
    print("    注意：这个包装在了沙盒里。kill 掉沙盒它就没了，不会污染你的机器。")

    step("3. 上传文件到沙盒 -> 用 pandas 分析 -> 把结果下载回本机")
    # 3.1 本地生成一份 CSV
    csv_text = "product,amount\nA,120\nB,340\nC,56\nB,44\nA,80\n"
    (OUT / "sales.csv").write_text(csv_text, encoding="utf-8")

    # 3.2 上传（bytes 走 HTTPS 进沙盒）
    sbx.files.write("/home/user/sales.csv", csv_text.encode("utf-8"))
    print("    已上传 /home/user/sales.csv")

    # 3.3 沙盒内分析
    exec_result = sbx.run_code(
        "import pandas as pd\n"
        "df = pd.read_csv('/home/user/sales.csv')\n"
        "report = df.groupby('product')['amount'].sum().sort_values(ascending=False)\n"
        "report.to_csv('/home/user/report.csv')\n"
        "print(report)"
    )
    for line in exec_result.logs.stdout:
        print("   [stdout]", getattr(line, "line", line))

    # 3.4 下载结果回本机
    report_bytes = sbx.files.read("/home/user/report.csv", format="bytes")
    (OUT / "report.csv").write_bytes(report_bytes)
    print(f"    已下载报告到 {OUT / 'report.csv'}")

    step("4. 后台进程：启动、查看、终止")
    handle = sbx.commands.run("sleep 600", background=True)  # 立即返回，不阻塞
    time.sleep(1)
    procs = sbx.commands.list()
    print(f"    沙盒内现有 {len(procs)} 个进程（含刚启动的 sleep）")
    handle.kill()
    print("    已把后台 sleep 杀掉")

    step("5. 清理")
    sbx.kill()
    print("沙盒已销毁。本机留下的只有 out/ 下的两个 csv —— 这就是 agent 交付物的典型形态。")


if __name__ == "__main__":
    main()
