# -*- coding: utf-8 -*-
"""
04 生命周期与成本：E2B 生产使用最重要的一课 —— 沙盒不是白跑的，也不该白活着。

核心概念：
  * timeout   : 沙盒最长存活时间（默认 300s），到期云端自动销毁。这是成本护栏。
  * set_timeout: 给在跑的任务续命。
  * metadata  : 打标签，便于 Sandbox.list() 管理自己名下的沙盒。
  * pause     : 把整台"机器"的内存态做成快照后停机 —— 不再按运行计费，
                resume 时从快照恢复（几百毫秒），变量、文件、进程状态全都在。
  * fork      : 从暂停的沙盒分叉出 N 个独立副本（并行实验/并行 agent 的基础）。

观察点：
  * 全程开着 dashboard，看沙盒状态在 running -> paused -> running -> terminated 间切换。
  * pause -> resume 后，之前定义的 Jupyter 变量原样恢复 —— 这就是"快照恢复"，
    也是 E2B 宣称毫秒级启动的原因：启动 = 恢复快照，不是冷启动内核。
"""
import time

from dotenv import load_dotenv  # noqa: E402

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from e2b_code_interpreter import Sandbox  # noqa: E402


def step(title: str):
    print(f"\n{'=' * 60}\n>> {title}\n{'=' * 60}")


def main():
    step("1. 带标签创建（timeout=60 秒，故意设短让你体会护栏）")
    sbx = Sandbox.create(timeout=60, metadata={"demo": "04_lifecycle", "owner": "me"})
    print(f"   sandbox_id = {sbx.sandbox_id}")

    step("2. 用 metadata 在列表里找到自己的沙盒")
    page = Sandbox.list().next_items() or []  # list() 返回分页器，取第一批
    tagged = [s for s in page if "04_lifecycle" in str(getattr(s, "metadata", ""))]
    print(f"   账号下可见沙盒 {len(page)} 个，其中带 04_lifecycle 标签的 {len(tagged)} 个")

    step("3. 干点活：留一个变量 + 一个文件，待会儿验证快照恢复")
    sbx.run_code("favorite_number = 42")
    sbx.files.write("/home/user/state.txt", "我在暂停前写下的".encode("utf-8"))

    step("4. 续命 set_timeout + 查看资源指标")
    sbx.set_timeout(300)
    print("   已把剩余存活时间续到 300s")
    try:
        metrics = sbx.get_metrics()
        print(f"   metrics: {metrics}")
    except Exception as e:
        print(f"   (get_metrics 在此模板不可用: {type(e).__name__})")

    step("5. 暂停 pause() —— 停止计费，内存态变快照")
    t0 = time.perf_counter()
    sbx.pause()
    print(f"   pause 耗时 {time.perf_counter() - t0:.2f}s, is_running={sbx.is_running()}")
    print("   -> 看 dashboard：状态变成 paused。现在它不再按运行计费。")

    step("6. 恢复 connect(id) —— 几百毫秒内满血复活")
    t0 = time.perf_counter()
    sbx2 = Sandbox.connect(sbx.sandbox_id)
    print(f"   resume 耗时 {time.perf_counter() - t0:.2f}s （对比 01 里冷启动的时间！）")
    exec_result = sbx2.run_code("print('恢复后变量还在:', favorite_number)")
    for line in exec_result.logs.stdout:
        print("   [stdout]", getattr(line, "line", line))
    content = sbx2.files.read("/home/user/state.txt")
    print(f"   恢复后文件内容: {content}")

    step("7. fork：从运行中的沙盒克隆出独立副本（fork 会内部完成快照）")
    try:
        clones = sbx2.fork(count=1)
        for c in clones:
            if isinstance(c, Exception):
                print("   fork 失败:", c)
            else:
                txt = c.files.read("/home/user/state.txt")
                print(f"   副本 {c.sandbox_id} 里 state.txt = {txt}")
                c.kill()
    except Exception as e:
        print(f"   (fork 演示不可用: {type(e).__name__}: {e} —— 不影响理解概念)")

    step("8. 清理")
    sbx.kill()
    try:
        sbx2.kill()
    except Exception:
        pass
    print("全部销毁。记住这条铁律：agent 每次用完沙盒都要 kill，长任务靠 pause 续命。")


if __name__ == "__main__":
    main()
