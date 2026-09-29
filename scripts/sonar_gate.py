#!/usr/bin/env python3
"""
sonar_gate.py — `static` check command for loop mode (SonarCloud/SonarQube).

Re-authored from analyze_sonarqube.py to close the verified false-green bug
(design §7.1): the original get_issues() swallowed RequestException and returned
[], which the caller read as "clean" → exit 0. A network blip therefore looked
like a passing gate.

Strict propagation here: ANY fetch fault → the result file carries
`status: "error"` and NO `total_issues` field, so the loop engine's clean-signal
rule (on_missing:"error") turns it into an ERROR verdict, never a green. A real
clean project is `status: "ok", total_issues: 0` — distinct from a failed fetch.

Usage:
  sonar_gate.py --out <result.json> [--project-key KEY] [--pull-request N] [--branch B]

Env: SONAR_TOKEN (required), SONAR_HOST_URL (default https://sonarcloud.io).

Exit codes: 0 = fetched (result written, may be clean or dirty);
            3 = fetch/config error (result written with status:"error").
The loop gate decides green/red from the *file*, not this exit code.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

PAGE_SIZE = 500
DEFAULT_HOST = "https://sonarcloud.io"


class FetchError(Exception):
    pass


def read_project_key(explicit):
    if explicit:
        return explicit
    for root in (os.getcwd(),):
        props = os.path.join(root, "sonar-project.properties")
        if os.path.isfile(props):
            with open(props, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("sonar.projectKey="):
                        return line.split("=", 1)[1].strip()
    raise FetchError("sonar.projectKey not found (pass --project-key or add sonar-project.properties)")


def fetch_page(host, token, params):
    url = host.rstrip("/") + "/api/issues/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise FetchError(f"HTTP {resp.status} from SonarQube")
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} from SonarQube: {exc.reason}")
    except urllib.error.URLError as exc:
        raise FetchError(f"network error contacting SonarQube: {exc.reason}")
    except (ValueError, OSError) as exc:
        raise FetchError(f"invalid response from SonarQube: {exc}")


def count_issues(host, token, project_key, pull_request, branch,
                 statuses=("OPEN", "CONFIRMED", "REOPENED")):
    """Return the total open-issue count. Raise FetchError on ANY fault —
    never silently return 0, which would be indistinguishable from clean."""
    total_reported = None
    collected = 0
    page = 1
    while True:
        params = {
            "componentKeys": project_key,
            "statuses": ",".join(statuses),
            "p": page,
            "ps": PAGE_SIZE,
        }
        if pull_request:
            params["pullRequest"] = pull_request
        elif branch and branch not in ("main", "master"):
            params["branch"] = branch

        data = fetch_page(host, token, params)
        issues = data.get("issues")
        if issues is None:
            raise FetchError("malformed response: missing 'issues'")
        collected += len(issues)
        total_reported = data.get("total", total_reported)
        if total_reported is None:
            raise FetchError("malformed response: missing 'total'")
        if collected >= total_reported or not issues:
            break
        page += 1
    return total_reported


def write_result(out_path, payload):
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="SonarCloud static gate (strict, no false-green).")
    parser.add_argument("--out", required=True, help="Path to write the result JSON.")
    parser.add_argument("--project-key")
    parser.add_argument("--pull-request")
    parser.add_argument("--branch")
    args = parser.parse_args(argv)

    host = os.environ.get("SONAR_HOST_URL", DEFAULT_HOST)
    token = os.environ.get("SONAR_TOKEN", "")

    try:
        if not token:
            raise FetchError("SONAR_TOKEN not set")
        project_key = read_project_key(args.project_key)
        total = count_issues(host, token, project_key, args.pull_request, args.branch)
    except FetchError as exc:
        # Strict: a fault is an error, NOT zero issues. No total_issues field.
        write_result(args.out, {
            "status": "error",
            "fetch_failed": True,
            "error": str(exc),
        })
        print(f"sonar_gate: error: {exc}", file=sys.stderr)
        return 3

    write_result(args.out, {
        "status": "ok",
        "fetch_failed": False,
        "total_issues": total,
    })
    print(f"sonar_gate: ok, total_issues={total}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
