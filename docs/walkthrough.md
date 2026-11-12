# ai-qa-runner · 一步一步使用与讲解

这份文档按「第一次拿到仓库」的顺序写：先跑起来，再看懂输出，最后能讲清楚设计。

## 0. 五分钟路线

1. 跑 `demo`，看 10 条用例全过。
2. 注入一个 `cart` 缺陷，看 1 条失败被归因为「应用缺陷」。
3. 用 `--repeat 3` 看 flaky 检测。
4. 用本地模型从需求生成用例。
5. 打开 `docs/walkthrough` 最后的「面试怎么讲」，用自己的话复述一遍。

---

## 1. 它解决什么问题

输入两样东西：

- 一份自然语言需求（例如 `requirements/login.md`）
- 一个被测页面（例如本地演示商城）

输出：

- 一组结构化测试用例（JSON，可逐条校验）
- 一份单文件 HTML 报告：每条用例的步骤、耗时、失败原因、截图和证据

和「让模型直接写 Playwright 代码」的区别：

| 模型直接写代码 | ai-qa-runner |
|---|---|
| 选择器可能凭空编造 | 先生成页面可达性摘要，再生成用例；生成后还会核对定位器 |
| 报错后不知道是应用坏了还是用例写错 | 规则归因：应用缺陷 / 定位失效 / 用例错误 / 环境 / flaky |
| 结果无法比较 | 固定 schema + 评测集 + 可回归的数字 |

---

## 2. 环境准备（Windows）

### 2.1 创建虚拟环境

```powershell
cd C:\ClaudeProjects\ai-qa-runner
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

看到 `Successfully installed ...` 即可。

### 2.2 安装浏览器

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

> 如果下载失败（网络问题），不用卡住：程序会自动回退到本机已安装的 Edge / Chrome。CI 里使用 Chromium。

### 2.3 跑第一次 demo

```powershell
.\.venv\Scripts\python.exe -m src.cli demo
```

预期输出：

```text
▶ 执行 10 条用例 × 1 轮 → http://127.0.0.1:xxxxx
...
通过 10/10（100%） · 失败 0 · 不稳定 0 · 总耗时 8-9s
报告：...\reports\report.html
```

双击打开 `reports/report.html`。这一步不需要任何 API Key，也不连外网。

---

## 3. 看三个 demo，理解三种状态

### 3.1 全部通过

就是上一步。报告里：

- 顶部：通过率、通过/失败/不稳定、总耗时
- 「失败归因」：全部通过时显示空状态
- 每条用例可展开：步骤、耗时、断言结果

### 3.2 注入应用缺陷

```powershell
.\.venv\Scripts\python.exe -m src.cli demo --bugs cart
```

预期：

```text
cart-add-twice  失败  应用缺陷
通过 9/10（90%） · 失败 1 · 不稳定 0
```

为什么是「应用缺陷」：点击「加入购物车」两次，动作都成功，但合计金额仍是 ¥299，而需求要求 ¥598。执行器给的事实是「断言失败、没有页面脚本错误、没有失败请求」，规则因此归到应用缺陷。

### 3.3 检测 flaky

```powershell
.\.venv\Scripts\python.exe -m src.cli demo --bugs flaky --repeat 3
```

`?bugs=flaky` 会让「加入购物车」有 45% 概率静默失败。同一用例跑 3 轮：

- 3 轮全过 → 通过
- 3 轮全挂 → 失败
- 有输有赢 → **不稳定（flaky）**，并保留第一次失败的截图作为证据

每次结果可能不同，这是预期行为。

---

## 4. 用你自己的需求生成用例

需要先启动演示站（终端 1）：

```powershell
.\.venv\Scripts\python.exe -m src.cli serve --port 8000
```

另开终端（终端 2）：

```powershell
.\.venv\Scripts\python.exe -m src.cli generate `
  --req requirements/login.md `
  --url http://127.0.0.1:8000 `
  --provider ollama `
  --model qwen2.5-coder:7b `
  --count 5 `
  --out cases/generated-login.json
```

执行过程：

1. Playwright 打开页面，抓取 role / label / testid，打印「抓到 N 个可交互元素」。
2. 模型输出 JSON。
3. `schema.py` 逐条校验；有错就带着错误信息回炉重写。
4. 输出「合法 X / Y 条（有效率 Z%）」。
5. 用页面摘要核对定位器，打印「未出现在页面摘要中」的 warning（只警告，不拒绝）。

实测（2026-11-11，qwen2.5-coder:7b）：

```text
合法 5 / 5 条（有效率 100%，1 轮，约 44 秒）
执行结果：4/5 通过
```

失败的那条漏了登录前置步骤，被报告归因为「定位失效 / 用例错误」，而不是误报应用缺陷。

然后执行：

```powershell
.\.venv\Scripts\python.exe -m src.cli run --cases cases/generated-login.json --serve demo-app
```

---

## 5. 报告怎么看

打开报告后按这个顺序读：

