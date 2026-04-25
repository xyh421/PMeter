# PMeter

> 用 Python 编写场景的接口性能测试工具 —— 像 Locust 一样轻量，朝 JMeter 方向进化。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## 简介

PMeter 是一个纯 Python 的接口压测工具。你只需写一个普通的 Python 文件来描述压测场景，无需 XML / YAML，即可完成从基础 HTTP 压测到分布式多进程的完整测试流程。

**核心设计目标**

- **场景即代码** — 用 Python 类和装饰器定义用户行为，对 IDE 友好
- **开箱即用** — 一条命令启动，内置统计、报告、Web 面板
- **逐步接近 JMeter** — CSV 参数化、前/后置处理器、关联提取、自定义检查点、分布式压测

---

## 功能一览

| 能力 | 说明 |
|------|------|
| Python 场景 | 直接写 Python，无需 YAML / XML |
| HttpUser | 每个虚拟用户独享一个 `requests.Session` |
| `@task(weight)` | 任务权重随机调度 |
| `between()` / `constant()` | 思考时间（Wait Time） |
| 并发用户 | 按速率逐步启动、固定时长压测 |
| 内置 HTTP 客户端 | 自动计时与统计，支持链式断言 |
| 响应断言 | 状态码、响应体包含、JSONPath 值 |
| **CSV 参数化** | `CsvDataSet` 线程安全循环读取 |
| **前置/后置处理器** | `@pre_processor` / `@post_processor` |
| **关联提取** | `extract_regex` / `extract_json_path` / `extract_header` / `extract_cookie` |
| **自定义检查点** | `self.check(name, condition)` |
| **HTML 报告** | `--html-report` 生成含图表的自包含 HTML |
| **分布式压测** | `--workers N` 多进程，结果自动合并 |
| **Web UI** | `--web-ui` 实时 RPS / 延迟监控面板 |

---

## 安装

```bash
git clone https://github.com/yourname/pmeter.git
cd pmeter
pip install -e .
```

> 依赖仅 `requests>=2.31`，Python 3.10+。

---

## 快速开始

### 1. 编写场景

```python
# examples/demo_scenario.py
from pmeter import HttpUser, between, task

class ApiUser(HttpUser):
    host = "https://httpbin.org"
    wait_time = between(0.5, 1.5)

    @task(2)
    def health(self):
        self.client.get("/status/200", name="health").assert_status(200)

    @task(1)
    def query_json(self):
        self.client.get("/json", name="json") \
            .assert_json_path("slideshow.author", "Yours Truly")
```

### 2. 运行压测

```bash
pmeter run examples/demo_scenario.py --users 10 --spawn-rate 2 --run-time 30s
```

### 3. 查看报告

控制台输出摘要，同时可生成 HTML 报告：

```bash
pmeter run examples/demo_scenario.py --users 10 --run-time 30s --html-report report.html
```

---

## 使用指南

### CSV 参数化

```python
from pmeter import HttpUser, CsvDataSet, task

users_csv = CsvDataSet("data/users.csv")  # 列头：username,password

class LoginUser(HttpUser):
    host = "https://api.example.com"

    def on_start(self):
        row = users_csv.next_row()
        self.username = row["username"]
        self.password = row["password"]

    @task
    def login(self):
        self.client.post("/login", json={
            "user": self.username,
            "pass": self.password,
        })
```

- 多线程安全，自动循环（用完从头再来）
- 支持自定义分隔符：`CsvDataSet("data.csv", delimiter="\t")`

---

### 前置 / 后置处理器

```python
from pmeter import HttpUser, task, pre_processor, post_processor

class SignedUser(HttpUser):
    host = "https://api.example.com"

    @pre_processor
    def add_auth(self, method, url, kwargs):
        kwargs.setdefault("headers", {})["Authorization"] = f"Bearer {self.token}"
        return kwargs          # 返回修改后的 kwargs

    @post_processor
    def log_slow(self, response):
        if response.elapsed_ms > 500:
            print(f"SLOW: {response.request_name} {response.elapsed_ms:.0f}ms")

    @task
    def fetch(self):
        self.client.get("/data")
```

---

### 关联提取

```python
@task
def login_then_profile(self):
    # 登录，从 JSON 响应中提取 token
    resp = self.client.post("/auth/token", json={"user": "alice", "pass": "s3cr3t"})
    self.vars["token"] = resp.extract_json_path("access_token")

    # 用 token 请求受保护接口
    self.client.get("/profile", headers={
        "Authorization": f"Bearer {self.vars['token']}"
    })

    # 从 HTML 正文用正则提取 CSRF token
    page = self.client.get("/dashboard")
    self.vars["csrf"] = page.extract_regex(r'csrf_token" value="([^"]+)"')
```

可用提取方法：

