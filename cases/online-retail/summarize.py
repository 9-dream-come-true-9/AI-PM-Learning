"""Download the public UCI workbook, calculate monthly metrics, and write a brief."""

import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from getpass import getpass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
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


def read_api_settings():
    values = []
    interactive = sys.stdin.isatty()
    for variable, prompt, secret in (
        ("AI_API_BASE", "API Base URL：", False),
        ("AI_MODEL", "模型名：", False),
        ("AI_API_KEY", "API Key（输入时不显示）：", True),
    ):
        value = os.environ.get(variable, "").strip()
        if not value and interactive:
            read = getpass if secret else input
            value = read(prompt).strip()
        if not value:
            raise ValueError(f"请提供 {variable}，或在终端中输入。")
        values.append(value)

    base, model, key = values
    url = urlsplit(base)
    if (
        url.scheme not in ("https", "http")
        or not url.hostname
        or url.username is not None
        or url.fragment
    ):
        raise ValueError("API Base URL 应为有效的 HTTP(S) 服务地址。")
    path = url.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return url._replace(path=path).geturl(), model, key


def generate_analysis(metrics, endpoint, model, api_key):
    evidence = {"metric_labels": METRICS, "months": metrics["months"]}
    prompt = (
        "你为一家网店的负责人写中文经营简报。只根据下方给定的两月汇总写两条观察，"
        "一条观察销售额与销售单数，一条观察取消额与净额。每条引用相关 metric_ids，最后给一条可执行的下一步。"
        "金额字段单位是英镑便士，单数字段是销售发票数。"
        "用本月、上月描述先后，只写方向和含义，不写任何数字或百分比，数值由程序插入。"
        "观察只描述表内四个指标的变化，不引入比率、均值或其他衍生指标。净额是销售额减去取消额的差额，不能称为收入、回款或利润。不要推断因果。"
        "下一步应可用订单表已有的商品、国家、日期或发票字段执行。取消单没有原单关联字段。"
        "每条限八十字，单行纯文本。"
        "只返回一个 JSON 对象。格式为 "
        '{"observations":[{"text":"观察","metric_ids":["gross_pence"]},'
        '{"text":"观察","metric_ids":["cancelled_pence"]}],"next_step":"建议"}。'
        "\n\n" + json.dumps(evidence, ensure_ascii=False)
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    header = os.environ.get("AI_API_KEY_HEADER", "Authorization").strip().lower()
    if header not in ("authorization", "api-key", "x-api-key"):
        raise ValueError("AI_API_KEY_HEADER 支持 Authorization、api-key 或 x-api-key。")
    credential = f"Bearer {api_key}" if header == "authorization" else api_key
    request.add_unredirected_header(header, credential)

    try:
        with urlopen(request, timeout=90) as response:
            completion = json.load(response)
    except HTTPError as error:
        guidance = {
            401: "请核对 Key、认证头和服务地址。",
            403: "请确认 Key 有权调用该模型。",
            404: "请核对服务地址和模型名。",
            429: "请检查服务额度或请求频率。",
        }
        hint = guidance.get(error.code, "请检查模型配置或稍后重试。")
        raise RuntimeError(f"API 请求失败（HTTP {error.code}）。{hint}") from None
    except (TimeoutError, URLError):
        raise RuntimeError("API 连接失败或超时，请检查服务地址和网络。") from None
    except (UnicodeError, ValueError):
        raise ValueError("API 返回了无法解析的响应。") from None

    try:
        choice = completion["choices"][0]
        content = choice["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ValueError("API 响应缺少简报文本，请确认模型支持 Chat Completions。") from None
    if choice.get("finish_reason") not in (None, "stop"):
        raise ValueError("API 未完整生成简报，请检查模型配置后重试。")

    fenced = re.fullmatch(r"\x60{3}(?:json)?\s*([\s\S]+?)\s*\x60{3}", content, re.IGNORECASE)
    try:
        analysis = json.loads(fenced[1] if fenced else content)
    except ValueError:
        raise ValueError("模型返回的简报无法解析为 JSON，请重试。") from None

    observations = analysis.get("observations") if isinstance(analysis, dict) else None
    if not isinstance(observations, list) or len(observations) != 2:
        raise ValueError("AI 简报应包含两条观察，请重试。")
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError("AI 简报的观察格式不合格，请重试。")
        references = observation.get("metric_ids")
        if (
            not isinstance(references, list)
            or not 1 <= len(references) <= 3
            or any(not isinstance(key, str) or key not in METRICS for key in references)
        ):
            raise ValueError("AI 简报引用了无法核对的指标，请重试。")
        validate_text(observation.get("text"))
    validate_text(analysis.get("next_step"))
    return analysis


def validate_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 80 or re.search(r"[\d\r\n]", text):
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
        endpoint, model, api_key = read_api_settings()
        print("正在读取 Excel 并计算月度指标…", flush=True)
        workbook = get_workbook()
        raw = pd.read_excel(workbook, usecols=COLUMNS, engine="openpyxl")
        metrics = compute_metrics(raw)
        metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2) + "\n"
        atomic_write(output / "metrics.json", metrics_json.encode("utf-8"))
        print(f"指标已生成：{output / 'metrics.json'}", flush=True)
        print("正在调用模型 API 撰写简报…", flush=True)
        report = render_report(metrics, generate_analysis(metrics, endpoint, model, api_key))
        atomic_write(output / "report.md", report.encode("utf-8"))
        print(f"简报已生成：{output / 'report.md'}")
        return 0
    except (OSError, ValueError, RuntimeError, EOFError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
