import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

class GitHubCollector:
    def __init__(self, config):
        self.config = config
        self.base_url = config.settings["github_api"]["base_url"]
        self.graphql_url = config.settings["github_api"]["graphql_url"]

    def _make_request(self, url: str, params: Optional[Dict] = None) -> Dict[Any, Any]:
        """Make a GET request to GitHub API"""
        response = requests.get(url, headers=self.config.headers, params=params)
        response.raise_for_status()
        return response.json()

    def _make_graphql_request(self, query: str, variables: Optional[Dict] = None) -> Dict[Any, Any]:
        """Make a GraphQL request to GitHub API"""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        response = requests.post(self.graphql_url, headers=self.config.graphql_headers, json=payload)
        response.raise_for_status()

        data = response.json()
        if "errors" in data:
            raise Exception(f"GraphQL error: {data['errors']}")
        return data

    def get_org_repositories(self) -> List[str]:
        """Get all repository names from the organization"""
        if self.config.github_org:
            url = f"{self.base_url}/orgs/{self.config.github_org}/repos"
            params = {"per_page": 100, "type": "all"}

            all_repos = []
            page = 1

            while True:
                params["page"] = page
                repos_data = self._make_request(url, params)

                if not repos_data:
                    break

                for repo in repos_data:
                    all_repos.append(repo["name"])

                if len(repos_data) < 100:
                    break

                page += 1

            return all_repos
        elif self.config.github_owner:
            url = f"{self.base_url}/users/{self.config.github_owner}/repos"
            params = {"per_page": 100, "type": "all"}

            all_repos = []
            page = 1

            while True:
                params["page"] = page
                repos_data = self._make_request(url, params)

                if not repos_data:
                    break

                for repo in repos_data:
                    all_repos.append(repo["name"])

                if len(repos_data) < 100:
                    break

                page += 1

            return all_repos
        else:
            return []

    def get_all_branches(self, repo: str) -> List[str]:
        """Get all branch names for a repository"""
        owner = self.config.github_org or self.config.github_owner
        url = f"{self.base_url}/repos/{owner}/{repo}/branches"

        all_branches = []
        page = 1
        per_page = 100

        while True:
            params = {"per_page": per_page, "page": page}
            branches_data = self._make_request(url, params)

            if not branches_data:
                break

            for branch in branches_data:
                all_branches.append(branch["name"])

            if len(branches_data) < per_page:
                break

            page += 1

        return all_branches

    def get_commits_for_period(self, days_back: int = 1) -> Dict[str, List[Dict]]:
        """Collect commits from ALL branches of all repositories for the specified period"""
        since_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        since_str = since_date.isoformat()

        all_commits = {}

        # Get repositories to process
        if self.config.repositories is None:
            # Fetch all repositories from organization/user
            repositories = self.get_org_repositories()
        else:
            repositories = self.config.repositories

        for repo in repositories:
            repo_commits = []

            # Get all branches for this repository
            branches = self.get_all_branches(repo)
            print(f"  Found {len(branches)} branches in {repo}: {', '.join(branches)}")

            # Collect commits from each branch
            for branch in branches:
                page = 1
                per_page = self.config.settings["github_api"]["per_page"]

                while True:
                    # Use github_org if available, otherwise use github_owner
                    owner = self.config.github_org or self.config.github_owner
                    url = f"{self.base_url}/repos/{owner}/{repo}/commits"
                    params = {
                        "sha": branch,  # Specify the branch
                        "since": since_str,
                        "per_page": per_page,
                        "page": page
                    }

                    try:
                        commits_data = self._make_request(url, params)
                    except Exception as e:
                        print(f"  Error fetching commits from branch {branch} in {repo}: {e}")
                        break

                    if not commits_data:
                        break

                    for commit in commits_data:
                        # Check if we already have this commit (avoid duplicates from merge commits)
                        if not any(existing["sha"] == commit["sha"] for existing in repo_commits):
                            commit_info = {
                                "sha": commit["sha"],
                                "message": commit["commit"]["message"],
                                "author": commit["author"]["login"] if commit["author"] else commit["commit"]["author"]["name"],
                                "date": commit["commit"]["author"]["date"],
                                "url": commit["html_url"],
                                "repository": repo,
                                "branch": branch
                            }
                            repo_commits.append(commit_info)

                    if len(commits_data) < per_page:
                        break

                    page += 1

            # Sort commits by date (newest first)
            repo_commits.sort(key=lambda x: x["date"], reverse=True)
            all_commits[repo] = repo_commits

        return all_commits

    def get_pull_requests_for_period(self, days_back: int = 1) -> Dict[str, List[Dict]]:
        """Collect pull requests and their comments for the specified period"""
        since_date = datetime.now(timezone.utc) - timedelta(days=days_back)

        all_prs = {}

        # Get repositories to process
        if self.config.repositories is None:
            repositories = self.get_org_repositories()
        else:
            repositories = self.config.repositories

        for repo in repositories:
            repo_prs = []

            # Get pull requests updated in the period
            owner = self.config.github_org or self.config.github_owner
            url = f"{self.base_url}/repos/{owner}/{repo}/pulls"
            params = {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": self.config.settings["github_api"]["per_page"]
            }

            prs_data = self._make_request(url, params)

            for pr in prs_data:
                pr_updated = datetime.fromisoformat(pr["updated_at"].replace('Z', '+00:00'))
                if pr_updated < since_date:
                    continue

                # Get PR comments
                comments = self._get_pr_comments(repo, pr["number"], since_date)

                # Check if PR was recently created (sent to review)
                pr_created = datetime.fromisoformat(pr["created_at"].replace('Z', '+00:00'))
                recently_created = pr_created >= since_date

                # Include PRs that either have recent comments OR were recently created (sent to review)
                if comments or recently_created:
                    pr_info = {
                        "number": pr["number"],
                        "title": pr["title"],
                        "url": pr["html_url"],
                        "state": pr["state"],
                        "author": pr["user"]["login"],
                        "created_at": pr["created_at"],
                        "updated_at": pr["updated_at"],
                        "comments": comments,
                        "repository": repo,
                        "recently_created": recently_created
                    }
                    repo_prs.append(pr_info)

            all_prs[repo] = repo_prs

        return all_prs

    def _get_pr_comments(self, repo: str, pr_number: int, since_date: datetime) -> List[Dict]:
        """Get comments for a specific pull request"""
        comments = []

        # Get issue comments (PR comments in issues API)
        owner = self.config.github_org or self.config.github_owner
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        comments_data = self._make_request(url)

        for comment in comments_data:
            comment_date = datetime.fromisoformat(comment["created_at"].replace('Z', '+00:00'))
            if comment_date >= since_date:
                comments.append({
                    "id": comment["id"],
                    "author": comment["user"]["login"],
                    "body": comment["body"],
                    "created_at": comment["created_at"],
                    "url": comment["html_url"],
                    "type": "issue_comment"
                })

        # Get review comments (code review comments)
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        review_comments_data = self._make_request(url)

        for comment in review_comments_data:
            comment_date = datetime.fromisoformat(comment["created_at"].replace('Z', '+00:00'))
            if comment_date >= since_date:
                comments.append({
                    "id": comment["id"],
                    "author": comment["user"]["login"],
                    "body": comment["body"],
                    "created_at": comment["created_at"],
                    "url": comment["html_url"],
                    "type": "review_comment",
                    "path": comment.get("path"),
                    "line": comment.get("line")
                })

        return sorted(comments, key=lambda x: x["created_at"])

    def get_project_issues_in_columns(self, project_number: int, columns: List[str]) -> List[Dict]:
        """Get issues from specific project columns using GraphQL"""
        if not project_number or not columns:
            return []

        query = """
        query($org: String!, $projectNumber: Int!, $first: Int, $after: String) {
            organization(login: $org) {
                projectV2(number: $projectNumber) {
                    items(first: $first, after: $after) {
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                        nodes {
                            id
                            content {
                                ... on Issue {
                                    number
                                    title
                                    url
                                    state
                                    author {
                                        login
                                    }
                                    createdAt
                                    updatedAt
                                    repository {
                                        name
                                    }
                                    labels(first: 10) {
                                        nodes {
                                            name
                                        }
                                    }
                                    assignees(first: 5) {
                                        nodes {
                                            login
                                        }
                                    }
                                }
                            }
                            fieldValues(first: 20) {
                                nodes {
                                    ... on ProjectV2ItemFieldSingleSelectValue {
                                        name
                                        field {
                                            ... on ProjectV2SingleSelectField {
                                                name
                                            }
                                        }
                                    }
                                    ... on ProjectV2ItemFieldTextValue {
                                        text
                                        field {
                                            ... on ProjectV2Field {
                                                name
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """

        variables = {
            "org": self.config.github_org,
            "projectNumber": project_number,
            "first": 100
        }

        project_issues = []
        has_next_page = True
        after = None

        while has_next_page:
            if after:
                variables["after"] = after

            try:
                result = self._make_graphql_request(query, variables)

                if not result.get("data", {}).get("organization", {}).get("projectV2"):
                    break

                items = result["data"]["organization"]["projectV2"]["items"]

                for item in items["nodes"]:
                    content = item.get("content")
                    if not content:
                        continue

                    # Check if item is in one of the specified columns
                    item_column = None
                    for field_value in item.get("fieldValues", {}).get("nodes", []):
                        field_name = field_value.get("field", {}).get("name", "")
                        if field_name == "Status":  # Typical column field name
                            item_column = field_value.get("name")
                            break

                    if item_column in columns:
                        issue_data = {
                            "number": content["number"],
                            "title": content["title"],
                            "url": content["url"],
                            "state": content["state"],
                            "author": content["author"]["login"] if content["author"] else "unknown",
                            "created_at": content["createdAt"],
                            "updated_at": content["updatedAt"],
                            "repository": content["repository"]["name"],
                            "labels": [label["name"] for label in content.get("labels", {}).get("nodes", [])],
                            "assignees": [assignee["login"] for assignee in content.get("assignees", {}).get("nodes", [])],
                            "project_column": item_column
                        }
                        project_issues.append(issue_data)

                page_info = items["pageInfo"]
                has_next_page = page_info["hasNextPage"]
                after = page_info["endCursor"]

            except Exception as e:
                print(f"Error fetching project issues: {e}")
                break

        return project_issues

    def get_parent_issue_via_rest_api(self, issue_url: str) -> Optional[Dict]:
        """
        Use REST API to get parent issue of a sub-issue directly.
        Returns parent issue data or None if no parent found.
        """
        try:
            # Extract owner, repo, and issue number from URL
            # URL format: https://github.com/owner/repo/issues/123
            parts = issue_url.replace("https://github.com/", "").split("/")
            if len(parts) >= 4 and parts[2] == "issues":
                owner, repo, _, issue_number = parts[0], parts[1], parts[2], parts[3]

                # Use GitHub REST API direct parent endpoint
                parent_url = f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}/parent"
                response = requests.get(parent_url, headers=self.config.headers)

                if response.status_code == 200:
                    return response.json()

        except Exception as e:
            print(f"Error getting parent issue via REST API: {e}")

        return None

    def find_case_parent(self, issue: Dict, max_depth: int = 10) -> Optional[Dict]:
        """
        Recursively find parent Issue with 'Case' label.
        If current issue is a Case itself, return it.
        Uses REST API to traverse up the parent hierarchy until Case is found.
        """
        if max_depth <= 0:
            return None

        # Check if this issue is a Case by type
        issue_type = issue.get("type", {}).get("name", "").lower() if issue.get("type") else None
        if issue_type == "case":
            return issue

        # Fallback: Check if this issue is a Case by label (backwards compatibility)
        labels = issue.get("labels", [])
        if isinstance(labels, dict) and "nodes" in labels:
            labels = labels["nodes"]

        # Handle both string labels and dict labels
        for label in labels:
            if isinstance(label, str):
                if label.lower() == "case":
                    return issue
            elif isinstance(label, dict):
                if label.get("name", "").lower() == "case":
                    return issue

        # Get parent issue using REST API
        issue_url = issue.get("url")
        if issue_url:
            parent_issue_data = self.get_parent_issue_via_rest_api(issue_url)
            if parent_issue_data:
                # Check if parent is a Case by type
                parent_type = parent_issue_data.get("type", {}).get("name", "").lower() if parent_issue_data.get("type") else None
                if parent_type == "case":
                    return {
                        "title": parent_issue_data["title"],
                        "url": parent_issue_data["html_url"],
                        "type": parent_issue_data.get("type"),
                        "labels": [{"name": label["name"]} for label in parent_issue_data.get("labels", [])]
                    }

                # Fallback: Check if parent is a Case by label
                parent_labels = parent_issue_data.get("labels", [])
                if any(label["name"].lower() == "case" for label in parent_labels):
                    return {
                        "title": parent_issue_data["title"],
                        "url": parent_issue_data["html_url"],
                        "type": parent_issue_data.get("type"),
                        "labels": [{"name": label["name"]} for label in parent_labels]
                    }

                # If parent is not a Case, recursively search its parent
                converted_parent = {
                    "title": parent_issue_data["title"],
                    "url": parent_issue_data["html_url"],
                    "type": parent_issue_data.get("type"),
                    "labels": [{"name": label["name"]} for label in parent_labels]
                }
                return self.find_case_parent(converted_parent, max_depth - 1)

        return None

    def get_issues_for_period(self, days_back: int = 1) -> Dict[str, List[Dict]]:
        """Collect issues and their comments for the specified period"""
        since_date = datetime.now(timezone.utc) - timedelta(days=days_back)

        all_issues = {}

        # If project filtering is configured, use project-based collection
        if self.config.project_number and self.config.columns:
            print(f"  Filtering by project {self.config.project_number} columns: {', '.join(self.config.columns)}")

            # Get issues from project columns
            project_issues = self.get_project_issues_in_columns(self.config.project_number, self.config.columns)

            # Also check incident project if configured
            if self.config.incident_project_number:
                incident_issues = self.get_project_issues_in_columns(self.config.incident_project_number, self.config.columns)
                project_issues.extend(incident_issues)

            # Group by repository and filter by date, then get comments
            for issue in project_issues:
                repo = issue["repository"]
                issue_updated = datetime.fromisoformat(issue["updated_at"].replace('Z', '+00:00'))

                if issue_updated >= since_date:
                    # Get comments for this issue
                    comments = self._get_issue_comments(repo, issue["number"], since_date)

                    if comments:  # Only include issues with recent comments
                        issue["comments"] = comments

                        if repo not in all_issues:
                            all_issues[repo] = []
                        all_issues[repo].append(issue)

            return all_issues

        # Fallback to original method if no project filtering
        # Get repositories to process
        if self.config.repositories is None:
            repositories = self.get_org_repositories()
        else:
            repositories = self.config.repositories

        for repo in repositories:
            repo_issues = []

            # Get issues updated in the period
            owner = self.config.github_org or self.config.github_owner
            url = f"{self.base_url}/repos/{owner}/{repo}/issues"
            params = {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": self.config.settings["github_api"]["per_page"]
            }

            issues_data = self._make_request(url, params)

            for issue in issues_data:
                # Skip pull requests (they appear in issues API too)
                if "pull_request" in issue:
                    continue

                issue_updated = datetime.fromisoformat(issue["updated_at"].replace('Z', '+00:00'))
                if issue_updated < since_date:
                    continue

                # Get issue comments
                comments = self._get_issue_comments(repo, issue["number"], since_date)

                if comments:  # Only include issues with recent comments
                    issue_info = {
                        "number": issue["number"],
                        "title": issue["title"],
                        "url": issue["html_url"],
                        "state": issue["state"],
                        "author": issue["user"]["login"],
                        "created_at": issue["created_at"],
                        "updated_at": issue["updated_at"],
                        "comments": comments,
                        "repository": repo,
                        "labels": [label["name"] for label in issue["labels"]],
                        "assignees": [assignee["login"] for assignee in issue.get("assignees", [])]
                    }
                    repo_issues.append(issue_info)

            all_issues[repo] = repo_issues

        return all_issues

    def _get_issue_comments(self, repo: str, issue_number: int, since_date: datetime) -> List[Dict]:
        """Get comments for a specific issue"""
        comments = []

        owner = self.config.github_org or self.config.github_owner
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}/comments"
        comments_data = self._make_request(url)

        for comment in comments_data:
            comment_date = datetime.fromisoformat(comment["created_at"].replace('Z', '+00:00'))
            if comment_date >= since_date:
                comments.append({
                    "id": comment["id"],
                    "author": comment["user"]["login"],
                    "body": comment["body"],
                    "created_at": comment["created_at"],
                    "url": comment["html_url"]
                })

        return sorted(comments, key=lambda x: x["created_at"])

    def organize_commits_by_author(self, commits_data: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        """Organize commits by author across all repositories"""
        commits_by_author = {}

        for repo, commits in commits_data.items():
            for commit in commits:
                author = commit["author"]
                if author not in commits_by_author:
                    commits_by_author[author] = []
                commits_by_author[author].append(commit)

        # Sort commits by date for each author
        for author in commits_by_author:
            commits_by_author[author].sort(key=lambda x: x["date"], reverse=True)

        return commits_by_author

    # === Methods for exact time period collection ===

    def get_commits_for_exact_period(self, start_time: datetime, end_time: datetime) -> Dict[str, List[Dict]]:
        """Collect commits from ALL branches of all repositories for the EXACT time period"""
        # Convert to UTC for API calls
        start_utc = start_time.astimezone(timezone.utc)
        end_utc = end_time.astimezone(timezone.utc)
        since_str = start_utc.isoformat()

        all_commits = {}

        # Get repositories to process
        if self.config.repositories is None:
            repositories = self.get_org_repositories()
        else:
            repositories = self.config.repositories

        for repo in repositories:
            repo_commits = []

            # Get all branches for this repository
            branches = self.get_all_branches(repo)
            print(f"  Found {len(branches)} branches in {repo}")

            # Collect commits from each branch
            for branch in branches:
                page = 1
                per_page = self.config.settings["github_api"]["per_page"]

                while True:
                    owner = self.config.github_org or self.config.github_owner
                    url = f"{self.base_url}/repos/{owner}/{repo}/commits"
                    params = {
                        "sha": branch,
                        "since": since_str,
                        "per_page": per_page,
                        "page": page
                    }

                    try:
                        commits_data = self._make_request(url, params)
                    except Exception as e:
                        print(f"  Error fetching commits from branch {branch} in {repo}: {e}")
                        break

                    if not commits_data:
                        break

                    for commit in commits_data:
                        commit_date = datetime.fromisoformat(commit["commit"]["author"]["date"].replace('Z', '+00:00'))

                        # EXACT filtering: only include if within start_time <= commit_date <= end_time
                        if start_utc <= commit_date <= end_utc:
                            # Check if we already have this commit (avoid duplicates from merge commits)
                            if not any(existing["sha"] == commit["sha"] for existing in repo_commits):
                                commit_info = {
                                    "sha": commit["sha"],
                                    "message": commit["commit"]["message"],
                                    "author": commit["author"]["login"] if commit["author"] else commit["commit"]["author"]["name"],
                                    "date": commit["commit"]["author"]["date"],
                                    "url": commit["html_url"],
                                    "repository": repo,
                                    "branch": branch
                                }
                                repo_commits.append(commit_info)

                    # Stop if we've gone past the end_time
                    if commits_data and all(
                        datetime.fromisoformat(c["commit"]["author"]["date"].replace('Z', '+00:00')) > end_utc
                        for c in commits_data
                    ):
                        break

                    if len(commits_data) < per_page:
                        break

                    page += 1

            # Sort commits by date (newest first)
            repo_commits.sort(key=lambda x: x["date"], reverse=True)
            all_commits[repo] = repo_commits

        return all_commits

    def get_pull_requests_for_exact_period(self, start_time: datetime, end_time: datetime) -> Dict[str, List[Dict]]:
        """Collect pull requests and their comments for the EXACT time period"""
        start_utc = start_time.astimezone(timezone.utc)
        end_utc = end_time.astimezone(timezone.utc)

        all_prs = {}

        # Get repositories to process
        if self.config.repositories is None:
            repositories = self.get_org_repositories()
        else:
            repositories = self.config.repositories

        for repo in repositories:
            repo_prs = []

            # Get pull requests
            owner = self.config.github_org or self.config.github_owner
            url = f"{self.base_url}/repos/{owner}/{repo}/pulls"
            params = {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": self.config.settings["github_api"]["per_page"]
            }

            prs_data = self._make_request(url, params)

            for pr in prs_data:
                pr_updated = datetime.fromisoformat(pr["updated_at"].replace('Z', '+00:00'))

                # Skip if updated before our time window
                if pr_updated < start_utc:
                    continue

                # Get PR comments (filtered by exact time range)
                comments = self._get_pr_comments_exact(repo, pr["number"], start_utc, end_utc)

                # Check if PR was created in the time window (sent to review)
                pr_created = datetime.fromisoformat(pr["created_at"].replace('Z', '+00:00'))
                recently_created = start_utc <= pr_created <= end_utc

                # Include PRs that either have comments in time range OR were created in time range
                if comments or recently_created:
                    pr_info = {
                        "number": pr["number"],
                        "title": pr["title"],
                        "url": pr["html_url"],
                        "state": pr["state"],
                        "author": pr["user"]["login"],
                        "created_at": pr["created_at"],
                        "updated_at": pr["updated_at"],
                        "comments": comments,
                        "repository": repo,
                        "recently_created": recently_created
                    }
                    repo_prs.append(pr_info)

            all_prs[repo] = repo_prs

        return all_prs

    def _get_pr_comments_exact(self, repo: str, pr_number: int, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Get comments for a specific pull request within exact time range"""
        comments = []

        # Get issue comments (PR comments in issues API)
        owner = self.config.github_org or self.config.github_owner
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        comments_data = self._make_request(url)

        for comment in comments_data:
            comment_date = datetime.fromisoformat(comment["created_at"].replace('Z', '+00:00'))
            if start_time <= comment_date <= end_time:
                comments.append({
                    "id": comment["id"],
                    "author": comment["user"]["login"],
                    "body": comment["body"],
                    "created_at": comment["created_at"],
                    "url": comment["html_url"],
                    "type": "issue_comment"
                })

        # Get review comments (code review comments)
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        review_comments_data = self._make_request(url)

        for comment in review_comments_data:
            comment_date = datetime.fromisoformat(comment["created_at"].replace('Z', '+00:00'))
            if start_time <= comment_date <= end_time:
                comments.append({
                    "id": comment["id"],
                    "author": comment["user"]["login"],
                    "body": comment["body"],
                    "created_at": comment["created_at"],
                    "url": comment["html_url"],
                    "type": "review_comment",
                    "path": comment.get("path"),
                    "line": comment.get("line")
                })

        return sorted(comments, key=lambda x: x["created_at"])

    def get_issues_for_exact_period(self, start_time: datetime, end_time: datetime) -> Dict[str, List[Dict]]:
        """Collect issues and their comments for the EXACT time period"""
        start_utc = start_time.astimezone(timezone.utc)
        end_utc = end_time.astimezone(timezone.utc)

        all_issues = {}

        # If project filtering is configured, use project-based collection
        if self.config.project_number and self.config.columns:
            print(f"  Filtering by project {self.config.project_number} columns: {', '.join(self.config.columns)}")

            # Get issues from project columns
            project_issues = self.get_project_issues_in_columns(self.config.project_number, self.config.columns)

            # Also check incident project if configured
            if self.config.incident_project_number:
                incident_issues = self.get_project_issues_in_columns(self.config.incident_project_number, self.config.columns)
                project_issues.extend(incident_issues)

            # Group by repository and filter by exact date range
            for issue in project_issues:
                repo = issue["repository"]
                issue_updated = datetime.fromisoformat(issue["updated_at"].replace('Z', '+00:00'))

                if issue_updated >= start_utc:
                    # Get comments for this issue (filtered by exact time range)
                    comments = self._get_issue_comments_exact(repo, issue["number"], start_utc, end_utc)

                    if comments:  # Only include issues with comments in time range
                        issue["comments"] = comments

                        if repo not in all_issues:
                            all_issues[repo] = []
                        all_issues[repo].append(issue)

            return all_issues

        # Fallback to original method if no project filtering
        if self.config.repositories is None:
            repositories = self.get_org_repositories()
        else:
            repositories = self.config.repositories

        for repo in repositories:
            repo_issues = []

            # Get issues
            owner = self.config.github_org or self.config.github_owner
            url = f"{self.base_url}/repos/{owner}/{repo}/issues"
            params = {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": self.config.settings["github_api"]["per_page"]
            }

            issues_data = self._make_request(url, params)

            for issue in issues_data:
                # Skip pull requests
                if "pull_request" in issue:
                    continue

                issue_updated = datetime.fromisoformat(issue["updated_at"].replace('Z', '+00:00'))
                if issue_updated < start_utc:
                    continue

                # Get issue comments (filtered by exact time range)
                comments = self._get_issue_comments_exact(repo, issue["number"], start_utc, end_utc)

                if comments:  # Only include issues with comments in time range
                    issue_info = {
                        "number": issue["number"],
                        "title": issue["title"],
                        "url": issue["html_url"],
                        "state": issue["state"],
                        "author": issue["user"]["login"],
                        "created_at": issue["created_at"],
                        "updated_at": issue["updated_at"],
                        "comments": comments,
                        "repository": repo,
                        "labels": [label["name"] for label in issue["labels"]],
                        "assignees": [assignee["login"] for assignee in issue.get("assignees", [])]
                    }
                    repo_issues.append(issue_info)

            all_issues[repo] = repo_issues

        return all_issues

    def _get_issue_comments_exact(self, repo: str, issue_number: int, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Get comments for a specific issue within exact time range"""
        comments = []

        owner = self.config.github_org or self.config.github_owner
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}/comments"
        comments_data = self._make_request(url)

        for comment in comments_data:
            comment_date = datetime.fromisoformat(comment["created_at"].replace('Z', '+00:00'))
            if start_time <= comment_date <= end_time:
                comments.append({
                    "id": comment["id"],
                    "author": comment["user"]["login"],
                    "body": comment["body"],
                    "created_at": comment["created_at"],
                    "url": comment["html_url"]
                })

        return sorted(comments, key=lambda x: x["created_at"])