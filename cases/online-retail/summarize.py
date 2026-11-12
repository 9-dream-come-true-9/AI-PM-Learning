"""Download the public UCI workbook, calculate monthly metrics, and write a brief."""

import hashlib
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

import pandas as pd


ROOT = Path(__file__).resolve().parent
SOURCE_PAGE = "https://archive.ics.uci.edu/dataset/352/online+retail"
SOURCE_ZIP = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"
SOURCE_SHA256 = "f5385cbb54bbebf7196389109c6b0621faab0c304e3702548165e71c84aede8b"
COLUMNS = ["InvoiceNo", "InvoiceDate", "Quantity", "UnitPrice"]
METRICS = {
    "gross_pence": "销售额",
    "cancelled_pence": "取消额",
    "net_pence": "净额",
    "sale_invoices": "销售单数",
}


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
    try:
        temp_path.write_bytes(content)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def get_workbook():
    cache = ROOT / ".cache" / "online-retail.zip"
    data = cache.read_bytes() if cache.exists() else b""
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        print("正在下载 UCI 公开订单数据…", flush=True)
        with urlopen(SOURCE_ZIP, timeout=45) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
            raise ValueError("UCI 下载文件与已核对的版本不一致，请检查数据源。")
        atomic_write(cache, data)
    with ZipFile(io.BytesIO(data)) as archive:
        return io.BytesIO(archive.read("Online Retail.xlsx"))


def amount_in_pence(quantity, price):
    value = Decimal(str(price)) * abs(int(quantity)) * 100
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def compute_metrics(raw):
    dates = pd.to_datetime(raw["InvoiceDate"], errors="coerce")
    start, end = dates.min(), dates.max()
    if pd.isna(start) or pd.isna(end):
        raise ValueError("订单没有有效日期。")
    first = start.to_period("M") + int(start.day != 1)
    latest = end.to_period("M") - int(end.day != end.days_in_month)
    previous = latest - 1
    if previous < first:
        raise ValueError("数据不足以比较两个完整月份。")
    months = [str(previous), str(latest)]

    invoice = raw["InvoiceNo"].astype("string").str.strip().str.upper()
    quantity = pd.to_numeric(raw["Quantity"], errors="coerce")
    price = pd.to_numeric(raw["UnitPrice"], errors="coerce")
    cancellation = invoice.str.startswith("C", na=False)
    sale = ~cancellation & quantity.gt(0)
    cancelled = cancellation & quantity.lt(0)
    eligible = (
        dates.notna()
        & invoice.fillna("").ne("")
        & quantity.mod(1).eq(0)
        & price.gt(0)
        & price.lt(float("inf"))
        & (sale | cancelled)
    )
    selected = eligible & dates.dt.strftime("%Y-%m").isin(months)
    lines = pd.DataFrame({
        "month": dates[selected].dt.strftime("%Y-%m"),
        "invoice": invoice[selected],
        "kind": cancellation[selected].map({True: "cancel", False: "sale"}),
        "amount_pence": [
            amount_in_pence(units, unit_price)
            for units, unit_price in zip(quantity[selected], price[selected], strict=True)
        ],
    })

    with closing(sqlite3.connect(":memory:")) as connection:
        lines.to_sql("lines", connection, index=False)
        connection.row_factory = sqlite3.Row
        totals = [
            dict(row)
            for row in connection.execute(
                (ROOT / "metrics.sql").read_text(encoding="utf-8"), months
            )
        ]
    if [row["month"] for row in totals] != months:
        raise ValueError("所选月份缺少符合口径的订单，无法比较。")

    return {
        "source": SOURCE_PAGE,
        "source_sha256": SOURCE_SHA256,
        "source_date_range": [str(start.date()), str(end.date())],
        "source_rows": len(raw),
        "excluded_rows": int((~eligible).sum()),
        "currency": "GBP",
        "months": totals,
    }


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def generate_analysis(metrics):
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("找不到 Codex CLI。请安装 Codex CLI，运行 codex login 后重试。")

    text = {"type": "string"}
    schema = object_schema({
        "observations": {
            "type": "array",
            "items": object_schema({
                "text": text,
                "metric_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(METRICS)},
                },
            }),
        },
        "next_step": text,
    })
    evidence = {"metric_labels": METRICS, "months": metrics["months"]}
    prompt = (
        "你为一家网店的负责人写中文经营简报。只根据下方给定的两月汇总写两条观察，"
        "每条引用一到三个 metric_ids，最后给一条可执行的下一步。"
        "金额字段单位是英镑便士，单数字段是销售发票数。"
        "用本月、上月描述先后，只写方向和含义，不写任何数字或百分比，数值由程序插入。"
        "不要推断因果，不把取消额理解成该月销售的取消率。"
        "下一步应可用订单表已有的商品、国家、日期或发票字段执行。取消单没有原单关联字段。"
        "每条限八十字，单行纯文本。不要调用工具、打开文件或执行命令。"
        "\n\n" + json.dumps(evidence, ensure_ascii=False)
    )

    with tempfile.TemporaryDirectory(prefix="retail-brief-") as directory:
        scratch = Path(directory)
        schema_path = scratch / "schema.json"
        result_path = scratch / "analysis.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        result = subprocess.run(
            [
                codex, "-a", "never", "exec", "--ignore-user-config", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
                "--output-schema", str(schema_path), "--output-last-message",
                str(result_path), "-",
            ],
            input=prompt,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=scratch,
            timeout=180,
        )
        if result.returncode or not result_path.is_file():
            reason = next(
                (line.strip() for line in reversed(result.stderr.splitlines())
                 if "error" in line.lower()),
                "",
            )
            raise RuntimeError(
                "Codex 调用失败，请检查登录、网络和额度。"
                + (f" CLI 信息：{reason[:240]}" if reason else "")
            )
        analysis = json.loads(result_path.read_text(encoding="utf-8"))

    observations = analysis.get("observations", [])
    if len(observations) != 2:
        raise ValueError("AI 简报应包含两条观察，请重试。")
    for observation in observations:
        references = observation["metric_ids"]
        if not 1 <= len(references) <= 3 or any(key not in METRICS for key in references):
            raise ValueError("AI 简报引用了无法核对的指标，请重试。")
        validate_text(observation["text"])
    validate_text(analysis["next_step"])
    return analysis


