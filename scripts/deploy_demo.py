"""Deploy demoapp at a commit and announce it on `deploys` (docs/SPEC_W4.md, "Deploy marker").

    uv run python scripts/deploy_demo.py <sha> [--pr N]      # what `make deploy-demo` runs
    uv run python scripts/deploy_demo.py local               # the working tree, uncommitted too

The container is rebuilt from `git archive <sha>`, so it runs exactly that commit. The Deploy
message is published only once the container is healthy, and the script fails if it isn't acked.
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from shipwatch_common.kafka import Producer, ensure_topics
from shipwatch_common.schemas import Deploy
from shipwatch_common.settings import ServiceSettings

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / ".deploy/demoapp-src"
SERVICE = "demoapp"

# Runs a command and returns its stdout; raises CalledProcessError on a non-zero exit.
Runner = Callable[[list[str]], str]


class DeploySettings(ServiceSettings):
    github_repo: str = ""


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout


def find_pr(repo: str, sha: str, runner: Runner = run) -> int | None:
    """The first merged PR GitHub associates with `sha`, or None (no repo, no PR, gh failing)."""
    if not repo or repo == "your-user/your-repo":
        return None
    try:
        out = runner(["gh", "api", f"repos/{repo}/commits/{sha}/pulls"])
        pulls = json.loads(out or "[]")
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"deploy_demo: couldn't look up the PR for {sha[:12]}: {exc}", file=sys.stderr)
        return None
    merged = [p["number"] for p in pulls if p.get("merged_at")]
    return merged[0] if merged else None


def export_commit(
    sha: str, dest: Path = EXPORT, runner: Callable[..., bytes] | None = None
) -> None:
    """Replace `dest` with the repo's tree at `sha`."""
    data = (runner or _git_archive)(sha)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")


def _git_archive(sha: str) -> bytes:
    return subprocess.run(
        ["git", "archive", "--format=tar", sha], cwd=ROOT, check=True, capture_output=True
    ).stdout


def publish_deploy(deploy: Deploy, bootstrap_servers: str, topic: str, **config: object) -> None:
    producer = Producer(bootstrap_servers, **config)
    producer.publish(topic, deploy.service, deploy.to_bytes())
    (result,) = producer.flush_results(15)
    if not result.ok:
        raise RuntimeError(f"publishing the deploy marker failed: {result.error}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("sha", help="commit-ish to deploy, or 'local' for the working tree")
    parser.add_argument("--pr", type=int, default=None, help="PR number (default: ask GitHub)")
    args = parser.parse_args()
    settings = DeploySettings()

    head = run(["git", "rev-parse", "HEAD"]).strip()
    if args.sha == "local":
        sha, context = f"{head}-dirty", "."
    else:
        try:
            sha = run(["git", "rev-parse", "--verify", f"{args.sha}^{{commit}}"]).strip()
        except subprocess.CalledProcessError:
            print(f"deploy_demo: unknown commit {args.sha!r}", file=sys.stderr)
            return 1
        export_commit(sha)
        if not (EXPORT / "services/demoapp/Dockerfile").exists():
            print(f"deploy_demo: {sha[:12]} has no services/demoapp", file=sys.stderr)
            return 1
        context = str(EXPORT.relative_to(ROOT))

    # redpanda-init only runs on `make up`; a stack started before week 4 lacks these topics.
    ensure_topics(
        settings.kafka_bootstrap_servers, {settings.logs_topic: 3, settings.deploys_topic: 1}
    )
    print(f"deploy_demo: building and starting {SERVICE} at {sha[:12]} …", flush=True)
    # Output streams through: the first build downloads every dependency and takes a while.
    compose = subprocess.run(
        ["docker", "compose", "--profile", "demo", "up", "-d", "--build", "--wait", SERVICE],
        cwd=ROOT,
        env={**os.environ, "DEMOAPP_CONTEXT": context, "DEMOAPP_SHA": sha},
    )
    if compose.returncode != 0:
        print(f"deploy_demo: {SERVICE} did not come up healthy", file=sys.stderr)
        return 1

    if args.pr is not None:
        pr_number = args.pr
    else:  # an uncommitted tree belongs to no PR
        pr_number = None if args.sha == "local" else find_pr(settings.github_repo, sha)
    deploy = Deploy(
        service=SERVICE,
        repo=settings.github_repo,
        sha=sha,
        pr_number=pr_number,
        deployed_at=datetime.now(UTC),
    )
    publish_deploy(deploy, settings.kafka_bootstrap_servers, settings.deploys_topic)
    print(f"deploy_demo: published to {settings.deploys_topic}: {deploy.model_dump_json()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
