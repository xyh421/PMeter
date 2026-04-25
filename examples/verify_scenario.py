"""
验证场景 —— 配合 mock_server.py 使用

运行前先启动服务器：
    python examples/mock_server.py

然后运行压测：
    pmeter run examples/verify_scenario.py --users 5 --run-time 15s --html-report verify.html
"""
from pmeter import HttpUser, between, task, pre_processor, post_processor


class VerifyUser(HttpUser):
    host = "http://127.0.0.1:8080"
    wait_time = between(0.2, 0.5)

    async def on_start(self):
        resp = await self.client.post("/auth/token", json={"user": "alice", "pass": "s3cr3t"})
        self.vars["token"] = resp.extract_json_path("access_token")

    @pre_processor
    def inject_token(self, method, url, kwargs):
        if self.vars.get("token") and "/need-auth" in url:
            kwargs.setdefault("headers", {})["Authorization"] = f"Bearer {self.vars['token']}"
        return kwargs

    @post_processor
    def warn_slow(self, response):
        if response.elapsed_ms > 600:
            print(f"  [SLOW] {response.request_name}: {response.elapsed_ms:.0f}ms")

    @task(3)
    async def health_check(self):
        resp = await self.client.get("/health", name="health")
        resp.assert_status(200)

    @task(2)
    async def get_json(self):
        resp = await self.client.get("/json", name="get_json")
        resp.assert_json_path("slideshow.author", "Yours Truly")

    @task(2)
    async def auth_flow(self):
        resp = await self.client.post("/auth/token",
                                      json={"user": "alice", "pass": "s3cr3t"},
                                      name="auth/token")
        token = resp.extract_json_path("access_token")
        resp2 = await self.client.get("/need-auth",
                                      name="need-auth",
                                      headers={"Authorization": f"Bearer {token}"})
        resp2.assert_status(200)

    @task(1)
    async def inventory_check(self):
        resp = await self.client.get("/inventory/42", name="inventory")
        data = resp.json()
        self.check("stock > 0",   data["stock"] > 0,         f"stock={data['stock']}")
        self.check("price valid", 0 < data["price"] < 10000, f"price={data['price']}")

    @task(1)
    async def slow_request(self):
        resp = await self.client.get("/slow", name="slow")
        resp.assert_status(200)
