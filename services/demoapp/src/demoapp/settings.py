from shipwatch_common.settings import ServiceSettings


class DemoSettings(ServiceSettings):
    service_name: str = "demoapp"
    # Set from the image's build arg by `make deploy-demo`; "dev" when run from a checkout.
    git_sha: str = "dev"
    ship_logs: bool = True
    # Requests slower than this log a "slow request" warning.
    slow_ms: float = 250.0
    # Upper bound (ms) of the random latency a handler adds, so slow requests happen now and then.
    max_jitter_ms: float = 300.0
    # Share of requests that get the jitter at all; the rest answer immediately.
    jitter_rate: float = 0.02
