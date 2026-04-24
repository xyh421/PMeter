# PMeter

PMeter 是一个用 Python 编写场景的接口性能测试工具，使用方式更接近 Locust，但目标是逐步补齐 JMeter 常见的接口压测能力。

## 当前能力

| 能力 | 说明 |
|------|------|
| Python 场景 | 直接写 Python 场景，不需要 YAML |
| HttpUser | 用户模型，每用户独立 Session |
| @task(weight) | 任务权重随机调度 |
| between() / constant() | 思考时间 |
| 并发用户 | 按速率启动、固定时长压测 |
| 内置 HTTP 客户端 | 自动计时与统计 |
| 响应断言 | 状态码、正文包含、JSON 路径值 |
| **CSV 参数化** | `CsvDataSet` 线程安全循环读取 |
| **前置/后置处理器** | `@pre_processor` / `@post_processor` |
| **关联提取** | `extract_regex` / `extract_json_path` / `extract_header` / `extract_cookie` |
| **自定义检查点** | `self.check(name, condition)` |
| **HTML 报告** | `--html-report report.html` |
| **分布式压测** | `--workers N` 多进程 |
| **Web UI** | `--web-ui` 实时监控面板 |

## 安装

```bash
pip install -e .
```

## 使用方式

### 基础压测

```python
from pmeter import HttpUser, between, task

class ApiUser(HttpUser):
    host = "https://httpbin.org"
    wait_time = between(0.5, 1.5)

    @task(2)
    def health(self):
        self.client.get("/status/200", name="health").assert_status(200)

    @task(1)
    def query_json(self):
        self.client.get("/json", name="json").assert_json_path("slideshow.author", "Yours Truly")
```

```bash
pmeter run examples/demo_scenario.py --users 10 --spawn-rate 2 --run-time 30s
```

### CSV 参数化

```python
from pmeter import HttpUser, CsvDataSet, task

users_csv = CsvDataSet("data/users.csv")  # 列：username,password

class LoginUser(HttpUser):
    host = "https://api.example.com"

    def on_start(self):
        row = users_csv.next_row()
        self.username = row["username"]
        self.password = row["password"]

    @task
    def login(self):
        self.client.post("/login", json={"user": self.username, "pass": self.password})
```

### 前置/后置处理器

```python
from pmeter import HttpUser, task, pre_processor, post_processor

class SignedUser(HttpUser):
    host = "https://api.example.com"

    @pre_processor
    def add_auth_header(self, method, url, kwargs):
        kwargs.setdefault("headers", {})["Authorization"] = f"Bearer {self.token}"
        return kwargs

    @post_processor
    def log_slow(self, response):
        if response.elapsed_ms > 500:
            print(f"SLOW: {response.request_name} {response.elapsed_ms:.0f}ms")

    @task
    def fetch(self):
        self.client.get("/data")
```

### 关联提取

```python
@task
def login_then_fetch(self):
    resp = self.client.post("/auth/token", json={"user": "alice", "pass": "s3cr3t"})
    # 从 JSON 提取 token 存到 self.vars
    self.vars["token"] = resp.extract_json_path("access_token")

    # 下一个请求复用 token
    self.client.get("/profile", headers={"Authorization": f"Bearer {self.vars['token']}"})

    # 也可以用正则从 HTML/文本提取
    page = self.client.get("/dashboard")
    csrf = page.extract_regex(r'csrf_token" value="([^"]+)"')
    self.vars["csrf"] = csrf
```

### 自定义检查点

```python
@task
def check_stock(self):
    resp = self.client.get("/inventory/42")
    data = resp.json()
    self.check("stock > 0", data["stock"] > 0, f"stock={data['stock']}")
    self.check("price valid", 0 < data["price"] < 10000)
```

控制台报告会显示每个检查点的通过率，HTML 报告中也有专属表格。

### HTML 报告

```bash
pmeter run examples/demo_scenario.py --users 50 --run-time 1m --html-report report.html
```

生成自包含 HTML 文件，含响应时间图表、请求统计表、检查点表格。

### 分布式压测

```bash
# 本机 4 个进程，每进程 25 用户，合计 100 并发
pmeter run examples/demo_scenario.py --users 100 --workers 4 --run-time 2m
```

Worker 进程自动平分用户数，结果合并后统一报告。

### Web UI 实时面板

```bash
pmeter run examples/demo_scenario.py --users 20 --run-time 60s --web-ui --web-port 8089
```

测试过程中打开 http://localhost:8089 可查看实时 RPS、延迟曲线、错误率。

## CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--users` | 1 | 并发用户数 |
| `--spawn-rate` | 1.0 | 每秒启动用户数 |
| `--run-time` | 30s | 运行时长（支持 s/m/h） |
| `--host` | — | 覆盖场景中的 host |
| `--html-report PATH` | — | 生成 HTML 报告 |
| `--workers N` | 1 | 分布式进程数 |
| `--web-ui` | — | 启动实时 Web 面板 |
| `--web-port` | 8089 | Web 面板端口 |

## API 概览

### `HttpUser`

- 每个并发用户对应独立 `requests.Session`
- 可定义 `host`、`weight`、`wait_time`
- 可覆写 `on_start()` / `on_stop()`
- `self.vars: dict` — 跨请求共享变量（关联提取用）
- `self.check(name, condition, message="")` — 记录检查点

### `self.client`

- `get / post / put / patch / delete`
- `request(method, url, name=..., timeout=..., **kwargs)`

### `HttpResponse`

**断言（链式调用）**
- `assert_status(code)`
- `assert_body_contains(text)`
- `assert_json_path(path, expected)`

**提取**
- `extract_regex(pattern, group=1) -> str | None`
- `extract_json_path(path) -> Any`
- `extract_header(name) -> str | None`
- `extract_cookie(name) -> str | None`

**属性**
- `status_code`, `text`, `headers`, `elapsed_ms`
- `json()`

### `CsvDataSet(path, *, delimiter=",", encoding="utf-8")`

- `next_row() -> dict[str, str]` — 线程安全，循环取行

### 装饰器

- `@pre_processor` — 每次请求前调用，可修改 kwargs
- `@post_processor` — 每次请求后调用，接收 HttpResponse
