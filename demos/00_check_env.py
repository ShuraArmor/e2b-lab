# -*- coding: utf-8 -*-
"""
00 环境自检：不连接 E2B 云端（无网络请求到沙盒服务），只检查本地配置是否就绪。
跑通本脚本 = 你可以开始 01~05 了。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

ok = True


def check(name: str, passed: bool, detail: str = ""):
    global ok
    mark = "PASS" if passed else "FAIL"
    if not passed:
        ok = False
    print(f"[{mark}] {name}" + (f"  -> {detail}" if detail else ""))


# 1) SDK 可导入
try:
    import e2b_code_interpreter
    from importlib.metadata import version

    check("E2B SDK 已安装", True, f"e2b-code-interpreter {version('e2b-code-interpreter')}")
except Exception as e:
    check("E2B SDK 已安装", False, str(e))

# 2) API Key
key = os.environ.get("E2B_API_KEY", "").strip()
if key:
    check("E2B_API_KEY 已配置", True, f"{key[:8]}...（已隐藏）")
else:
    check(
        "E2B_API_KEY 已配置",
        False,
        "请到 https://e2b.dev/dashboard 注册/登录，创建 API Key，"
        f"粘贴到 {ROOT / '.env'} 的 E2B_API_KEY= 后面（可参考 .env.example）",
    )

# 3) 能否连通 E2B 控制面（DNS/TLS 层面）
try:
    import httpx

    r = httpx.get("https://api.e2b.dev", timeout=10)
    check("网络可达 api.e2b.dev", True, f"HTTP {r.status_code}")
except Exception as e:
    check("网络可达 api.e2b.dev", False, f"{type(e).__name__}: {e}")

print()
if ok:
    print("一切就绪！接下来按顺序运行：01_quickstart.py -> 05_agent_pattern.py")
    print("强烈建议同时打开 https://e2b.dev/dashboard 的 Sandboxes 页面，")
    print("你会亲眼看到沙盒被创建、运行、暂停、销毁的全过程。")
else:
    print("请先解决上面的 FAIL 项，再运行 01~05。")
