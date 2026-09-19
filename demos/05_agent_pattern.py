# -*- coding: utf-8 -*-
"""
05 Agent 模式：把前面所有积木拼成真正的 "LLM + 代码解释器" 工具调用闭环。

真实 agent 里，run_code 的代码来自 LLM 输出（tool call 的参数）。本脚本没有接
真实大模型（不依赖任何 LLM API），而是用一段"剧本"模拟模型的决策序列，重点让你
观察 agent 工程的结构本身：

    模型输出代码 -> 工具层送进沙盒执行 -> Execution 结果回填给模型 -> 继续或总结

并专门演示 agent 工程中最关键的一环：第一次代码有 bug -> 把 traceback 喂回去 ->
第二次代码自我修复成功。所有真实 LLM agent（OpenAI 的 code interpreter、
Claude 的分析工具等）跑的都是这个循环，只是"决策"部分换成了真模型。

观察点：
  * 每一步打印工具名、入参摘要、耗时、结果摘要 —— 这就是 agent 的执行轨迹，
    也是你调试 agent 时的主要观测面。
  * 模型的"手"只碰到沙盒，从来没碰到你的 Windows —— 这就是沙盒的全部意义。
"""
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


def tool_execute_python(sbx, code: str, note: str = ""):
    """工具层：LLM 的 run_code 工具调用在这里落地。生产里要对 code 做审计/限流。"""
    t0 = time.perf_counter()
    print(f"   [工具调用] run_code  {note}")
    exec_result = sbx.run_code(code, timeout=60)
    dt = time.perf_counter() - t0
    if exec_result.error:
        return {"ok": False, "error": f"{exec_result.error.name}: {exec_result.error.value}",
                "traceback": exec_result.error.traceback or "", "elapsed": dt}
    out = "\n".join(str(getattr(m, "line", m)) for m in exec_result.logs.stdout)
    return {"ok": True, "stdout": out, "elapsed": dt}


def main():
    step("0. 起沙盒（agent 的'工作台'）")
    sbx = Sandbox.create(timeout=300, metadata={"demo": "05_agent_pattern"})
    print(f"   sandbox_id = {sbx.sandbox_id}")

    # ---------------------------------------------------------------
    # 剧本第 1 幕：模型决定先用代码造一份模拟数据
    # ---------------------------------------------------------------
    step("1. 模型产出代码：生成 100 行模拟销售数据 CSV")
    code_gen = '''
import csv, random
random.seed(7)
products = ["键盘", "鼠标", "显示器", "耳机", "摄像头"]
with open("/home/user/sales.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "product", "qty", "price"])
    for i in range(100):
        p = random.choice(products)
        w.writerow([1000 + i, p, random.randint(1, 5), round(random.uniform(50, 800), 2)])
print("已生成 100 行数据")
'''
    result = tool_execute_python(sbx, code_gen, note="生成模拟数据")
    print(f"   -> {result['stdout'].strip()}  ({result['elapsed']:.2f}s)")

    # ---------------------------------------------------------------
    # 剧本第 2 幕：模型第一次分析 —— 故意写错列名（模拟真实 agent 的常态：先犯错）
    # ---------------------------------------------------------------
    step("2. 模型第一次分析：有 bug（列名写错）—— 观察错误如何回传")
    code_buggy = '''
import pandas as pd
df = pd.read_csv("/home/user/sales.csv")
top = df.groupby("产品")["qty"].sum().sort_values(ascending=False).head(3)
print(top)
'''
    result = tool_execute_python(sbx, code_buggy, note="第一次分析(有bug)")
    print(f"   -> 失败: {result['error']}")
    print(f"   -> traceback 将被回填给模型，模型据此修正代码")

    # ---------------------------------------------------------------
    # 剧本第 3 幕：模型看到 traceback 后修正（这里由剧本扮演"修正"）
    # ---------------------------------------------------------------
    step("3. 模型修正后再试：成功")
    code_fixed = '''
import pandas as pd
df = pd.read_csv("/home/user/sales.csv")
df["revenue"] = df["qty"] * df["price"]
top = df.groupby("product")["revenue"].sum().sort_values(ascending=False)
print(top)
top.to_csv("/home/user/top_products.csv")
'''
    result = tool_execute_python(sbx, code_fixed, note="修正后的分析")
    print(f"   -> 成功 ({result['elapsed']:.2f}s):\n{result['stdout']}")

    # ---------------------------------------------------------------
    # 剧本第 4 幕：模型取回结果，生成给用户的最终答案
    # ---------------------------------------------------------------
    step("4. 取回交付物，agent 给出最终回答")
    top_csv = sbx.files.read("/home/user/top_products.csv", format="bytes")
    (OUT / "top_products.csv").write_bytes(top_csv)
    first_line = top_csv.decode("utf-8").strip().splitlines()[1]
    best_product, revenue = first_line.split(",")[0], float(first_line.split(",")[1])
    print(f"   [最终回答] 销售额最高的产品是「{best_product}」，总销售额 {revenue:,.2f} 元。")
    print(f"   明细已保存到 {(OUT / 'top_products.csv')}")

    step("5. 收工即销毁（成本纪律）")
    sbx.kill()
    print("完成。这个 剧本化工具循环 = 真实 LLM agent 代码执行的全部骨架。")


if __name__ == "__main__":
    main()
