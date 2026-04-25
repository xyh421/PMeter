from pmeter import HttpUser, between, task


class WebsiteUser(HttpUser):
    host = "https://httpbin.org"
    wait_time = between(0.2, 1.0)

    @task(3)
    async def get_health(self) -> None:
        resp = await self.client.get("/status/200", name="health")
        resp.assert_status(200)

    @task(1)
    async def get_json(self) -> None:
        resp = await self.client.get("/json", name="json")
        resp.assert_status(200).assert_json_path("slideshow.author", "Yours Truly")
