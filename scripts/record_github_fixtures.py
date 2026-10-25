"""Record the GitHub responses the agent-tool tests replay (services/worker/tests/fixtures/github/).

    uv run python scripts/record_github_fixtures.py

Uses GITHUB_TOKEN, or `gh auth token` if that is unset. Responses are trimmed to the fields the
tools read, so the fixtures stay small and hold nothing beyond public repo data. Re-running it
overwrites the files; check the test expectations afterwards, since real data can change.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

OUT = Path(__file__).resolve().parents[1] / "services/worker/tests/fixtures/github"
REPO = "encode/httpx"
DIFF_PRS = [3174, 3783]  # small docs PR (later reverted by #3192); PR with failing CI
CHURN_PR, CHURN_NOW, CHURN_DAYS = 3174, "2024-05-05T00:00:00Z", 90
AUTHOR = "shinyzhu"  # one merged PR (#3174), reverted by #3192
CHECK_SHAS = {
    "failing": "c1e73124c5d24aa6d61f0413d217f5810f62eb00",  # #3783: one failure, rest cancelled
    "passing": "dad77d69a4b634ef6e183581983cc925ef6cab43",  # #3739: success + one cancelled
}


def _token() -> str:
    if token := os.environ.get("GITHUB_TOKEN"):
        return token
    return subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _pick(obj: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: obj[k] for k in keys}


def _save(name: str, data: Any) -> None:
    (OUT / name).write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {name}")


def _search_items(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_count": body["total_count"],
        "items": [
            {
                "number": i["number"],
                "title": i["title"],
                "body": (i["body"] or "")[:300],
                "pull_request": {"merged_at": i["pull_request"]["merged_at"]},
            }
            for i in body["items"]
        ],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(base_url="https://api.github.com", headers=headers, timeout=20) as gh:

        def get(url: str, **params: Any) -> Any:
            response = gh.get(url, params=params)
            response.raise_for_status()
            return response.json()

        for n in DIFF_PRS:
            pr = get(f"/repos/{REPO}/pulls/{n}")
            _save(
                f"pr_{n}.json",
                {
                    **_pick(
                        pr,
                        "number",
                        "title",
                        "created_at",
                        "changed_files",
                        "additions",
                        "deletions",
                    ),
                    "user": {"login": pr["user"]["login"]},
                    "head": {"sha": pr["head"]["sha"]},
                    "base": {"ref": pr["base"]["ref"]},
                },
            )
            files = get(f"/repos/{REPO}/pulls/{n}/files", per_page=100)
            fields = ("filename", "status", "additions", "deletions")
            _save(
                f"pr_{n}_files.json",
                [{**_pick(f, *fields), "patch": f.get("patch")} for f in files],
            )

        since = datetime.fromisoformat(CHURN_NOW) - timedelta(days=CHURN_DAYS)
        since_s = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        paths = [f["filename"] for f in get(f"/repos/{REPO}/pulls/{CHURN_PR}/files")]
        churn = {
            p: [{"sha": c["sha"]} for c in get(f"/repos/{REPO}/commits", path=p, since=since_s)]
            for p in paths
        }
        _save("churn.json", {"now": CHURN_NOW, "days": CHURN_DAYS, "commits": churn})

        search = {"sort": "created", "order": "desc", "per_page": 100}
        q = f"repo:{REPO} is:pr is:merged author:{AUTHOR}"
        _save("search_merged_author.json", _search_items(get("/search/issues", q=q, **search)))
        q = f"repo:{REPO} is:pr is:merged in:title Revert"
        _save("search_reverts.json", _search_items(get("/search/issues", q=q, **search)))

        for name, sha in CHECK_SHAS.items():
            body = get(f"/repos/{REPO}/commits/{sha}/check-runs", per_page=100)
            runs = [_pick(r, "name", "status", "conclusion") for r in body["check_runs"]]
            _save(
                f"check_runs_{name}.json", {"total_count": body["total_count"], "check_runs": runs}
            )


if __name__ == "__main__":
    main()
