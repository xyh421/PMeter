from pmeter import HttpUser, between, task


class WebsiteUser(HttpUser):
    host = "https://httpbin.org"
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self.client.session.headers.update({"User-Agent": "PMeter/0.1"})

    @task(3)
    def get_health(self) -> None:
        self.client.get("/status/200", name="health").assert_status(200)

    @task(1)
    def get_json(self) -> None:
        (
            self.client.get("/json", name="json")
            .assert_status(200)
            .assert_json_path("slideshow.author", "Yours Truly")
        )
