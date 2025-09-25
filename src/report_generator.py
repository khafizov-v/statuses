from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any
import re

class ReportGenerator:
    def __init__(self, config):
        self.config = config
        self.template_path = Path(__file__).parent.parent / "templates" / "status_template.md"

        # Username to real name mapping
        self.username_mapping = {
            "zotho": "Svyatoslav",
            "akablockchain2": "Alexey",
            "khssnv": "Alisher",
            "Vsevolod-Rusinskiy": "Vsevolod",
            "statictype": "Andreea"
        }

    def load_template(self) -> str:
        """Load the status report template"""
        with open(self.template_path, 'r', encoding='utf-8') as f:
            return f.read()

    def generate_report(self, commits_data: Dict, prs_data: Dict, issues_data: Dict, report_date: str = None) -> str:
        """Generate a status report from collected data"""
        if report_date is None:
            report_date = datetime.now().strftime(self.config.settings["report_format"]["date_format"])

        # Organize commits by author
        commits_by_author = self._organize_commits_by_author(commits_data)

        # Generate report sections
        commits_section = self._generate_commits_section(commits_by_author)
        prs_section = self._generate_prs_section(prs_data)
        issues_section = self._generate_issues_section(issues_data)

        # Build the complete report
        report = f"# Project Status Report — {report_date}\n\n"

        # Add commits section
        total_commits = sum(len(commits) for commits in commits_by_author.values())
        report += f"## Commits: {total_commits}\n"
        report += commits_section + "\n\n"
        report += "---\n\n"

        # Add PR section if there are PRs and it's enabled
        if prs_section and self.config.settings["report_format"]["include_pr_section"]:
            report += "## Pull Requests\n\n"
            report += prs_section
            report += "---\n\n"

        # Add issues section if there are issues and it's enabled
        if issues_section and self.config.settings["report_format"]["include_case_sections"]:
            report += issues_section

        return report

    def _organize_commits_by_author(self, commits_data: Dict) -> Dict[str, List[Dict]]:
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

    def _generate_commits_section(self, commits_by_author: Dict[str, List[Dict]]) -> str:
        """Generate the commits section of the report"""
        if not commits_by_author:
            return "No commits found for the specified period.\n"

        lines = []
        max_commits_shown = self.config.settings["report_format"]["max_commits_shown"]

        for author, commits in commits_by_author.items():
            # Map author to real name
            author_real_name = self.username_mapping.get(author, author)

            # Group commits by repository
            repos_commits = {}
            for commit in commits:
                repo = commit["repository"]
                if repo not in repos_commits:
                    repos_commits[repo] = []
                repos_commits[repo].append(commit)

            # Format repository names
            repo_names = ", ".join(repos_commits.keys())

            # Format commit links as simple numbered hyperlinks
            commit_links = []
            commit_count = 0
            for repo, repo_commits in repos_commits.items():
                for commit in repo_commits:
                    if commit_count < max_commits_shown:
                        commit_links.append(f"[{commit_count + 1}]({commit['url']})")
                        commit_count += 1
                    else:
                        break
                if commit_count >= max_commits_shown:
                    break

            if len(commits) > max_commits_shown:
                commit_links.append("...")

            links_str = ", ".join(commit_links)
            lines.append(f"**{author_real_name}:** {len(commits)} {repo_names} ({links_str})")

        return "\n".join(lines)

    def _generate_prs_section(self, prs_data: Dict) -> str:
        """Generate the pull requests section of the report"""
        if not prs_data or not any(prs_data.values()):
            return ""

        sections = []

        for repo, prs in prs_data.items():
            for pr in prs:
                section = f"### [{pr['title']}]({pr['url']})\n"

                # If PR was recently created but has no comments, mention it was sent to review
                if pr.get("recently_created", False) and not pr["comments"]:
                    author = pr["author"]
                    author_real_name = self.username_mapping.get(author, author)
                    section += f"**{author_real_name}:** Sent PR to review\n\n"
                elif pr["comments"]:
                    # Add PR comments
                    for comment in pr["comments"]:
                        # Format comment with author
                        comment_text = self._format_comment(comment["body"])
                        author = comment["author"]
                        author_real_name = self.username_mapping.get(author, author)
                        section += f"**{author_real_name}:** {comment_text}\n\n"
                else:
                    # Skip PRs that have neither recent creation nor comments
                    continue

                sections.append(section)

        return "\n".join(sections)

    def _generate_issues_section(self, issues_data: Dict) -> str:
        """Generate the issues section of the report"""
        if not issues_data or not any(issues_data.values()):
            return ""

        # Group issues by case/project using parent case detection
        cases = {}

        for repo, issues in issues_data.items():
            for issue in issues:
                if not issue["comments"]:
                    continue

                # First try to find parent case using the collector's logic
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).parent))
                from github_collector import GitHubCollector
                collector = GitHubCollector(self.config)
                parent_case = collector.find_case_parent(issue)

                if parent_case:
                    case_name = parent_case["title"]
                    case_url = parent_case["url"]
                    case_key = f"[{case_name}]({case_url})"

                    if case_key not in cases:
                        cases[case_key] = []
                    cases[case_key].append(issue)
                # If no parent case found, don't include this issue in any case section

        # Generate sections for each case
        sections = []
        for case_key, case_issues in cases.items():
            # Case key is either "[Case Name](url)" or just repo name
            if case_key.startswith("[") and "](" in case_key:
                section = f"## Case: {case_key}\n\n"
            else:
                section = f"## Case: {case_key}\n\n"

            for issue in case_issues:
                # Get assignee names
                assignee_names = []
                for assignee_username in issue.get("assignees", []):
                    real_name = self.username_mapping.get(assignee_username, assignee_username)
                    assignee_names.append(real_name)

                # Format title with assignee names
                if assignee_names:
                    assignee_str = f" ({', '.join(assignee_names)})"
                else:
                    assignee_str = ""

                section += f"### [{issue['title']}]({issue['url']}){assignee_str}\n"

                # Add issue comments
                for comment in issue["comments"]:
                    comment_text = self._format_comment(comment["body"])
                    author = comment["author"]
                    # Map comment author to real name too
                    author_real_name = self.username_mapping.get(author, author)
                    section += f"**{author_real_name}:** {comment_text}\n\n"

            sections.append(section + "---\n")

        return "\n".join(sections)

    def _format_comment(self, comment_body: str) -> str:
        """Format comment body for inclusion in report"""
        # Remove excessive whitespace
        comment = re.sub(r'\n\s*\n', '\n\n', comment_body.strip())

        # Truncate very long comments
        max_length = 500
        if len(comment) > max_length:
            comment = comment[:max_length] + "..."

        return comment

    def save_report(self, report_content: str, filename: str = None) -> str:
        """Save the generated report to a file"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.config.report_filename_prefix}_{timestamp}.md"

        output_dir = Path(__file__).parent.parent / self.config.output_directory
        output_dir.mkdir(exist_ok=True)

        output_path = output_dir / filename

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report_content)

        return str(output_path)

    def send_to_telegram(self, report_content: str, file_path: str = None) -> bool:
        """Send report to Telegram if configured"""
        if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
            return False

        try:
            # Always send as file if file_path is provided
            if file_path:
                return self._send_file_to_telegram(file_path)
            else:
                # If no file path provided, create a temporary file
                import tempfile
                import os

                with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as temp_file:
                    temp_file.write(report_content)
                    temp_file_path = temp_file.name

                try:
                    result = self._send_file_to_telegram(temp_file_path)
                finally:
                    # Clean up temporary file
                    os.unlink(temp_file_path)

                return result

        except Exception as e:
            print(f"Failed to send to Telegram: {e}")
            return False

    def _send_file_to_telegram(self, file_path: str) -> bool:
        """Send file to Telegram"""
        try:
            import requests

            url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendDocument"

            with open(file_path, 'rb') as file:
                files = {'document': file}
                data = {'chat_id': self.config.telegram_chat_id}

                response = requests.post(url, data=data, files=files)
                response.raise_for_status()
                return True

        except Exception as e:
            print(f"Failed to send file to Telegram: {e}")
            return False

    def send_to_zulip(self, report_content: str) -> bool:
        """Send report to Zulip as text message (never truncated)"""
        # Debug: Print configuration status
        print(f"Zulip config check:")
        print(f"  Email: {'✓' if self.config.zulip_email else '✗'} {self.config.zulip_email}")
        print(f"  Key: {'✓' if self.config.zulip_key else '✗'} {'***' if self.config.zulip_key else 'None'}")
        print(f"  Stream: {'✓' if self.config.zulip_stream else '✗'} {self.config.zulip_stream}")
        print(f"  Topic: {'✓' if self.config.zulip_topic else '✗'} {self.config.zulip_topic}")

        if not all([self.config.zulip_email, self.config.zulip_key, self.config.zulip_stream, self.config.zulip_topic]):
            print("Zulip configuration incomplete")
            return False

        try:
            import requests
            import base64

            # Zulip requires basic auth with email:key encoded in base64
            credentials = f"{self.config.zulip_email}:{self.config.zulip_key}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()

            # Extract site URL from email domain
            domain = self.config.zulip_email.split('@')[1]
            site_url = f"https://{domain}"
            url = f"{site_url}/api/v1/messages"

            print(f"Sending to Zulip URL: {url}")
            print(f"Stream: {self.config.zulip_stream}, Topic: {self.config.zulip_topic}")

            headers = {
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded"
            }

            data = {
                "type": "stream",
                "to": self.config.zulip_stream,
                "subject": self.config.zulip_topic,
                "content": report_content
            }

            response = requests.post(url, headers=headers, data=data)
            print(f"Zulip response status: {response.status_code}")
            print(f"Zulip response: {response.text}")
            response.raise_for_status()

            return True

        except Exception as e:
            print(f"Failed to send to Zulip: {e}")
            import traceback
            traceback.print_exc()
            return False