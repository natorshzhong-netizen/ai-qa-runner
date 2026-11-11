"""命令行入口。

python -m src.cli demo
python -m src.cli generate --req requirements/login.md --url http://127.0.0.1:8000
python -m src.cli run --cases cases/demo-shop.json --serve demo-app
python -m src.cli serve
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .demo import apply_query
from .diagnosis import explain_with_llm
from .executor import Executor, ExecutorOptions, save_run
from .generate import generate_cases, offline_generate, save_result
from .llm import LLMError, make_client
from .models import CATEGORY_LABELS, CaseStatus, RunResult
from .outline import capture_outline, format_outline, outline_to_json
from .report import write_report
from .schema import TestCase, load_cases
from .server import running_server

ROOT = Path(__file__).resolve().parents[1]
DEMO_APP = ROOT / "demo-app"
DEFAULT_CASES = ROOT / "cases" / "demo-shop.json"


def _setup_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # 某些被重定向的实现没有 reconfigure
        pass


def _print_summary(run: RunResult) -> None:
    print()
    print(f"{'用例':<34}{'状态':<8}{'耗时':>8}  归因")
    print("-" * 72)
    for case in run.cases:
        if case.status == CaseStatus.PASSED:
            label, dx = "通过", ""
        elif case.status == CaseStatus.FLAKY:
            label = f"不稳定({case.pass_count}/{case.attempts})"
            dx = CATEGORY_LABELS[case.diagnosis.category] if case.diagnosis else ""
        else:
            label = "失败"
            dx = CATEGORY_LABELS[case.diagnosis.category] if case.diagnosis else ""
        print(f"{case.case_id:<34}{label:<8}{case.duration_ms / 1000:>7.1f}s  {dx}")
    print("-" * 72)
    print(
        f"通过 {run.passed}/{run.total}（{run.pass_rate * 100:.0f}%）"
        f" · 失败 {run.failed + run.errors} · 不稳定 {run.flaky}"
        f" · 总耗时 {run.duration_ms / 1000:.1f}s"
    )


def _load(path: Path) -> tuple[list[TestCase], dict]:
    try:
        cases, issues, meta = load_cases(path)
    except FileNotFoundError:
        print(f"✗ 找不到用例文件：{path}")
        return [], {}
    except json.JSONDecodeError as exc:
        print(f"✗ 用例文件不是合法 JSON：{exc}")
        return [], {}
    if issues:
        print(f"⚠ {path} 中有 {len(issues)} 条用例未通过校验：")
        for issue in issues[:10]:
            print(f"  - 第 {issue.index + 1} 条 {issue.case_id or ''}: {issue.message}")
    return cases, meta


def _explainer(args: argparse.Namespace):
    if not getattr(args, "llm_diagnose", False):
        return None
    try:
        _cfg, client = make_client(args.provider, args.model)
    except LLMError as exc:
        print(f"⚠ 模型诊断不可用：{exc}")
        return None
    if client is None:
        print("⚠ offline 模式没有模型，使用纯规则归因")
        return None

    def explain(signal, diagnosis):
        return explain_with_llm(signal, diagnosis, client.chat)

    return explain


def _run_suite(
    cases: list[TestCase],
    url: str,
    args: argparse.Namespace,
    *,
    mode: str,
    provider: str | None,
    report_name: str = "report.html",
) -> tuple[RunResult, Path, Path]:
    output_dir = Path(args.output_dir).resolve()
    options = ExecutorOptions(
        base_url=url,
        headless=not getattr(args, "headed", False),
        timeout_ms=args.timeout_ms,
        repeats=getattr(args, "repeat", 1),
        trace=getattr(args, "trace", False),
        output_dir=output_dir,
    )
    executor = Executor(options)
    print(f"▶ 执行 {len(cases)} 条用例 × {options.repeats} 轮 → {url}")
    run = executor.run(cases, mode=mode, provider=provider, explain=_explainer(args))

    report_path = write_report(run, output_dir / report_name, output_dir)
    json_path = output_dir / "last-run.json"
    save_run(run, json_path)
    _print_summary(run)
    print(f"\n报告：{report_path}\n数据：{json_path}")
    return run, report_path, json_path


def _maybe_open(path: Path, args: argparse.Namespace) -> None:
    if getattr(args, "open", False):
        webbrowser.open(path.as_uri())


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import start_server

    root = Path(args.root).resolve()
    if not (root / "index.html").exists():
        print(f"✗ {root} 下没有 index.html")
        return 1
    server, url = start_server(root, args.port)
    print(f"演示站：{url}  （Ctrl+C 停止）")
    try:
        while True:
            server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    cases, _meta = _load(Path(args.cases))
    if not cases:
        return 1
    query = f"bugs={args.bugs}" if args.bugs else ""
    cases = apply_query(cases, query)

    with running_server(DEMO_APP, args.port) as url:
        run, report_path, _json = _run_suite(
            cases,
            url,
            args,
            mode=f"demo{('?' + query) if query else ''}",
            provider="offline:curated",
        )
    _maybe_open(report_path, args)
    return 1 if (run.failed + run.errors) and args.fail_on_failure else 0


def cmd_run(args: argparse.Namespace) -> int:
    cases, meta = _load(Path(args.cases))
    if not cases:
        print("✗ 没有可执行的用例")
        return 1
    provider = meta.get("generated_by")

    def execute(url: str) -> int:
        run, report_path, _json = _run_suite(
            cases,
            url,
            args,
            mode=args.mode or Path(args.cases).stem,
            provider=provider,
        )
        _maybe_open(report_path, args)
        bad = run.failed + run.errors
        if args.fail_on_failure and bad:
            print(f"✗ 有 {bad} 条失败用例")
            return 1
        return 0

    if args.serve:
        with running_server(Path(args.serve), args.port) as url:
            return execute(url)
    if not args.url:
        print("✗ 需要 --url，或使用 --serve demo-app 自动起服务")
        return 1
    return execute(args.url)


def cmd_generate(args: argparse.Namespace) -> int:
    req_path = Path(args.req)
    if not req_path.exists():
        print(f"✗ 找不到需求文件：{req_path}")
        return 1
    requirement = req_path.read_text(encoding="utf-8")
    outline_text = ""
    outline: dict | None = None
    if args.url and not args.no_outline:
        try:
            print(f"▶ 探测页面可达性：{args.url}")
            captured = capture_outline(args.url, timeout_ms=args.timeout_ms)
            outline = captured
            outline_text = format_outline(captured)
            if args.outline_out:
                Path(args.outline_out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.outline_out).write_text(outline_to_json(captured), encoding="utf-8")
            print(f"  抓到 {len(captured.get('elements', []))} 个可交互元素")
        except Exception as exc:  # noqa: BLE001 - 探测失败不应阻断生成
            print(f"⚠ 页面探测失败，改为仅依据需求生成：{exc}")

    try:
        cfg, client = make_client(args.provider, args.model)
    except LLMError as exc:
        print(f"✗ {exc}")
        return 1

    if client is None:
        fallback = Path(args.fallback_cases)
        if not fallback.exists():
            print("✗ offline 模式需要 --fallback-cases 指向固定用例文件")
            return 1
        print(f"▶ offline：读取固定用例 {fallback}")
        result = offline_generate(fallback)
    else:
        print(f"▶ 生成用例：{cfg.label}（约 {args.count} 条）")
        try:
            result = generate_cases(
                requirement,
                client,
                outline_text=outline_text,
                outline=outline,
                count=args.count,
                requirement_source=str(req_path),
                repair_attempts=args.repair_attempts,
                log=print,
            )
        except LLMError as exc:
            print(f"✗ 生成失败：{exc}")
            return 1

    save_result(result, args.out, meta={"source_url": args.url or ""})
    print(
        f"\n✓ 合法 {len(result.cases)} / {result.raw_items} 条"
        f"（有效率 {result.valid_rate * 100:.0f}%，{result.attempts} 轮）→ {args.out}"
    )
    if result.issues:
        print("未修复的问题：")
        for issue in result.issues[:8]:
            print(f"  - 第 {issue.index + 1} 条 {issue.case_id or ''}: {issue.message}")
    if result.outline_warnings:
        print(f"\n⚠ {len(result.outline_warnings)} 处定位器未出现在页面摘要中（执行时会再验证）：")
        for warning in result.outline_warnings[:10]:
            print(f"  - {warning}")
    if not result.cases:
        return 1
    print(f"\n下一步：python -m src.cli run --cases {args.out} --serve demo-app")
    return 0


def _common_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--output-dir", default="reports", help="报告与截图输出目录（默认 reports/）")
    p.add_argument("--repeat", type=int, default=1, help="同一用例跑几轮，用于检测 flaky")
    p.add_argument("--timeout-ms", type=int, default=5000, help="单步等待超时")
    p.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    p.add_argument("--trace", action="store_true", help="保存 Playwright trace.zip")
    p.add_argument("--open", action="store_true", help="跑完自动打开报告")
    p.add_argument("--fail-on-failure", action="store_true", help="有失败用例时退出码为 1")
    p.add_argument("--provider", default=None, help="deepseek / openai / ollama / offline")
    p.add_argument("--model", default=None)
    p.add_argument("--llm-diagnose", action="store_true", help="用模型补充根因提示（不改变分类）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-qa-runner",
        description="把自然语言需求变成可执行、可归因的端到端测试。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="起演示站、跑自带用例、生成报告")
    demo.add_argument("--cases", default=str(DEFAULT_CASES))
    demo.add_argument("--bugs", default="", help="注入缺陷：1 / login / cart / validation / flaky")
    demo.add_argument("--port", type=int, default=0)
    _common_run_flags(demo)
    demo.set_defaults(func=cmd_demo)

    run = sub.add_parser("run", help="执行用例文件")
    run.add_argument("--cases", required=True)
    run.add_argument("--url", help="被测站点地址")
    run.add_argument("--serve", help="自动托管一个静态目录作为被测站点")
    run.add_argument("--port", type=int, default=0)
    run.add_argument("--mode", default="", help="报告里显示的模式标签")
    _common_run_flags(run)
    run.set_defaults(func=cmd_run)

    gen = sub.add_parser("generate", help="从需求生成用例")
    gen.add_argument("--req", required=True, help="需求文件（md/txt）")
    gen.add_argument("--url", help="先探测该页面，生成时只引用真实元素")
    gen.add_argument("--out", default="cases/generated.json")
    gen.add_argument("--count", type=int, default=6)
    gen.add_argument("--repair-attempts", type=int, default=2)
    gen.add_argument("--provider", default=None)
    gen.add_argument("--model", default=None)
    gen.add_argument("--timeout-ms", type=int, default=15000)
    gen.add_argument("--no-outline", action="store_true")
    gen.add_argument("--outline-out", default="", help="把页面可达性摘要另存为 JSON")
    gen.add_argument("--fallback-cases", default=str(DEFAULT_CASES))
    gen.set_defaults(func=cmd_generate)

    serve = sub.add_parser("serve", help="本地托管 demo-app")
    serve.add_argument("--root", default=str(DEMO_APP))
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