1. **顶部 KPI**：通过率、失败数、不稳定数、总耗时。
2. **失败归因**：先看是应用问题还是用例/环境问题，避免拿错方向。
3. **失败用例卡片**（默认展开）：归因、置信度、原因、步骤表。
4. **证据**：失败截图、页面异常、控制台错误、失败请求；加了 `--trace` 还会保存 `trace.zip`。
5. **通过用例**：默认折叠，需要时展开看步骤。

报告是单文件：CSS、JS、截图都内联，直接发给别人也能打开。

---

## 6. 代码地图

| 文件 | 职责 | 关键入口 |
|---|---|---|
| `src/schema.py` | 用例结构与语义校验 | `TestCase`、`Step`、`Target`、`load_cases` |
| `src/outline.py` | 抓页面可达性摘要；生成后核对定位器 | `capture_outline`、`build_locator_index`、`check_case_locators` |
| `src/generate.py` | 调模型、解析 JSON、校验、带错误修复 | `generate_cases`、`extract_cases_payload` |
| `src/executor.py` | Playwright 执行、截图、事件收集、多轮合并 | `Executor.run`、`merge_attempts` |
| `src/diagnosis.py` | 规则归因 | `classify_exception`、`diagnose_signal` |
| `src/report.py` | 单文件 HTML | `render_report` |
| `src/llm.py` | DeepSeek / OpenAI 兼容 / Ollama / offline | `make_client` |
| `src/cli.py` | demo / generate / run / serve | `main` |

一次 `run` 的内部顺序：

```text
读取 cases → 逐条 schema 校验
→ 每个用例新建浏览器 context
→ 按 steps 执行（goto/fill/click/expect_*）
→ 失败时截图、记录 console/pageerror/requestfailed
→ 计算 error_kind → 规则归因
→ 多轮结果合并（flaky）
→ 渲染 HTML + 保存 JSON
```

---

## 7. 关键设计，面试可以直接讲

### 7.1 为什么用例是 JSON，不是模型写的代码

代码里的选择器无法在不执行的情况下验证；JSON 可以逐条校验动作、定位器、断言，还能统计「有效率」。执行器只认识 9 种动作，模型写不出危险操作。

### 7.2 为什么先抓页面再生成

提示词里放入真实的 role / label / testid，模型编造选择器的概率会明显下降。生成后还会再核对一次：摘要里找不到的定位器标 warning，而不是静默接受。

### 7.3 为什么归因先规则后模型

同一个失败信号必须得到同一个分类，才能进评测集。规则只看确定性事实：异常类型、HTTP 状态、页面脚本错误、失败请求。模型只在规则结论后补一句根因提示，不改变分类。

### 7.4 为什么 flaky 要单独定义

「跑一次失败」和「时好时坏」是两类问题。`--repeat N` 把同一用例跑 N 轮：全过、全挂、有输有赢，第三种单独标为 flaky，并保留失败证据。

---

## 8. 评测与测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe eval\run_eval.py
```

实测：

| 项目 | 结果 |
|---|---|
| 测试 | 43 passed |
| schema 有效性判定 | 25/25，precision/recall 100% |
| 失败归因规则 | 15/15 |
| 干净套件 | 10/10 |
| 注入缺陷归因 | 6/6 |

评测集是仓库自带的小样本，用于回归，不代表通用准确率。

---

## 9. 面试怎么讲（三个问题）

**Q1：模型生成的用例不可靠怎么办？**

先约束输出结构（schema），再校验、再修复；执行前就能知道哪些用例不合法。定位器还有页面摘要核对。模型不是不可控文本生成器，而是受约束的候选生成器。

**Q2：失败了怎么知道是谁的问题？**

按确定性事实归因：连接拒绝/5xx 是环境或应用；元素找不到且页面无脚本错误，多半是定位/用例问题；动作成功但断言不符，是应用缺陷；多轮结果不一致，是 flaky。报告带包号、截图和请求失败证据。

**Q3：怎么证明改动没退化？**

自建固定评测集和 43 个测试：schema、归因、真实浏览器执行都有覆盖。CI 每次 push 跑 ruff + pytest + eval + demo，指标下降会直接失败。

---

## 10. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `playwright install chromium` 下载失败 | 程序会自动回退到本机 Edge/Chrome；CI 里装 Chromium |
| 不想配 API Key | `--provider offline`，直接读取 `cases/demo-shop.json` |
| 生成时连不上页面 | 先 `serve --port 8000`；或加 `--no-outline` 只按需求生成 |
| 模型输出不是合法 JSON | 生成器会带错误信息修复；仍失败会列出未修复问题 |
| 失败用例耗时 5 秒以上 | 断言在等超时，属于预期；可用 `--timeout-ms` 调整 |
| Windows 控制台中文乱码 | CLI 已把 stdout 设为 UTF-8；旧终端可先 `chcp 65001` |

## 11. 局限

- 动作集只覆盖单页交互，没有文件上传、iframe、多标签页、登录态复用。
- 生成质量依赖模型；离线模式只能验证执行链路，验证不了生成质量。
- 归因规则覆盖常见失败形态，新类型会落到「未归类」，需要补规则。
- 演示站缺陷是人工注入的，不代表真实项目缺陷分布。