| 方法 | 说明 |
|------|------|
| `extract_json_path(path)` | 从 JSON 响应按路径取值（`.` 分隔） |
| `extract_regex(pattern, group=1)` | 正则提取，返回捕获组 |
| `extract_header(name)` | 提取响应头 |
| `extract_cookie(name)` | 提取 Cookie |

---

### 自定义检查点

```python
@task
def check_stock(self):
    resp = self.client.get("/inventory/42")
    data = resp.json()
    self.check("stock > 0",   data["stock"] > 0,  f"stock={data['stock']}")
    self.check("price valid", 0 < data["price"] < 10000)
```

控制台和 HTML 报告都会显示每个检查点的通过率与失败次数。

---

### HTML 报告

```bash
pmeter run examples/demo_scenario.py --users 50 --run-time 1m --html-report report.html
```

生成**自包含** HTML 文件（无需联网），包含：
- 顶部汇总卡片（总请求、失败率、RPS、P50 / P95 / P99）
- 响应时间柱状图 & 请求/失败对比图（Chart.js）
- 各接口详细统计表
- 自定义检查点表格（如有）

---

### 分布式压测

```bash
# 4 个进程，每进程 25 用户，合计 100 并发
pmeter run examples/demo_scenario.py --users 100 --workers 4 --run-time 2m
```

Worker 进程通过 `multiprocessing` 启动（跨平台 spawn 模式），用户数自动平分，结果汇总后统一输出。

---

### Web UI 实时面板

```bash
pmeter run examples/demo_scenario.py --users 20 --run-time 60s --web-ui --web-port 8089
```

测试过程中打开 [http://localhost:8089](http://localhost:8089) 可实时查看：
- RPS 历史曲线
- P95 延迟历史曲线
- 各接口实时请求量与失败率

---

## CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `scene` | — | 场景文件路径（必填） |
| `--users` | `1` | 并发用户数 |
| `--spawn-rate` | `1.0` | 每秒启动用户数 |
| `--run-time` | `30s` | 运行时长，支持 `s` / `m` / `h` |
| `--host` | — | 覆盖场景中的 `host` |
| `--html-report PATH` | — | 生成 HTML 报告 |
| `--workers N` | `1` | 分布式进程数 |
| `--web-ui` | — | 启动实时 Web 面板 |
| `--web-port` | `8089` | Web 面板端口 |

---

## API 参考

### `HttpUser`

| 属性 / 方法 | 说明 |
|-------------|------|
| `host: str` | 目标服务根地址 |
| `weight: int` | 多用户类时的启动权重（默认 1） |
| `wait_time` | 两次任务间隔，`between(a, b)` 或 `constant(t)` |
| `on_start()` | 用户启动时调用一次 |
| `on_stop()` | 用户退出时调用一次 |
| `self.vars: dict` | 跨请求共享变量，用于关联提取 |
| `self.check(name, condition, message="")` | 记录自定义检查点 |
| `self.client` | `HttpClient` 实例 |

### `self.client`

```python
self.client.get(url, name=None, timeout=30.0, **kwargs)
self.client.post(url, ...)
self.client.put(url, ...)
self.client.patch(url, ...)
self.client.delete(url, ...)
```

### `HttpResponse`

**断言（链式调用，断言失败自动计入失败统计）**

```python
.assert_status(200)
.assert_body_contains("ok")
.assert_json_path("data.id", 42)
```

**提取**

```python
.extract_json_path("access_token")           # -> Any
.extract_regex(r'token="([^"]+)"', group=1)  # -> str | None
.extract_header("X-Request-Id")              # -> str | None
.extract_cookie("session")                   # -> str | None
```

**属性**

```python
.status_code   # int
.text          # str
.headers       # dict
.elapsed_ms    # float
.json()        # dict / list
```

### `CsvDataSet`

```python
ds = CsvDataSet("data/users.csv", delimiter=",", encoding="utf-8")
row = ds.next_row()  # -> dict[str, str]，线程安全，自动循环
```

### 装饰器

```python
@pre_processor   # 每次请求前调用，参数 (self, method, url, kwargs)，可返回修改后的 kwargs
@post_processor  # 每次请求后调用，参数 (self, response)
```

---

## 项目结构

```
pmeter/
├── src/pmeter/
│   ├── cli.py          # CLI 入口
│   ├── runner.py       # 运行引擎 & HttpUser
│   ├── http.py         # HTTP 客户端 & 响应封装
│   ├── stats.py        # 统计收集
│   ├── dsl.py          # task / between / constant
│   ├── csv_data.py     # CSV 参数化
│   ├── processors.py   # 前/后置处理器装饰器
│   ├── report.py       # HTML 报告生成
│   ├── distributed.py  # 分布式多进程
│   └── web_ui.py       # 实时 Web 面板
├── examples/
│   └── demo_scenario.py
└── tests/
    └── test_all_features.py
```

---

## License

[MIT](LICENSE)
