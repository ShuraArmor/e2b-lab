# -*- coding: utf-8 -*-
"""
03 Code Interpreter：E2B 最出圈的能力 —— run_code 的正确打开方式。

四个核心机制，每个都直接影响你写 agent 工具层：
  1) 状态持久：同一个沙盒里，前一次 run_code 定义的变量/函数，后一次还能用
     （因为沙盒里跑的是一个常驻的 Jupyter 内核，不是一次性脚本）。
  2) context 隔离：create_code_context() 可以开出多个互不干扰的"内核会话"。
  3) 富结果：matplotlib 图表会作为 Result 返回（PNG 等格式），agent 可以把图发给用户。
  4) 错误回传：代码抛异常 -> exec.error 里带回完整 traceback -> 喂回给模型让它自己改，
     这就是"agent 自我修复"闭环的原料。

观察点：
  * 图表不是在沙盒里"截图"传回来的，而是内核产生的 PNG 字节 —— 渲染发生在你的机器。
  * 故意触发的那段报错代码，注意看 error 的 name/value/traceback 结构。
"""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from e2b_code_interpreter import Sandbox  # noqa: E402


def step(title: str):
    print(f"\n{'=' * 60}\n>> {title}\n{'=' * 60}")


def show_logs(exec_result):
    for line in exec_result.logs.stdout:
        print("   [stdout]", getattr(line, "line", line))
    for line in exec_result.logs.stderr:
        print("   [stderr]", getattr(line, "line", line))


def main():
    sbx = Sandbox.create(timeout=300, metadata={"demo": "03_interpreter"})
    print(f"sandbox_id = {sbx.sandbox_id}")

    step("1. 状态跨调用持久：第一个 cell 定义的变量，第二个 cell 还在")
    sbx.run_code("secret = 41")                       # cell 1
    exec_result = sbx.run_code("print(secret + 1)")   # cell 2 —— 同一内核
    show_logs(exec_result)

    step("2. context 隔离：两个内核会话同名变量互不干扰")
    ctx_a = sbx.create_code_context()
    ctx_b = sbx.create_code_context()
    sbx.run_code("x = '我是 A 会话'", context=ctx_a)
    sbx.run_code("x = '我是 B 会话'", context=ctx_b)
    ra = sbx.run_code("print(x)", context=ctx_a)
    rb = sbx.run_code("print(x)", context=ctx_b)
    show_logs(ra)
    show_logs(rb)

    step("3. 富结果：在沙盒里画图，把 PNG 拉回本机")
    exec_result = sbx.run_code(
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        "x = np.linspace(0, 10, 200)\n"
        "plt.figure(figsize=(6, 3))\n"
        "plt.plot(x, np.sin(x), label='sin')\n"
        "plt.plot(x, np.cos(x), label='cos')\n"
        "plt.legend()\n"
        "plt.title('plotted inside the sandbox')\n"
        "plt.show()"
    )
    png_b64 = next((res.png for res in exec_result.results if res.png), None)
    if png_b64:
        (OUT / "chart.png").write_bytes(base64.b64decode(png_b64))
        print(f"    图表已保存: {OUT / 'chart.png'}  <- 打开看看")
    else:
        print("    本沙盒模板没有返回 PNG（检查 results:）", exec_result.results)

    step("4. 错误回传：agent 自我修复的原料")
    exec_result = sbx.run_code("df = pd.read_csv('/not_exist.csv')")  # 故意报错
    if exec_result.error:
        e = exec_result.error
        print(f"    error.name = {e.name}\n    error.value = {e.value}")
        print(f"    traceback 末尾: {(e.traceback or '')[-200:]}")

    step("5. 流式输出：on_stdout 回调（长任务时 agent 可实时转播进度）")
    exec_result = sbx.run_code(
        "import time\n"
        "for i in range(3):\n"
        "    print(f'处理第 {i+1}/3 批...')\n"
        "    time.sleep(0.5)",
        on_stdout=lambda m: print("    [实时]", getattr(m, "line", m)),
    )
    show_logs(exec_result)

    step("6. 清理")
    sbx.kill()
    print("完成。同沙盒共享内核状态 + context 隔离 + 富结果 + 错误回传，这四个机制记住。")


if __name__ == "__main__":
    main()
