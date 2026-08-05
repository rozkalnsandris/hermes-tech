#!/usr/bin/env python3
"""Configure and verify Hermes Tech GitHub merge and main-branch policy.

The tool is intentionally fail-closed. It requires an exact main SHA, a
successful CI check on that SHA, and exactly one write-enabled deploy key with
an explicitly supplied title before it changes repository administration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
INTEGRITY_RULESET_NAME = "Hermes Tech main integrity"
CODE_GATE_RULESET_NAME = "Hermes Tech code PR gate"
MANAGED_RULESET_NAMES = {INTEGRITY_RULESET_NAME, CODE_GATE_RULESET_NAME}


class PolicyError(RuntimeError):
    """A policy precondition or verification failure."""


class ApiError(PolicyError):
    """A GitHub API failure with a stable status code."""

    def __init__(self, status: int, method: str, path: str, detail: str) -> None:
        super().__init__(f"GitHub API {method} {path} failed with HTTP {status}: {detail}")
        self.status = status
        self.method = method
        self.path = path
        self.detail = detail


@dataclass(frozen=True)
class RepositoryRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def api_prefix(self) -> str:
        owner = urllib.parse.quote(self.owner, safe="")
        name = urllib.parse.quote(self.name, safe="")
        return f"/repos/{owner}/{name}"


class GitHubApi:
    def __init__(self, token: str, *, api_root: str = API_ROOT) -> None:
        token = token.strip()
        if not token:
            raise PolicyError("GH_TOKEN or GITHUB_TOKEN is required")
        self._token = token
        self._api_root = api_root.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        if not path.startswith("/"):
            raise PolicyError(f"internal error: API path must be absolute: {path!r}")
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self._api_root}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "hermes-tech-main-policy",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            detail = _api_error_detail(raw)
            raise ApiError(exc.code, method, path, detail) from None
        except urllib.error.URLError as exc:
            raise PolicyError(f"GitHub API network failure for {method} {path}: {exc.reason}") from None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PolicyError(f"GitHub API returned invalid JSON for {method} {path}: {exc}") from None


def _api_error_detail(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "non-JSON error response"
    if isinstance(payload, Mapping):
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message
    return "unspecified API error"


def parse_repository(value: str) -> RepositoryRef:
    parts = value.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise argparse.ArgumentTypeError("repository must be in owner/name form")
    return RepositoryRef(parts[0], parts[1])


def validate_sha(value: str) -> str:
    candidate = value.strip().lower()
    if len(candidate) != 40 or any(character not in "0123456789abcdef" for character in candidate):
        raise argparse.ArgumentTypeError("expected-main-sha must be a full 40-character SHA-1")
    return candidate


def integrity_ruleset_payload() -> dict[str, Any]:
    return {
        "name": INTEGRITY_RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {
            "ref_name": {
                "include": ["refs/heads/main"],
                "exclude": [],
            }
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_linear_history"},
        ],
    }


def code_gate_ruleset_payload(status_check: str) -> dict[str, Any]:
    if not status_check.strip():
        raise PolicyError("status check context must not be empty")
    return {
        "name": CODE_GATE_RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [
            {
                "actor_id": None,
                "actor_type": "DeployKey",
                "bypass_mode": "always",
            }
        ],
        "conditions": {
            "ref_name": {
                "include": ["refs/heads/main"],
                "exclude": [],
            }
        },
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "allowed_merge_methods": ["squash"],
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_approving_review_count": 0,
                    "required_review_thread_resolution": True,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [{"context": status_check}],
                    "strict_required_status_checks_policy": True,
                },
            },
        ],
    }


def repository_settings_payload() -> dict[str, Any]:
    return {
        "allow_merge_commit": False,
        "allow_rebase_merge": False,
        "allow_squash_merge": True,
        "delete_branch_on_merge": True,
    }


def require_repository_state(repo_payload: Mapping[str, Any], repository: RepositoryRef) -> None:
    if repo_payload.get("full_name") not in (None, repository.full_name):
        raise PolicyError(
            f"repository identity mismatch: expected={repository.full_name} "
            f"actual={repo_payload.get('full_name')!r}"
        )
    if repo_payload.get("default_branch") != "main":
        raise PolicyError(f"default branch must be main, got {repo_payload.get('default_branch')!r}")
    permissions = repo_payload.get("permissions")
    if isinstance(permissions, Mapping) and permissions.get("admin") is not True:
        raise PolicyError("authenticated identity does not have repository admin permission")


def require_exact_main_sha(api: GitHubApi, repository: RepositoryRef, expected_sha: str) -> None:
    payload = api.request("GET", f"{repository.api_prefix}/git/ref/heads/main")
    actual_sha = _nested_string(payload, "object", "sha")
    if actual_sha != expected_sha:
        raise PolicyError(f"main SHA changed: expected={expected_sha} actual={actual_sha}")


def require_successful_check(
    api: GitHubApi,
    repository: RepositoryRef,
    expected_sha: str,
    status_check: str,
) -> None:
    payload = api.request(
        "GET",
        f"{repository.api_prefix}/commits/{expected_sha}/check-runs?per_page=100",
    )
    runs = payload.get("check_runs") if isinstance(payload, Mapping) else None
    if not isinstance(runs, Sequence):
        raise PolicyError("GitHub check-runs response is missing check_runs")
    matching = [
        run
        for run in runs
        if isinstance(run, Mapping) and run.get("name") == status_check
    ]
    successful = [run for run in matching if run.get("conclusion") == "success"]
    if not successful:
        observed = sorted(
            {
                f"{run.get('name')}={run.get('conclusion')}"
                for run in runs
                if isinstance(run, Mapping)
            }
        )
        raise PolicyError(
            f"required successful check {status_check!r} is absent on {expected_sha}; "
            f"observed={observed}"
        )


def require_single_write_deploy_key(
    api: GitHubApi,
    repository: RepositoryRef,
    expected_title: str,
) -> Mapping[str, Any]:
    payload = api.request("GET", f"{repository.api_prefix}/keys?per_page=100")
    if not isinstance(payload, Sequence):
        raise PolicyError("GitHub deploy-key response is not a list")
    write_keys = [
        key
        for key in payload
        if isinstance(key, Mapping) and key.get("read_only") is False
    ]
    if len(write_keys) != 1:
        titles = sorted(str(key.get("title")) for key in write_keys)
        raise PolicyError(
            f"exactly one write-enabled deploy key is required; count={len(write_keys)} titles={titles}"
        )
    key = write_keys[0]
    if key.get("title") != expected_title:
        raise PolicyError(
            f"write-enabled deploy key title mismatch: expected={expected_title!r} "
            f"actual={key.get('title')!r}"
        )
    if not isinstance(key.get("id"), int):
        raise PolicyError("write-enabled deploy key has no numeric id")
    return key


def require_no_classic_branch_protection(api: GitHubApi, repository: RepositoryRef) -> None:
    path = f"{repository.api_prefix}/branches/main/protection"
    try:
        api.request("GET", path)
    except ApiError as exc:
        if exc.status == 404:
            return
        raise
    raise PolicyError(
        "classic main branch protection already exists; remove or explicitly reconcile it "
        "before applying layered repository rulesets"
    )


def list_repository_rulesets(api: GitHubApi, repository: RepositoryRef) -> list[Mapping[str, Any]]:
    payload = api.request(
        "GET",
        f"{repository.api_prefix}/rulesets?includes_parents=false&targets=branch&per_page=100",
    )
    if not isinstance(payload, Sequence):
        raise PolicyError("GitHub rulesets response is not a list")
    return [item for item in payload if isinstance(item, Mapping)]


def _ruleset_targets_main(payload: Mapping[str, Any]) -> bool:
    conditions = payload.get("conditions")
    if not isinstance(conditions, Mapping):
        return False
    ref_name = conditions.get("ref_name")
    if not isinstance(ref_name, Mapping):
        return False
    include = ref_name.get("include")
    if not isinstance(include, Sequence):
        return False
    return bool({"refs/heads/main", "~DEFAULT_BRANCH", "~ALL"}.intersection(include))


def require_no_unmanaged_main_rulesets(
    api: GitHubApi,
    repository: RepositoryRef,
    summaries: Iterable[Mapping[str, Any]],
) -> None:
    conflicting: list[str] = []
    for summary in summaries:
        name = summary.get("name")
        if name in MANAGED_RULESET_NAMES:
            continue
        if summary.get("enforcement") not in ("active", "enabled"):
            continue
        ruleset_id = summary.get("id")
        if not isinstance(ruleset_id, int):
            raise PolicyError(f"ruleset {name!r} has no numeric id")
        detail = api.request("GET", f"{repository.api_prefix}/rulesets/{ruleset_id}")
        if isinstance(detail, Mapping) and _ruleset_targets_main(detail):
            conflicting.append(str(name))
    if conflicting:
        raise PolicyError(f"unmanaged active rulesets also target main: {sorted(conflicting)}")


def _find_managed_ruleset(
    summaries: Iterable[Mapping[str, Any]],
    name: str,
) -> Mapping[str, Any] | None:
    matching = [summary for summary in summaries if summary.get("name") == name]
    if len(matching) > 1:
        raise PolicyError(f"duplicate managed rulesets named {name!r}")
    return matching[0] if matching else None


def upsert_ruleset(
    api: GitHubApi,
    repository: RepositoryRef,
    summaries: Iterable[Mapping[str, Any]],
    payload: Mapping[str, Any],
) -> int:
    name = str(payload["name"])
    existing = _find_managed_ruleset(summaries, name)
    if existing is None:
        response = api.request("POST", f"{repository.api_prefix}/rulesets", payload)
    else:
        ruleset_id = existing.get("id")
        if not isinstance(ruleset_id, int):
            raise PolicyError(f"managed ruleset {name!r} has no numeric id")
        response = api.request("PUT", f"{repository.api_prefix}/rulesets/{ruleset_id}", payload)
    if not isinstance(response, Mapping) or not isinstance(response.get("id"), int):
        raise PolicyError(f"GitHub did not return a numeric id for ruleset {name!r}")
    return int(response["id"])


def _require_subset(actual: Any, expected: Any, path: str = "payload") -> None:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            raise PolicyError(f"{path} must be an object")
        for key, expected_value in expected.items():
            if key not in actual:
                raise PolicyError(f"{path}.{key} is missing")
            _require_subset(actual[key], expected_value, f"{path}.{key}")
        return
    if isinstance(expected, list):
        if not isinstance(actual, Sequence) or isinstance(actual, (str, bytes, bytearray)):
            raise PolicyError(f"{path} must be a list")
        if path.endswith(".rules"):
            actual_by_type = {
                item.get("type"): item
                for item in actual
                if isinstance(item, Mapping) and isinstance(item.get("type"), str)
            }
            for item in expected:
                expected_type = item.get("type")
                if expected_type not in actual_by_type:
                    raise PolicyError(f"{path} is missing rule type {expected_type!r}")
                _require_subset(actual_by_type[expected_type], item, f"{path}[{expected_type}]")
            return
        if actual != expected:
            raise PolicyError(f"{path} mismatch: expected={expected!r} actual={actual!r}")
        return
    if actual != expected:
        raise PolicyError(f"{path} mismatch: expected={expected!r} actual={actual!r}")


def verify_managed_rulesets(
    api: GitHubApi,
    repository: RepositoryRef,
    status_check: str,
) -> dict[str, int]:
    summaries = list_repository_rulesets(api, repository)
    result: dict[str, int] = {}
    for expected in (integrity_ruleset_payload(), code_gate_ruleset_payload(status_check)):
        name = str(expected["name"])
        summary = _find_managed_ruleset(summaries, name)
        if summary is None:
            raise PolicyError(f"managed ruleset is missing: {name}")
        ruleset_id = summary.get("id")
        if not isinstance(ruleset_id, int):
            raise PolicyError(f"managed ruleset {name!r} has no numeric id")
        detail = api.request("GET", f"{repository.api_prefix}/rulesets/{ruleset_id}")
        _require_subset(detail, expected, f"ruleset[{name}]")
        result[name] = ruleset_id
    require_no_unmanaged_main_rulesets(api, repository, summaries)
    return result


def verify_repository_settings(repo_payload: Mapping[str, Any]) -> None:
    expected = repository_settings_payload()
    for key, value in expected.items():
        if repo_payload.get(key) is not value:
            raise PolicyError(
                f"repository setting {key} mismatch: expected={value!r} actual={repo_payload.get(key)!r}"
            )


def _nested_string(payload: Any, *keys: str) -> str:
    current = payload
    for key in keys:
        if not isinstance(current, Mapping):
            raise PolicyError(f"GitHub response is missing {'.'.join(keys)}")
        current = current.get(key)
    if not isinstance(current, str):
        raise PolicyError(f"GitHub response is missing {'.'.join(keys)}")
    return current


def preflight(
    api: GitHubApi,
    repository: RepositoryRef,
    expected_sha: str,
    deploy_key_title: str,
    status_check: str,
) -> dict[str, Any]:
    repo_payload = api.request("GET", repository.api_prefix)
    if not isinstance(repo_payload, Mapping):
        raise PolicyError("GitHub repository response is not an object")
    require_repository_state(repo_payload, repository)
    require_exact_main_sha(api, repository, expected_sha)
    require_successful_check(api, repository, expected_sha, status_check)
    deploy_key = require_single_write_deploy_key(api, repository, deploy_key_title)
    require_no_classic_branch_protection(api, repository)
    summaries = list_repository_rulesets(api, repository)
    require_no_unmanaged_main_rulesets(api, repository, summaries)
    return {
        "repository": repository.full_name,
        "main_sha": expected_sha,
        "status_check": status_check,
        "write_deploy_key": {
            "id": deploy_key["id"],
            "title": deploy_key["title"],
        },
        "managed_rulesets_present": sorted(
            str(item.get("name"))
            for item in summaries
            if item.get("name") in MANAGED_RULESET_NAMES
        ),
    }


def apply_policy(
    api: GitHubApi,
    repository: RepositoryRef,
    expected_sha: str,
    deploy_key_title: str,
    status_check: str,
    confirmation: str,
) -> dict[str, Any]:
    expected_confirmation = f"APPLY {repository.full_name}@{expected_sha}"
    if confirmation != expected_confirmation:
        raise PolicyError(
            f"confirmation mismatch; required exact value: {expected_confirmation!r}"
        )
    evidence = preflight(api, repository, expected_sha, deploy_key_title, status_check)
    summaries = list_repository_rulesets(api, repository)
    integrity_id = upsert_ruleset(api, repository, summaries, integrity_ruleset_payload())
    summaries = list_repository_rulesets(api, repository)
    code_gate_id = upsert_ruleset(
        api,
        repository,
        summaries,
        code_gate_ruleset_payload(status_check),
    )
    api.request("PATCH", repository.api_prefix, repository_settings_payload())
    verified = verify_policy(
        api,
        repository,
        expected_sha,
        deploy_key_title,
        status_check,
        require_no_classic=True,
    )
    verified["applied_ruleset_ids"] = {
        INTEGRITY_RULESET_NAME: integrity_id,
        CODE_GATE_RULESET_NAME: code_gate_id,
    }
    verified["preflight"] = evidence
    return verified


def verify_policy(
    api: GitHubApi,
    repository: RepositoryRef,
    expected_sha: str,
    deploy_key_title: str,
    status_check: str,
    *,
    require_no_classic: bool = True,
) -> dict[str, Any]:
    repo_payload = api.request("GET", repository.api_prefix)
    if not isinstance(repo_payload, Mapping):
        raise PolicyError("GitHub repository response is not an object")
    require_repository_state(repo_payload, repository)
    verify_repository_settings(repo_payload)
    require_exact_main_sha(api, repository, expected_sha)
    require_successful_check(api, repository, expected_sha, status_check)
    deploy_key = require_single_write_deploy_key(api, repository, deploy_key_title)
    if require_no_classic:
        require_no_classic_branch_protection(api, repository)
    ruleset_ids = verify_managed_rulesets(api, repository, status_check)
    return {
        "repository": repository.full_name,
        "main_sha": expected_sha,
        "status_check": status_check,
        "write_deploy_key": {
            "id": deploy_key["id"],
            "title": deploy_key["title"],
        },
        "repository_settings": repository_settings_payload(),
        "ruleset_ids": ruleset_ids,
        "verified": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "apply", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--repository", required=True, type=parse_repository)
        subparser.add_argument("--expected-main-sha", required=True, type=validate_sha)
        subparser.add_argument("--deploy-key-title", required=True)
        subparser.add_argument("--status-check", default="validate")
        if command == "apply":
            subparser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    try:
        api = GitHubApi(token)
        if args.command == "preflight":
            result = preflight(
                api,
                args.repository,
                args.expected_main_sha,
                args.deploy_key_title,
                args.status_check,
            )
        elif args.command == "apply":
            result = apply_policy(
                api,
                args.repository,
                args.expected_main_sha,
                args.deploy_key_title,
                args.status_check,
                args.confirm,
            )
        else:
            result = verify_policy(
                api,
                args.repository,
                args.expected_main_sha,
                args.deploy_key_title,
                args.status_check,
            )
    except PolicyError as exc:
        print(f"KĻŪDA: GITHUB_MAIN_POLICY: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
