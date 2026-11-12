# 用 AI 生成网店经营简报

用一家英国网店的公开交易数据练习经营分析。运行一次，自动下载 Excel、用 pandas 清洗订单行、用 SQLite 计算指标，再由 Codex 撰写中文简报。报告中的金额和单数由程序生成，AI 只接收汇总指标。

[查看一次真实运行生成的简报](example-report.md)

## 一键运行

准备 Python 3.11 或更新版本和支持 `codex exec --output-schema` 的 Codex CLI。先用 `codex login` 完成一次登录。首次运行需要联网安装依赖和下载数据，每次写简报会使用当前 Codex 账号的模型额度。

Windows 上双击 [run.cmd](run.cmd)，也可以用下面的 PowerShell 命令启动。

```powershell
.\cases\online-retail\run.cmd
```

运行完成后，在本目录的 `output/report.md` 查看经营简报，在 `output/metrics.json` 核对数字。若 AI 调用失败，已计算的指标仍可查看，终端会显示原因。

macOS 或 Linux 可以在本目录运行以下命令。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python summarize.py
```

## 指标口径

- 默认比较数据中最近两个完整自然月。日期按原始订单记录的日期读取，按数据首尾的观测日期排除不完整月份。
- 销售额为正数量、正单价、单号不以 C 开头的订单行金额之和。销售单数按符合条件的发票号去重。
- 取消额为单号以 C 开头、负数量、正单价的订单行金额绝对值之和，归入取消行所在月份。逐行金额四舍五入到便士。
- 销售额减取消额得到本模块的净额。取消单未与原始订单逐笔匹配，净额可用于本案例的同口径比较。
- 日期、单号、数量或单价缺失的行，以及不符合上述符号规则的行，排除并计数。原始订单行保留，发票行无需具备唯一性。

[查看汇总 SQL](metrics.sql)

## 数据来源

Daqing Chen（2015）发布的 UCI Online Retail，收录 2010–2011 年的英国网店交易，许可为 CC BY 4.0。脚本校验下载文件的 SHA-256。原始表、虚拟环境和本地输出由 Git 忽略。 [数据和许可证](https://archive.ics.uci.edu/dataset/352/online+retail)