def validate_text(text):
    if not text.strip() or len(text) > 80 or re.search(r"[\d\r\n]", text):
        raise ValueError("AI 简报格式不合格，请重试。")


def display_value(key, value):
    if key.endswith("_pence"):
        sign = "-" if value < 0 else ""
        pounds, pence = divmod(abs(value), 100)
        return f"{sign}£{pounds:,}.{pence:02d}"
    return f"{value:,} 单"


def markdown_text(text):
    return re.sub(r"([\\<>\[\]*_~\x60|])", r"\\\1", text.strip())


def render_report(metrics, analysis):
    previous, current = metrics["months"]
    report = [
        "# 网店经营简报",
        "",
        f"本月为 **{current['month']}**，对照上月 **{previous['month']}**。金额以英镑计。",
        "",
        f"| 指标 | 上月 {previous['month']} | 本月 {current['month']} |",
        "| --- | ---: | ---: |",
    ]
    for key, label in METRICS.items():
        report.append(
            f"| {label} | {display_value(key, previous[key])} | "
            f"{display_value(key, current[key])} |"
        )

    report.extend(["", "## AI 经营观察", ""])
    for observation in analysis["observations"]:
        labels = "、".join(METRICS[key] for key in dict.fromkeys(observation["metric_ids"]))
        report.append(f"- {markdown_text(observation['text'])}（依据为{labels}）")

    start, end = metrics["source_date_range"]
    report.extend([
        "", "## 建议下一步", "", markdown_text(analysis["next_step"]),
        "", "## 数据与口径", "",
        f"原表覆盖 {start} 至 {end}，共 {metrics['source_rows']:,} 行。"
        f"其中 {metrics['excluded_rows']:,} 行不符合销售或取消的筛选口径。",
        "",
        "净额为当月销售额减去当月记账的取消额，取消单未与原始销售逐笔配对。",
        "",
        f"数据来自 Daqing Chen（2015），许可为 CC BY 4.0。 [UCI Online Retail]({SOURCE_PAGE})",
        "",
    ])
    return "\n".join(report)


def main():
    output = ROOT / "output"
    try:
        print("正在读取 Excel 并计算月度指标…", flush=True)
        workbook = get_workbook()
        raw = pd.read_excel(workbook, usecols=COLUMNS, engine="openpyxl")
        metrics = compute_metrics(raw)
        metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2) + "\n"
        atomic_write(output / "metrics.json", metrics_json.encode("utf-8"))
        print(f"指标已生成：{output / 'metrics.json'}", flush=True)
        print("正在用 Codex 撰写简报…", flush=True)
        report = render_report(metrics, generate_analysis(metrics))
        atomic_write(output / "report.md", report.encode("utf-8"))
        print(f"简报已生成：{output / 'report.md'}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
