#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27.0",
#     "rich>=13.7.0",
# ]
# ///

# Originally based on https://github.com/donnemartin/awesome-aws/blob/master/awesome/awesome.py
# Original work Copyright 2015 Donne Martin
# Creative Commons Attribution 4.0 International License (CC BY 4.0)
# http://creativecommons.org/licenses/by/4.0/
#
# Substantially rewritten to use async/httpx for parallel GitHub API requests.

"""
Update repository star scores and freshness indicators in README.md.

Usage:
    GITHUB_TOKEN=xxx uv run scripts/update_scores.py
"""

import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn


GITHUB_API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT = 10.0
MAX_CONCURRENT = 10
DEFAULT_README_PATH = "README.md"

# Repos to exclude from scoring
REPOS_EXCLUDE_SCORE = [
    'https://github.com/donnemartin',
    'https://github.com/donnemartin/awesome-aws',
    'https://github.com/sindresorhus/awesome',
    'https://github.com/kilimchoi/engineering-blogs',
    'https://github.com/aws/aws-sdk-go/wiki',
    '#',
]


@dataclass
class RepoResult:
    line_idx: int
    url: str
    stars: int | None = None
    pushed_at: datetime | None = None
    error: str | None = None


def extract_repo_params(url):
    """Extract user login and repo name from a GitHub URL."""
    tokens = url.split('/')
    if len(tokens) >= 5:
        return tokens[3], tokens[4]
    return None, None


def score_repo(stars):
    """Assign the Fiery Meter of AWSome score based on star count."""
    if stars < 100:
        return 0
    elif stars < 200:
        return 1
    elif stars < 500:
        return 2
    elif stars < 1000:
        return 3
    elif stars < 2000:
        return 4
    else:
        return 5


def score_freshness(last_pushed):
    """Assign freshness indicator based on last activity date."""
    if last_pushed is None:
        return ""

    now = datetime.now(timezone.utc)
    days_stale = (now - last_pushed.replace(tzinfo=timezone.utc)).days

    if days_stale > 365 * 3:
        return " :zzz:"
    elif days_stale > 365 * 2:
        return " :hourglass:"
    else:
        return ""


def update_repo_score(line, stars):
    """Update the repo's markdown with its new score."""
    cached_score = line.count(':fire:')
    score = score_repo(stars)

    if score != cached_score:
        prefix = ''
        if cached_score == 0:
            prefix = ' '
        cached_fires = ':fire:' * cached_score
        fires = ':fire:' * score
        line = line.replace(cached_fires + ']', prefix + fires + ']')

    return line


def update_repo_freshness(line, last_pushed):
    """Update/add freshness indicator to repo line."""
    freshness = score_freshness(last_pushed)

    line = line.replace(' :zzz:', '').replace(' :hourglass:', '')

    if freshness:
        # Add freshness indicator after fires, before closing bracket
        # Pattern: [repo-name :fire::fire:](url) -> [repo-name :fire::fire: :zzz:](url)
        line = re.sub(r'(\])\(', f'{freshness}](', line, count=1)

    return line


async def fetch_repo_info(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    url: str,
    line_idx: int,
    headers: dict,
    semaphore: asyncio.Semaphore,
) -> RepoResult:
    """Fetch repo stars and pushed_at from GitHub API."""
    async with semaphore:
        try:
            api_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
            response = await client.get(api_url, headers=headers)

            if response.status_code == 404:
                return RepoResult(line_idx=line_idx, url=url, error="Not found")
            if response.status_code == 403:
                if "rate limit" in response.text.lower():
                    return RepoResult(line_idx=line_idx, url=url, error="Rate limited")
                return RepoResult(line_idx=line_idx, url=url, error="Forbidden")
            if response.status_code != 200:
                return RepoResult(line_idx=line_idx, url=url, error=f"HTTP {response.status_code}")

            data = response.json()
            pushed_at = None
            if data.get("pushed_at"):
                pushed_at = datetime.fromisoformat(data["pushed_at"].replace("Z", "+00:00"))

            return RepoResult(
                line_idx=line_idx,
                url=url,
                stars=data["stargazers_count"],
                pushed_at=pushed_at,
            )
        except httpx.TimeoutException:
            return RepoResult(line_idx=line_idx, url=url, error="Timeout")
        except Exception as e:
            return RepoResult(line_idx=line_idx, url=url, error=str(e)[:50])


async def fetch_all_repos(
    repos_to_fetch: list[tuple[int, str, str, str]],
    github_token: str | None,
    console: Console,
) -> list[RepoResult]:
    """Fetch all repos concurrently."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "awesome-aws-score-updater/1.0",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(DEFAULT_TIMEOUT)) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[green]Fetching repos...", total=len(repos_to_fetch))

            tasks = [
                fetch_repo_info(client, owner, repo, url, idx, headers, semaphore)
                for idx, owner, repo, url in repos_to_fetch
            ]

            for coro in asyncio.as_completed(tasks):
                result = await coro
                results.append(result)
                progress.update(task, advance=1)

    return results


async def main():
    console = Console()

    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        console.print("[green]Using GitHub token (5000 requests/hour)[/green]")
    else:
        console.print("[yellow]No GITHUB_TOKEN - using unauthenticated API (60 requests/hour)[/yellow]")

    if not os.path.exists(DEFAULT_README_PATH):
        console.print(f"[red]Error: {DEFAULT_README_PATH} not found[/red]")
        sys.exit(1)

    with open(DEFAULT_README_PATH, 'r') as f:
        lines = [line.rstrip('\n') for line in f.readlines()]

    repos_to_fetch = []
    for idx, line in enumerate(lines):
        match = re.search(r'(https://github.com/[^)]*)', line)
        if not match:
            continue
        url = match.group(0)
        if any(substr in url for substr in REPOS_EXCLUDE_SCORE):
            continue
        user_login, repo_name = extract_repo_params(url)
        if user_login and repo_name:
            repos_to_fetch.append((idx, user_login, repo_name, url))

    console.print(f"Found [bold]{len(repos_to_fetch)}[/bold] repos to update")

    results = await fetch_all_repos(repos_to_fetch, github_token, console)

    results_by_idx = {r.line_idx: r for r in results}
    repos_broken = []

    for idx, line in enumerate(lines):
        if idx in results_by_idx:
            r = results_by_idx[idx]
            if r.error == "Rate limited":
                console.print("[red]Error: GitHub API rate limit exceeded[/red]")
                sys.exit(1)
            elif r.error:
                repos_broken.append(r.url)
            else:
                lines[idx] = update_repo_score(line, r.stars)
                lines[idx] = update_repo_freshness(lines[idx], r.pushed_at)

    with open(DEFAULT_README_PATH, 'w') as f:
        for line in lines:
            f.write(line + '\n')

    if repos_broken:
        console.print("[yellow]Broken repos:[/yellow]")
        for url in repos_broken:
            console.print(f"  {url}")

    console.print(f"[green]Updated {DEFAULT_README_PATH}[/green]")


if __name__ == "__main__":
    asyncio.run(main())
