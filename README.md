# E2B 本地实验工作台

> 目标：亲眼观察"E2B 沙盒"的创建、使用、暂停、销毁全过程，并掌握 agent 沙盒的核心概念与工程要点。
>
> 环境：你的 Windows 机器只负责"指挥"（跑 SDK 脚本）；沙盒本体是 E2B 云端的一台台 Linux 微虚拟机。

---

## 1. E2B 是什么（30 秒版）

E2B（e2b.dev）是给 **AI agent 用的云沙盒**：你的 agent（或脚本）通过 SDK，可以在
1~3 秒内拿到一台**一次性的、隔离的、完整的 Linux 环境**，在里面跑命令、执行 LLM
生成的代码、读写文件、画图，用完即焚，按秒计费。

一句话区分三样东西：

| 名字 | 是什么 |
|---|---|
| E2B 平台 | 云端的沙盒编排服务（控制面 + Firecracker 微虚拟机集群），**这部分跑在 E2B 的机房** |
| E2B SDK | 你本地安装的 `e2b-code-interpreter`（Python）/ `@e2b/code-interpreter`（JS），指挥沙盒的遥控器，开源 |
| E2B runtime | 沙盒内部那套 Firecracker 微虚拟机 + 快照恢复技术，也开源（github.com/e2b-dev/runtime） |

所以"本地配置 E2B"的准确含义是：**本地装好 SDK 和实验脚本，远程驱动云端沙盒**。
沙盒本体无法完整跑在你 Windows 本机上（Firecracker 需要 Linux 内核，整套 infra 自托管是一个
正经的运维项目）。想看"沙盒化的本地平替"，本仓库 `local-docker-demo/` 用 Docker 复刻了同样的模式。

## 2. 架构：一次 run_code 到底发生了什么

```
你的电脑 (Windows)                                E2B 云端
┌──────────────────────────┐      HTTPS/WSS     ┌─────────────────────────────────┐
│ demos/*.py               │ ─────────────────► │ 控制面 api.e2b.dev               │
│ e2b SDK（遥控器）          │      E2B_API_KEY   │   ├─ 鉴权 / 计费 / 编排(Nomad)   │
└──────────────────────────┘                    │   └─ 分配一台 Firecracker microVM│
                                                │        （独立 Linux 内核！）      │
                                                │            └─ envd 守护进程      │
                                                │               ├─ 命令执行        │
                                                │               ├─ 文件读写        │
                                                │               └─ Jupyter 内核    │
                                                └─────────────────────────────────┘
```

三个关键词：

- **Firecracker microVM**：AWS 开源的微型虚拟机。与 Docker 容器"共享宿主内核"不同，
  每个沙盒有**自己独立的内核**，隔离等级是虚拟机级的——这是"放心跑不可信代码"的根基。
- **envd**：沙盒镜像里预装的守护进程（agent 的"服务端"）。SDK 所有操作（run/files/code）
  本质都是调用 envd 的接口。
- **Template（模板）**：沙盒的"镜像"。默认模板 `code-interpreter-v1` 预装了 Python、
  pandas、matplotlib、Jupyter 内核等数据科学全家桶。可以基于 Dockerfile 自定义模板。

## 3. 核心概念速查表

| 概念 | 一句话解释 | 注意点 |
|---|---|---|
| Sandbox | 一台一次性的云端 Linux 微虚拟机，有唯一 `sandbox_id` | 非持久：到期/kill 后**数据全部蒸发** |
| Template | 沙盒从哪个预装环境启动（类似镜像） | 默认 `code-interpreter-v1`；自定义模板走 Dockerfile |
| Timeout | 沙盒最长存活时间，**默认 300 秒**，到期云端自动销毁 | 成本护栏；`set_timeout()` 可续命 |
| Pause / Resume | 内存态做成快照后停机；恢复只需几百毫秒，变量/文件/进程都在 | 暂停期间不按运行计费；长任务的正确续命方式 |
| Fork | 从暂停的沙盒克隆 N 个独立副本 | 并行实验、多 agent 并发的基础 |
| Metadata | 给沙盒打标签 | 配合 `Sandbox.list()` 管理名下沙盒 |
| run_code | 向沙盒内常驻 Jupyter 内核提交代码单元 | 同沙盒内**变量状态跨调用保留**；`context` 可开会话隔离 |
| commands.run | 在沙盒里跑 shell 命令，每次全新进程 | 进程状态不保留；`background=True` 可跑后台进程 |
| files | 本机 ↔ 沙盒 传文件（bytes over HTTPS） | 没有"共享文件夹"这回事，一切都是显式传输 |
| Execution | run_code 的返回：logs + results(图表等) + error | `error` 里的 traceback 喂回模型 = agent 自我修复闭环 |

## 4. 实验路线（建议顺序）

**一键模式（推荐）**：在 `e2b-lab` 目录下运行

