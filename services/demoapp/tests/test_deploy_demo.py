import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest
from shipwatch_common.schemas import Deploy
from shipwatch_common.settings import ServiceSettings

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("deploy_demo", ROOT / "scripts/deploy_demo.py")
deploy_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_demo)

SHA = "3f2c" + "0" * 36


def _gh(pulls):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return json.dumps(pulls)

    return runner, calls


def test_find_pr_takes_the_merged_one():
    runner, calls = _gh(
        [{"number": 4, "merged_at": None}, {"number": 7, "merged_at": "2026-11-16"}]
    )
    assert deploy_demo.find_pr("octo/sandbox", SHA, runner) == 7
    assert calls == [["gh", "api", f"repos/octo/sandbox/commits/{SHA}/pulls"]]


def test_find_pr_none_when_no_merged_pr():
    runner, _ = _gh([])
    assert deploy_demo.find_pr("octo/sandbox", SHA, runner) is None


@pytest.mark.parametrize("repo", ["", "your-user/your-repo"])
def test_find_pr_skips_github_without_a_repo(repo):
    runner, calls = _gh([{"number": 1, "merged_at": "x"}])
    assert deploy_demo.find_pr(repo, SHA, runner) is None
    assert calls == []


def test_find_pr_survives_gh_failing():
    def runner(cmd):
        raise subprocess.CalledProcessError(1, cmd)

    assert deploy_demo.find_pr("octo/sandbox", SHA, runner) is None


def test_export_commit_replaces_the_directory(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = b"print('hi')\n"
        info = tarfile.TarInfo("services/demoapp/app.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    dest = tmp_path / "src"
    (dest / "stale").mkdir(parents=True)

    deploy_demo.export_commit(SHA, dest, runner=lambda sha: buf.getvalue())

    assert (dest / "services/demoapp/app.py").read_bytes() == b"print('hi')\n"
    assert not (dest / "stale").exists()


def test_publish_deploy(kafka_topic, consume_all):
    deploy = Deploy(service="demoapp", repo="octo/sandbox", sha=SHA, pr_number=7)
    deploy_demo.publish_deploy(deploy, ServiceSettings().kafka_bootstrap_servers, kafka_topic)

    (message,) = consume_all(kafka_topic, 1)
    assert message.key() == b"demoapp"
    assert Deploy.from_bytes(message.value()) == deploy


def test_publish_deploy_fails_loudly_without_a_broker():
    deploy = Deploy(service="demoapp", repo="octo/sandbox", sha=SHA)
    with pytest.raises(RuntimeError, match="deploy marker"):
        deploy_demo.publish_deploy(deploy, "127.0.0.1:1", "deploys", **{"delivery.timeout.ms": 500})
