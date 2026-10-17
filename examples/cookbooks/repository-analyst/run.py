import argparse, json, os, urllib.request
from client import ask
from query import execute

REPOS = [
    "fw-ai/cookbook",
    "crusoecloud/crusoe-cookbook",
    "crusoecloud/crusoe-developer-hub",
    "perplexityai/api-cookbook",
    "youdotcom-oss/agent-skills",
    "youdotcom-oss/ydc-research-sample",
]


def fetch_records():
    rows = []
    for repo in REPOS:
        headers = {"User-Agent": "AYA-repository-analyst"}
        if os.environ.get("GH_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
        with urllib.request.urlopen(
            urllib.request.Request(
                "https://api.github.com/repos/" + repo, headers=headers
            ),
            timeout=30,
        ) as response:
            r = json.load(response)
        rows.append(
            {
                "repo": r["full_name"],
                "owner": r["owner"]["login"],
                "stars": r["stargazers_count"],
                "forks": r["forks_count"],
                "issues": r["open_issues_count"],
                "license": (r.get("license") or {}).get("spdx_id"),
                "description": r["description"],
                "url": r["html_url"],
            }
        )
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    args = parser.parse_args()
    key = os.environ.get("CRUSOE_API_KEY")
    if not key:
        parser.error("Set CRUSOE_API_KEY")
    print(
        json.dumps(
            ask(args.question, fetch_records(), key, execute),
            indent=2,
            ensure_ascii=False,
        )
    )
