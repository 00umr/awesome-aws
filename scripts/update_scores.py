#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Standalone script to update repository star scores in README.md.

Uses PyGithub with GITHUB_TOKEN environment variable for authentication.
Designed to run in GitHub Actions CI environment.
"""

import os
import re
import sys
from github import Github, Auth
from github.GithubException import UnknownObjectException, RateLimitExceededException


# Repos to exclude from scoring
REPOS_EXCLUDE_SCORE = [
    'https://github.com/donnemartin',
    'https://github.com/donnemartin/awesome-aws',
    'https://github.com/sindresorhus/awesome',
    'https://github.com/kilimchoi/engineering-blogs',
    'https://github.com/aws/aws-sdk-go/wiki',
    '#',
]


def get_github_client():
    """Initialize GitHub client with token from environment."""
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Error: GITHUB_TOKEN environment variable not set")
        sys.exit(1)
    return Github(auth=Auth.Token(token))


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


def process_readme(github_client, readme_path):
    """Process README and update star scores."""
    output = []
    repos_broken = []

    with open(readme_path, 'r') as f:
        lines = f.readlines()

    for line in lines:
        line = line.rstrip('\n')
        match = re.search(r'(https://github.com/[^)]*)', line)

        # If not processing a repo, just output the line
        if match is None:
            output.append(line)
            continue

        url = match.group(0)

        # If the repo is in the exclude list, just output the line
        if any(substr in url for substr in REPOS_EXCLUDE_SCORE):
            output.append(line)
            continue

        user_login, repo_name = extract_repo_params(url)
        if not user_login or not repo_name:
            output.append(line)
            continue

        try:
            repo = github_client.get_repo(f"{user_login}/{repo_name}")
            stars = repo.stargazers_count
            line = update_repo_score(line, stars)
            output.append(line)
        except UnknownObjectException:
            repos_broken.append(url)
            output.append(line)
        except RateLimitExceededException:
            print("Error: GitHub API rate limit exceeded")
            sys.exit(1)
        except Exception as e:
            print(f"Warning: Error fetching {url}: {e}")
            output.append(line)

    return output, repos_broken


def write_output(output, readme_path):
    """Write the updated content back to README."""
    with open(readme_path, 'w') as f:
        for line in output:
            f.write(line + '\n')


def main():
    readme_path = 'README.md'

    if not os.path.exists(readme_path):
        print(f"Error: {readme_path} not found")
        sys.exit(1)

    print("Initializing GitHub client...")
    github_client = get_github_client()

    print(f"Processing {readme_path}...")
    output, repos_broken = process_readme(github_client, readme_path)

    write_output(output, readme_path)

    if repos_broken:
        print("Broken repos found:")
        for repo in repos_broken:
            print(f"  {repo}")

    rate_limit = github_client.get_rate_limit().rate
    print(f"Rate limit remaining: {rate_limit.remaining}")
    print(f"Updated {readme_path}")


if __name__ == '__main__':
    main()
