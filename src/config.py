import os
import json
from dotenv import load_dotenv
from pathlib import Path

class Config:
    def __init__(self):
        # Load environment variables from .env file if it exists
        config_dir = Path(__file__).parent.parent / "config"
        env_file = config_dir / ".env"
        if env_file.exists():
            load_dotenv(env_file)
        # If no .env file, environment variables should be set directly (like in GitHub Actions)

        # Load settings from JSON
        settings_path = config_dir / "settings.json"
        with open(settings_path, 'r') as f:
            self.settings = json.load(f)

        # GitHub configuration - updated to match your .env file
        self.github_token = os.getenv("MY_GH_TOKEN")
        self.github_org = os.getenv("MY_GH_ORG")
        self.github_owner = os.getenv("MY_GH_OWNER")

        # Support both single repo, multiple repos, or all org repos
        single_repo = os.getenv("MY_GH_REPO")
        multiple_repos = os.getenv("REPOSITORIES")

        # If we have an organization, we'll fetch all repos from it
        # This overrides individual repo settings
        if self.github_org:
            self.repositories = None  # Will be populated dynamically
        elif single_repo:
            self.repositories = [single_repo]
        elif multiple_repos:
            self.repositories = [repo.strip() for repo in multiple_repos.split(",") if repo.strip()]
        else:
            self.repositories = []

        # Team configuration
        self.team_members = [member.strip() for member in os.getenv("TEAM_MEMBERS", "").split(",") if member.strip()]

        # GitHub Projects configuration
        self.project_number = int(os.getenv("MY_GH_PROJECT_NUMBER")) if os.getenv("MY_GH_PROJECT_NUMBER") else None
        self.incident_project_number = int(os.getenv("MY_GH_PROJECT_INCIDENT_NUMBER")) if os.getenv("MY_GH_PROJECT_INCIDENT_NUMBER") else None
        self.columns = [col.strip() for col in os.getenv("MY_GH_COLUMNS", "").split(",") if col.strip()]

        # Report configuration
        self.report_days_back = int(os.getenv("REPORT_DAYS_BACK", "1"))
        self.output_directory = os.getenv("OUTPUT_DIRECTORY", "output")
        self.report_filename_prefix = os.getenv("REPORT_FILENAME_PREFIX", "status_report")

        # Optional Telegram configuration
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        # Validate required configuration
        self._validate_config()

    def _validate_config(self):
        """Validate that required configuration is present"""
        if not self.github_token:
            raise ValueError("MY_GH_TOKEN is required")
        if not (self.github_org or self.github_owner):
            raise ValueError("MY_GH_ORG or MY_GH_OWNER is required")
        # Don't validate repositories here as they may be fetched dynamically from org

    @property
    def headers(self):
        """GitHub API headers"""
        return {
            "Authorization": f"Bearer {self.github_token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json"
        }

    @property
    def graphql_headers(self):
        """GitHub GraphQL API headers"""
        return {
            "Authorization": f"Bearer {self.github_token}",
            "Content-Type": "application/json"
        }