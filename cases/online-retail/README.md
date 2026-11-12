# online-retail 案例 · 本地落地与运行提示词

把**下面分隔线以下**的这段提示词发给任意一个编码 Agent（Codex、Claude Code 等）。它会把 `cases/online-retail` 装到本地、配好隔离运行环境，并在运行前向你索要模型服务的 Base URL、模型 ID 和 API Key。

# 落地并运行 online-retail 经营简报案例

目标项目：<https://github.com/9-dream-come-true-9/AI-PM-Learning/tree/main/cases/online-retail>

这是一个 Python 案例工程，不是可安装包：自动下载 UCI 英国网店订单 Excel，用 pandas 清洗、`metrics.sql` 计算月度指标，再调用 OpenAI 兼容的 Chat Completions 接口撰写中文经营简报。请把它在本地落地到「可直接运行」状态，并在运行前向用户索取所需凭据。

## 1. 落地源码

- 链接是 `/tree/main/cases/online-retail` 子目录，不是仓库根，直接 `git clone` 这个 URL 会失败。
- 用稀疏浅克隆，保留 `cases/online-retail` 原有层级（README 中的运行命令依赖这个层级）：
  ```bash
  git clone --depth 1 --filter=blob:none --sparse https://github.com/9-dream-come-true-9/AI-PM-Learning.git AI-PM-Learning
  git -C AI-PM-Learning sparse-checkout set cases/online-retail
  ```
- 目标目录已存在时不要覆盖，先报告并等待确认。
- 不要修改拉下来的任何上游文件。

## 2. 准备隔离运行环境

- 需要 Python 3.11 或更新版本；不满足就明确报告，不要改依赖版本去迁就。
- 在 `AI-PM-Learning/cases/online-retail/` 下建立虚拟环境并安装锁定依赖：
  ```bash
  python -m venv .venv
  .venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
  # .venv/bin/python -m pip install -r requirements.txt          # macOS / Linux
  ```
- 依赖固定为 `pandas==3.0.3`、`openpyxl==3.1.5`，两个版本均真实存在，不要凭经验改动版本号。
- 验证：实际 `import pandas, openpyxl`、编译检查 `summarize.py`；确认 `.venv`、`.cache`、`output` 已被 `.gitignore` 忽略，`git status` 干净。
- 如实区分「已验证通过」与「未验证」，不要把「没报错」说成「跑通了」。

## 3. 必须向用户索取运行凭据（先拿到，再运行）

`summarize.py` 在 `main()` 一开始就读配置，三项缺任一项都会直接抛错退出；非交互终端下也不会弹出输入提示。因此**运行前必须先向用户索要以下三项，缺一不可**：

| 环境变量          | 含义           | 要求                                                                                                     |
| ------------- | ------------ | ------------------------------------------------------------------------------------------------------ |
| `AI_API_BASE` | API Base URL | 任意支持 OpenAI 兼容 Chat Completions 的服务地址，须为 http(s)，通常以 `/v1` 结尾；也接受完整的 `/chat/completions` 地址，脚本会自动补齐路径。 |
| `AI_MODEL`    | 模型 ID        | 服务商提供的模型名。用户若只提供了 base url 和 key，**必须补问这一项**，三项齐全前不要运行。                                                |
| `AI_API_KEY`  | API Key      | 用于请求认证。                                                                                                |

- 可选 `AI_API_KEY_HEADER`：仅接受 `Authorization`（默认 `Bearer <key>`）、`api-key`、`x-api-key`。
- 索取时写清变量名与示例格式，不要让用户猜。
- 安全要求：Key 仅通过**当前终端的环境变量**传入，不要写进任何文件、不要写死进代码、不要回显或打印到日志、不要提交到 git；提示用户不要把它贴进公开内容。
- 若用户没有凭据或暂不提供，就停在「已装好但未运行」，并说明卡在哪里，不要自行编造地址、模型名或 Key，也不要擅自改用其他服务。

## 4. 运行（需用户确认后进行）

- 首次运行会联网下载 UCI `online-retail.zip` 并校验 SHA-256，缓存到 `.cache/`；每次生成简报都会消耗所选模型服务的额度。这两件事都要先征得用户同意。
- Windows（PowerShell，从 `AI-PM-Learning/` 目录执行）：
  ```powershell
  $env:AI_API_BASE = "<用户提供的 Base URL>"
  $env:AI_MODEL = "<用户提供的模型 ID>"
  $env:AI_API_KEY = "<用户提供的 Key>"
  .\cases\online-retail\run.cmd --no-pause
  ```
  也可直接双击 `run.cmd` 交互填写（末尾会 `pause`；`--no-pause` 供非交互场景使用）。`run.cmd` 每次都会重新执行一次 `pip install -r requirements.txt`，需要联网。
- macOS / Linux：按 README 用 `.venv/bin/python summarize.py` 运行。
- 产物：`output/metrics.json`（核对指标数字）、`output/report.md`（经营简报）。

## 5. 边界

- 未拿到第 3 节三项凭据、或未获得「下载数据 / 消耗额度」的确认前，不要运行主程序。
- 不要擅自下载大体积数据、不要调用需要账号或消耗额度的外部 API。
- API 调用失败时如实报告 HTTP 状态码与原因，不要编造简报内容或指标数字；指标文件的生成与否也要如实说明。

## 6. 报告

用中文报告：落地路径与 commit、装了哪些依赖（含版本）、验证结果（区分已验证 / 未验证）、运行前还缺用户提供什么，并给出可直接复制的运行命令。
