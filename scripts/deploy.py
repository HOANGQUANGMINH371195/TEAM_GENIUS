#!/usr/bin/env python3
"""Run an explicit Vercel frontend deploy using an ignored dotenv file."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values


def _dotenv_value(path: Path, name: str) -> str:
    values = dotenv_values(path)
    return str(values.get(name) or os.environ.get(name) or "").strip()


def _required(path: Path, name: str) -> str:
    value = _dotenv_value(path, name)
    if not value:
        raise SystemExit(f"Missing {name}; set it in {path} or the deploy environment.")
    return value


def deploy_vercel(path: Path) -> int:
    # Vercel CLI credentials may be stored in its local credential store.  A
    # long-lived VERCEL_TOKEN is optional; when absent we verify the existing
    # CLI session instead of forcing developers to duplicate credentials in
    # .env.  The deploy still requires an explicit project binding so `--yes`
    # can never silently create/deploy an unintended personal project.
    token = _dotenv_value(path, "VERCEL_TOKEN")
    env = os.environ.copy()
    if token:
        env["VERCEL_TOKEN"] = token
    # The local dotenv names deliberately avoid the reserved VERCEL_* prefix.
    # Translate them only in the child process that invokes the Vercel CLI.
    for dotenv_name, vercel_name in (
        ("DEPLOY_VERCEL_ORG_ID", "VERCEL_ORG_ID"),
        ("DEPLOY_VERCEL_PROJECT_ID", "VERCEL_PROJECT_ID"),
    ):
        value = _dotenv_value(path, dotenv_name)
        if value:
            env[vercel_name] = value
    project = _dotenv_value(path, "DEPLOY_VERCEL_PROJECT_ID")
    linked_project_files = (Path("web/.vercel/project.json"), Path(".vercel/project.json"))
    if not project and not any(item.is_file() for item in linked_project_files):
        raise SystemExit(
            "Set DEPLOY_VERCEL_PROJECT_ID (project name/ID) or run `vercel link` in web/ "
            "before deploying."
        )
    if not token:
        probe = subprocess.run(
            ["npx", "--yes", "vercel", "whoami"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode != 0:
            raise SystemExit("No Vercel CLI session found; run `vercel login` or set VERCEL_TOKEN.")
    # The existing Vercel project owns `web/` as its configured rootDirectory.
    # Run from the repository root; passing `--cwd web` makes the CLI resolve
    # that project root as `web/web` and fail before a deployment is created.
    command = ["npx", "--yes", "vercel", "deploy", "--prod", "--yes"]
    if project:
        command.extend(["--project", project])
    print("Starting Vercel production deploy for web/...")
    completed = subprocess.run(command, env=env, check=False)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("vercel",))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if not args.env_file.is_file():
        raise SystemExit(f"Missing {args.env_file}; copy .env.example to .env first.")
    return deploy_vercel(args.env_file)


if __name__ == "__main__":
    sys.exit(main())