```bash
python start_lab.py
```

它全自动处理剩下的一切：没 Key 就打开注册页并每 3 秒盯着 `.env`，检测到你粘贴的
Key 后自动跑完 00 自检 + 01~05 全部实验，最后输出汇总报告。你唯一要做的就是在
弹开的网页里注册（免费送 $100 额度），创建 API Key，粘贴到 `.env` 的
`E2B_API_KEY=` 后面——这一步只能本人完成（账号是你的，云端算力必须实名挂靠）。

**手动模式（想逐个体会时）**：

```bash
.venv\Scripts\activate          # 激活虚拟环境
python demos\00_check_env.py    # 环境自检（无 key 也能跑，会告诉你缺什么）
python demos\01_quickstart.py   # 创建/命令/代码/文件系统/销毁 —— 最小闭环
python demos\02_commands_files.py  # 进程控制 + 文件上传下载（agent 工具层基本功）
python demos\03_code_interpreter.py  # 状态持久、context 隔离、图表、错误回传
python demos\04_lifecycle.py    # timeout/续命/pause/resume/fork —— 成本与生命周期
python demos\05_agent_pattern.py  # 完整 agent 工具调用闭环（含"犯错-自修复"剧本）
```

**全程开着 [Dashboard 的 Sandboxes 页](https://e2b.dev/dashboard)**：脚本每一步
创建、暂停、销毁，你都能在网页上实时看到对应记录变化——这是"亲眼观察"的最佳视角。

**免费对照实验（不需要 Key）**：`python local-docker-demo\agent_local_demo.py`，
用 Docker 手动复刻一遍沙盒该有的纪律（断网、只读、限资源、超时强杀、用完即焚），
每一步都标注了对应的 E2B API。先跑它再看 01~05，体感会非常清晰。

## 5. 注意要点（最容易踩的坑）

1. **沙盒不是你的硬盘**。默认非持久，timeout 到期或 kill 后一切蒸发。要留下的产物，
   必须在销毁前用 `files.read` 拉回来，或持久化到外部存储（数据库/S3/卷）。
   需要长时间保鲜用 pause（快照），而不是祈祷 timeout 别到。
2. **默认 timeout 只有 300 秒**。agent 跑长任务务必 `set_timeout` 续命或调大创建参数；
   反过来，这正是防成本失控的护栏，别随手设成 24 小时。
3. **按秒计费**：vCPU $0.000014/秒 + 内存 $0.0000045/GiB·秒。默认规格(2vCPU/4GiB)
   约 **$0.17/小时**。免费 $100 一次性额度 ≈ 默认规格跑 ~600 小时，学习足够，
   但"忘了 kill"是真实烧钱方式。纪律：**脚本里 create 和 kill 成对出现**（本仓库脚本都是）。
4. **免费层限制**：最多 20 个并发沙盒、单个沙盒最长 1 小时、一次性 $100 额度。
5. **沙盒里永远是 Linux**。路径用 `/home/user/...`，没有 Windows 路径；你在本机 Windows
   上调试的代码，进沙盒后以 Linux 行为为准。
6. **LLM 生成的代码一律视为不可信**。沙盒的意义就是让它作恶也出不了圈；
   但别把你的 API Key、客户数据主动塞进沙盒（环境变量参数 `envs` 想清楚再用）。
7. **沙盒默认能访问外网**（`allow_internet_access=True`）。这既是"能 pip install"的
   原因，也是"agent 代码可外传数据"的通道。生产收紧时关掉它或用 `network` 参数精细控制。
8. **两种执行模型别混用**：`run_code` 是 Jupyter 内核（状态保留、适合数据分析）；
   `commands.run` 是一次性 shell 进程（无状态、适合装包/起服务/跑脚本）。
9. **重连靠 sandbox_id**：`Sandbox.connect(id)` 可在任何机器恢复操作；
   但 `Sandbox` 对象本身不可跨进程序化复用，长生命周期服务要自己管理 id 登记表。

## 6. 进阶方向（学完 01~05 后）

- **自定义模板**：写 Dockerfile → `e2b template build`（CLI 用 Docker 本地构建）→
  得到自己的 `Sandbox.create(template="my-template")`，预装你 agent 需要的一切。
- **MCP 接入**：E2B 官方提供托管 MCP 服务器（`https://mcp.e2b.dev/sse`，header 带
  `API_KEY`），可直接接入 Claude Desktop / Cursor 等客户端，让任意 AI 客户端获得
  "在沙盒里跑代码"的工具。开源实现在 [e2b-dev/mcp-server](https://github.com/e2b-dev/mcp-server)。
- **Pause/Fork 编排**：空闲即 pause，多 agent 时 fork 出副本并行探索再择优——
  这是 E2B 区别于"自建 Docker"的核心能力。
- **自托管**：SDK 与 runtime 开源，但完整 infra（Nomad/Consul/GCP Terraform）自托管
  是大工程，学习阶段不建议。想本地复刻同款体验，见 `local-docker-demo/` 或研究
  Microsandbox、Daytona 等自托管友好的替代品。

## 7. 国内替代与主流做法

**付费问题澄清**：E2B 不强制付费——注册送一次性 $100 额度（无需信用卡），按秒计费
（默认规格约 $0.17/小时），额度烧完才需要充值。真正烧钱的方式只有一种：忘了 kill。

**国内平台（2026 现状）**：E2B 的 SDK 接口正在成为事实标准，国内大厂直接做了协议兼容：

| 平台 | 形态 | 要点 |
|---|---|---|
| 阿里云 ACS Agent Sandbox | MicroVM 云沙箱（公测） | 内存级休眠唤醒、Checkpoint 克隆、最高 15K 沙盒/分钟弹性，Kimi 在用 |
| 阿里云 FC 云沙箱 | 函数计算上的代码执行环境 | 提供 **E2B 兼容 API**，支持 VPC / OSS 挂载 |
| 火山引擎 函数服务沙箱 | 云沙箱实例 | 接口层**兼容 E2B 协议**，官方 SDK/CLI 可直接接入 |

对学习者的意义：你在本仓库学的 E2B SDK 概念与代码，迁移到国内平台几乎零改造；
选型时真正的差异点是**计费、合规（数据不出境）、VPC/内网集成**，而不是 API 形态。

**本地/自托管选项**：
- **Docker 方案**：本仓库 `local-docker-demo/`（隔离弱于 microVM，学习足够）
- **DifySandbox**：开源、seccomp 隔离，Dify 生态标配，自托管友好
- **Microsandbox**：开源 microVM 沙箱（需 Linux/Mac），E2B 同级隔离的可自托管路线
- **裸 Firecracker / gVisor**：直接用底层技术自建（Linux only，运维量大）

**主流 agent 沙箱的四条技术路线**：
1. **托管微虚拟机云**（E2B、Modal Sandbox、Daytona、阿里 ACS、火山引擎）：
   Firecracker 级隔离 + 内存快照毫秒恢复 + 按秒计费——当前生产主流。
2. **大模型厂内建工具**（OpenAI Code Interpreter / Agents API 托管沙箱、
   Anthropic 代码执行工具、Gemini code execution）：沙箱与模型同栈、零接入成本，
   但被锁定在单一厂商生态。
3. **自托管容器系**（DifySandbox、Jupyter+Docker、OpenAI self-hosted exec-server）：
   便宜可控、数据不出门，隔离等级弱一档，适合内网企业场景。
4. **浏览器 / WASM**（Pyodide 等）：零成本零延迟、天然免安装，但单语言、
   兼容性受限——轻量分析场景的补充而非替代。

## 8. 面试速记（为什么 & 怎么答）

- **为什么 agent 跑代码要沙盒？** LLM 生成的代码不可信且不可静态预测；沙盒把
  "试错"的代价限定在一台一次性机器里：隔离（microVM 独立内核）、可复现（固定模板）、
  可回收（timeout/kill）、可观测（logs/metrics）。
- **为什么用 microVM 不用 Docker 容器？** 容器共享宿主内核，逃逸面和"吵闹邻居"
  问题在多租户下不可接受；microVM 每实例独立内核，同时靠快照做到毫秒级启动。
- **毫秒级启动怎么做到的？** 不是冷启动内核，而是**从内存快照恢复**（pause 的逆过程）。
  模板预置 + Firecracker snapshot/restore ≈ 几百 ms 拿到一台"热"机器。
- **agent 代码执行闭环怎么搭？** 模型输出代码 → 工具层送沙盒执行 → 把 stdout/结果/
  **报错 traceback** 回填对话 → 模型修正重试（见 `05_agent_pattern.py` 第 2、3 幕）。
- **和 Pyodide/WASM 方案的取舍？** WASM 启动最快、更便宜，但单语言、无完整 OS、
  兼容性受限（很多带 C 扩展的包跑不了）；microVM 全功能但贵。按任务形态选，可混用。
- **成本怎么控？** timeout 护栏 + 用完即 kill + 闲置 pause + 监控并发数 + 免费层
  20 并发/1h 上限的意识。

## 9. 参考链接

- 官网/文档：<https://e2b.dev> · <https://docs.e2b.dev>
- 定价：<https://e2b.dev/pricing>
- SDK 开源仓库：<https://github.com/e2b-dev/E2B>
- Runtime（Firecracker 快照恢复）：<https://github.com/e2b-dev/runtime>
- MCP 服务器：<https://github.com/e2b-dev/mcp-server>
- Computer Use 沙盒（虚拟桌面）：<https://github.com/e2b-dev/desktop>
