import os
import sys

def resource_path(path):
    if hasattr(sys, '_MEIPASS'): 
        return os.path.join(sys._MEIPASS, path)
    return os.path.join(os.path.abspath("."), path)

import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv(resource_path(".env"))

GITHUB_TOKEN = os.getenv("MY_GH_TOKEN")
ORG = os.getenv("MY_GH_ORG")  # Organization name
PROJECT_NUMBER = int(os.getenv("MY_GH_PROJECT_NUMBER"))
INCIDENT_PROJECT_NUMBER = int(os.getenv("MY_GH_PROJECT_INCIDENT_NUMBER")) if os.getenv("MY_GH_PROJECT_INCIDENT_NUMBER") else None
COLUMN_NAMES = [name.strip() for name in os.getenv("MY_GH_COLUMNS").split(",")]
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "comments.json")

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json"
}

GRAPHQL_URL = "https://api.github.com/graphql"
YESTERDAY = datetime.now(timezone.utc) - timedelta(days=1)
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage" if TELEGRAM_BOT_TOKEN else None


def run_query(query, variables):
    response = requests.post(GRAPHQL_URL, headers=HEADERS, json={"query": query, "variables": variables})
    if response.status_code != 200:
        raise Exception(f"GraphQL query failed with code {response.status_code}: {response.text}")

    data = response.json()
    if "errors" in data:
        raise Exception(f"GraphQL error: {data['errors']}")
    return data


def get_project_id_by_number(project_number):
    """Get project ID by project number"""
    query = """
    query($org: String!, $number: Int!) {
      organization(login: $org) {
        projectV2(number: $number) {
          id
        }
      }
    }
    """
    variables = {"org": ORG, "number": project_number}
    data = run_query(query, variables)

    org_data = data.get("data", {}).get("organization")
    if not org_data:
        raise Exception(f"Organization '{ORG}' not found.")
    project = org_data.get("projectV2")
    if not project:
        raise Exception(f"Project number {project_number} not found in organization '{ORG}'.")
    return project["id"]


def get_project_id():
    return get_project_id_by_number(PROJECT_NUMBER)


def get_incidents(incident_project_id):
    """Get incidents from the incident project"""
    if not incident_project_id:
        return []
    
    incidents = []
    cursor = None
    has_next_page = True

    while has_next_page:
        query = """
        query($projectId: ID!, $cursor: String) {
          node(id: $projectId) {
            ... on ProjectV2 {
              items(first: 100, after: $cursor) {
                nodes {
                  content {
                    __typename
                    ... on Issue {
                      id
                      number
                      url
                      title
                      body
                      state
                      createdAt
                      updatedAt
                      labels(first: 100) {
                        nodes {
                          name
                        }
                      }
                      assignees(first: 100) {
                        nodes {
                          login
                        }
                      }
                      comments(last: 100) {
                        nodes {
                          body
                          createdAt
                          author {
                            login
                          }
                        }
                      }
                    }
                  }
                  fieldValues(first: 100) {
                    nodes {
                      ... on ProjectV2ItemFieldSingleSelectValue {
                        name
                      }
                      ... on ProjectV2ItemFieldTextValue {
                        text
                      }
                    }
                  }
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
        """
        variables = {"projectId": incident_project_id, "cursor": cursor}
        data = run_query(query, variables)
        items_data = data["data"]["node"]["items"]

        for item in items_data["nodes"]:
            content = item.get("content")
            if content and content.get("__typename") == "Issue":
                incidents.append(content)

        page = items_data["pageInfo"]
        has_next_page = page["hasNextPage"]
        cursor = page["endCursor"]

    return incidents


def get_items_with_status(project_id):
    items = []
    cursor = None
    has_next_page = True

    while has_next_page:
        query = """
        query($projectId: ID!, $cursor: String) {
          node(id: $projectId) {
            ... on ProjectV2 {
              items(first: 100, after: $cursor) {
                nodes {
                  content {
                    __typename
                    ... on Issue {
                      id
                      number
                      url
                      title
                      comments(last: 100) {
                        nodes {
                          body
                          createdAt
                          author {
                            login
                          }
                        }
                      }
                      issueTimeline: timelineItems(itemTypes: [CONNECTED_EVENT], last: 100) {
                        nodes {
                          ... on ConnectedEvent {
                            subject {
                              __typename
                              ... on Issue {
                                id
                                url
                                title
                                labels(first: 100) {
                                  nodes {
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
                  fieldValues(first: 100) {
                    nodes {
                      ... on ProjectV2ItemFieldSingleSelectValue {
                        name
                      }
                    }
                  }
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
        """
        variables = {"projectId": project_id, "cursor": cursor}
        data = run_query(query, variables)
        items_data = data["data"]["node"]["items"]

        for item in items_data["nodes"]:
            status = None
            for field in item["fieldValues"]["nodes"]:
                if field and "name" in field:
                    status = field["name"]
            content = item.get("content")
            
            # Debug: print item info
            if content and content.get("__typename") == "Issue":
                print(f"   Issue: {content['title']} | Status: {status}")
                
            if status in COLUMN_NAMES and content and content.get("__typename") == "Issue":
                items.append(content)
                print(f"     [OK] Added to results (status matches)")

        page = items_data["pageInfo"]
        has_next_page = page["hasNextPage"]
        cursor = page["endCursor"]

    return items


def get_all_org_prs():
    """
    Get all PRs from organization repositories
    """
    prs = []
    cursor = None
    has_next_page = True

    while has_next_page:
        query = """
        query($org: String!, $cursor: String) {
          organization(login: $org) {
            repositories(first: 100, after: $cursor) {
              nodes {
                pullRequests(states: [OPEN], last: 20) {
                  nodes {
                    id
                    number
                    url
                    title
                    body
                    state
                    isDraft
                    createdAt
                    updatedAt
                    reviewRequests(first: 10) {
                      nodes {
                        requestedReviewer {
                          __typename
                          ... on User {
                            login
                          }
                        }
                      }
                    }
                    reviews(last: 10) {
                      nodes {
                        body
                        state
                        createdAt
                        author {
                          login
                        }
                        comments(last: 5) {
                          nodes {
                            body
                            createdAt
                            author {
                              login
                            }
                          }
                        }
                      }
                    }
                    comments(last: 20) {
                      nodes {
                        body
                        createdAt
                        author {
                          login
                        }
                      }
                    }
                    timelineItems(itemTypes: [REVIEW_REQUESTED_EVENT], last: 10) {
                      nodes {
                        ... on ReviewRequestedEvent {
                          createdAt
                          requestedReviewer {
                            __typename
                            ... on User {
                              login
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
        """
        variables = {"org": ORG, "cursor": cursor}
        data = run_query(query, variables)
        
        org_data = data["data"]["organization"]
        repos = org_data["repositories"]["nodes"]
        
        for repo in repos:
            for pr in repo["pullRequests"]["nodes"]:
                prs.append(pr)

        page = org_data["repositories"]["pageInfo"]
        has_next_page = page["hasNextPage"]
        cursor = page["endCursor"]

    return prs


def get_parent_issue_via_rest_api(issue_url):
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
            parent_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/parent"
            response = requests.get(parent_url, headers=HEADERS)
            
            if response.status_code == 200:
                return response.json()
                                
    except Exception as e:
        print(f"Error getting parent issue via REST API: {e}")
        
    return None


def find_case_parent(issue):
    """
    Recursively find parent Issue with type 'Case'.
    If current issue is a Case itself, return it.
    Uses REST API to traverse up the parent hierarchy until Case is found.
    """
    # Check if this issue is a Case by type (first check type, fallback to labels for backwards compatibility)
    issue_type = issue.get("type", {}).get("name", "").lower() if issue.get("type") else None
    if issue_type == "case":
        return issue
    
    # Fallback: Check if this issue is a Case by label (backwards compatibility)
    labels = issue.get("labels", {}).get("nodes", [])
    if any(label["name"].lower() == "case" for label in labels):
        return issue

    # Get parent issue using REST API
    issue_url = issue.get("url")
    if issue_url:
        parent_issue_data = get_parent_issue_via_rest_api(issue_url)
        if parent_issue_data:
            # Check if parent is a Case by type
            parent_type = parent_issue_data.get("type", {}).get("name", "").lower() if parent_issue_data.get("type") else None
            if parent_type == "case":
                return {
                    "title": parent_issue_data["title"],
                    "url": parent_issue_data["html_url"],
                    "type": parent_issue_data.get("type"),
                    "labels": {"nodes": [{"name": label["name"]} for label in parent_issue_data.get("labels", [])]}
                }
            
            # Fallback: Check if parent is a Case by label
            parent_labels = parent_issue_data.get("labels", [])
            if any(label["name"].lower() == "case" for label in parent_labels):
                return {
                    "title": parent_issue_data["title"],
                    "url": parent_issue_data["html_url"],
                    "type": parent_issue_data.get("type"),
                    "labels": {"nodes": [{"name": label["name"]} for label in parent_labels]}
                }
            
            # If parent is not a Case, recursively search its parent
            converted_parent = {
                "title": parent_issue_data["title"],
                "url": parent_issue_data["html_url"],
                "type": parent_issue_data.get("type"),
                "labels": {"nodes": [{"name": label["name"]} for label in parent_labels]}
            }
            return find_case_parent(converted_parent)
    
    return None


def is_pr_sent_for_review_recently(pr):
    """
    Check if PR had REVIEW_REQUESTED_EVENT in the last 24 hours
    """
    print(f"[FETCH] Checking PR: {pr['title']} (#{pr['number']})")
    timeline = pr.get("timelineItems", {}).get("nodes", [])
    print(f"  Timeline events: {len(timeline)}")
    
    for event in timeline:
        created_at = datetime.fromisoformat(event["createdAt"].replace("Z", "+00:00"))
        print(f"  Event at: {created_at} (cutoff: {YESTERDAY})")
        if created_at > YESTERDAY:
            print(f"  [OK] Found recent review request!")
            return True
    
    print(f"  [ERROR] No recent review requests found")
    return False


def collect_recent_comments_and_prs(issues, all_prs, incidents=None):
    recent_comments = []
    recent_prs = []
    recent_incidents = []
    
    print(f"[RESULT] Analyzing {len(issues)} issues from project")
    
    # Process issues from project
    for item in issues:
        if item.get("__typename") == "Issue":
            case_parent = find_case_parent(item) or {"title": None, "url": None}
            for comment in item["comments"]["nodes"]:
                created_at = datetime.fromisoformat(comment["createdAt"].replace("Z", "+00:00"))
                if created_at > YESTERDAY:
                    recent_comments.append({
                        "type": "issue_comment",
                        "issue_url": item["url"],
                        "issue_title": item["title"],
                        "case_url": case_parent.get("url"),
                        "case_title": case_parent.get("title"),
                        "author": comment["author"]["login"] if comment["author"] else "unknown",
                        "created_at": comment["createdAt"],
                        "body": comment["body"]
                    })
    
    print(f"[RESULT] Analyzing {len(all_prs)} PRs from organization")
    
    # Process all PRs from organization
    for pr in all_prs:
        print(f"[FETCH] Found PR: {pr['title']} (#{pr['number']}) - State: {pr['state']}")
        # Check if PR was sent for review recently
        if is_pr_sent_for_review_recently(pr):
            # Get reviewers (only users, no teams)
            reviewers = []
            for req in pr.get("reviewRequests", {}).get("nodes", []):
                reviewer = req.get("requestedReviewer")
                if reviewer and reviewer.get("__typename") == "User":
                    reviewers.append(reviewer["login"])
            
            # Collect PR comments
            pr_comments = []
            for comment in pr["comments"]["nodes"]:
                created_at = datetime.fromisoformat(comment["createdAt"].replace("Z", "+00:00"))
                pr_comments.append({
                    "author": comment["author"]["login"] if comment["author"] else "unknown",
                    "created_at": comment["createdAt"],
                    "body": comment["body"],
                    "is_recent": created_at > YESTERDAY
                })
            
            # Collect review comments
            review_comments = []
            for review in pr.get("reviews", {}).get("nodes", []):
                if review["body"]:  # Only include reviews with body text
                    created_at = datetime.fromisoformat(review["createdAt"].replace("Z", "+00:00"))
                    review_comments.append({
                        "author": review["author"]["login"] if review["author"] else "unknown",
                        "created_at": review["createdAt"],
                        "state": review["state"],
                        "body": review["body"],
                        "is_recent": created_at > YESTERDAY
                    })
                
                # Include individual review comments
                for comment in review.get("comments", {}).get("nodes", []):
                    created_at = datetime.fromisoformat(comment["createdAt"].replace("Z", "+00:00"))
                    review_comments.append({
                        "author": comment["author"]["login"] if comment["author"] else "unknown",
                        "created_at": comment["createdAt"],
                        "state": "COMMENT",
                        "body": comment["body"],
                        "is_recent": created_at > YESTERDAY
                    })
            
            recent_prs.append({
                "type": "pull_request",
                "pr_url": pr["url"],
                "pr_title": pr["title"],
                "pr_number": pr["number"],
                "description": pr.get("body", ""),
                "state": pr["state"],
                "is_draft": pr["isDraft"],
                "created_at": pr["createdAt"],
                "updated_at": pr["updatedAt"],
                "reviewers": reviewers,
                "comments": pr_comments,
                "review_comments": review_comments
            })
    
    # Process incidents
    if incidents:
        print(f"[RESULT] Analyzing {len(incidents)} incidents")
        for incident in incidents:
            # Check for recent activity (comments or updates)
            has_recent_activity = False
            
            # Check for recent comments
            recent_comments_count = 0
            incident_comments = []
            for comment in incident["comments"]["nodes"]:
                created_at = datetime.fromisoformat(comment["createdAt"].replace("Z", "+00:00"))
                is_recent = created_at > YESTERDAY
                if is_recent:
                    recent_comments_count += 1
                    has_recent_activity = True
                incident_comments.append({
                    "author": comment["author"]["login"] if comment["author"] else "unknown",
                    "created_at": comment["createdAt"],
                    "body": comment["body"],
                    "is_recent": is_recent
                })
            
            # Check if incident was updated recently
            updated_at = datetime.fromisoformat(incident["updatedAt"].replace("Z", "+00:00"))
            if updated_at > YESTERDAY:
                has_recent_activity = True
            
            # Only include incidents with recent activity
            if has_recent_activity:
                labels = [label["name"] for label in incident.get("labels", {}).get("nodes", [])]
                assignees = [assignee["login"] for assignee in incident.get("assignees", {}).get("nodes", [])]
                
                recent_incidents.append({
                    "type": "incident",
                    "incident_url": incident["url"],
                    "incident_title": incident["title"],
                    "incident_number": incident["number"],
                    "state": incident["state"],
                    "body": incident.get("body", ""),
                    "created_at": incident["createdAt"],
                    "updated_at": incident["updatedAt"],
                    "labels": labels,
                    "assignees": assignees,
                    "comments": incident_comments,
                    "recent_comments_count": recent_comments_count
                })
    
    return recent_comments, recent_prs, recent_incidents


def format_telegram_message(comments, prs, incidents):
    """Format a concise report for Telegram"""
    lines = ["*GitHub Activity Report*"]
    lines.append(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    
    # Incidents summary
    if incidents:
        lines.append(f"[INCIDENT] *Incidents:* {len(incidents)} with recent activity")
        for incident in incidents:  # Show all incidents
            state_emoji = "[OPEN]" if incident['state'] == "OPEN" else "[CLOSED]"
            lines.append(f"  {state_emoji} [{incident['incident_title']}]({incident['incident_url']})")
            if incident['recent_comments_count'] > 0:
                lines.append(f"    {incident['recent_comments_count']} new comments")
        lines.append("")
    
    # PRs summary
    if prs:
        lines.append(f"*Pull Requests:* {len(prs)} sent for review")
        for pr in prs:  # Show all PRs
            draft_emoji = "[DRAFT]" if pr['is_draft'] else "[PR]"
            lines.append(f"  {draft_emoji} [{pr['pr_title']}]({pr['pr_url']})")
            if pr['reviewers']:
                lines.append(f"    Reviewers: {', '.join(pr['reviewers'])}")
        lines.append("")
    
    # Comments summary
    if comments:
        lines.append(f"*Issue Comments:* {len(comments)} new")
        lines.append("")
    
    if not incidents and not prs and not comments:
        lines.append("No recent activity in the last 24 hours")
    
    return "\n".join(lines)


def send_telegram_message(message):
    """Send message to Telegram bot"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram bot token or chat ID not configured, skipping notification")
        return False
    
    try:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        
        response = requests.post(TELEGRAM_API_URL, json=payload)
        
        if response.status_code == 200:
            print("[OK] Report sent to Telegram successfully")
            return True
        else:
            print(f"[ERROR] Failed to send Telegram message: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[ERROR] Error sending Telegram message: {str(e)}")
        return False


def send_telegram_file(file_path, caption=None):
    """Send file to Telegram bot"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram bot token or chat ID not configured, skipping file upload")
        return False
    
    try:
        telegram_file_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        
        with open(file_path, 'rb') as file:
            files = {'document': file}
            data = {
                'chat_id': TELEGRAM_CHAT_ID,
            }
            if caption:
                data['caption'] = caption
                data['parse_mode'] = 'Markdown'
            
            response = requests.post(telegram_file_url, files=files, data=data)
        
        if response.status_code == 200:
            print(f"[OK] File {file_path} sent to Telegram successfully")
            return True
        else:
            print(f"[ERROR] Failed to send Telegram file: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[ERROR] Error sending Telegram file: {str(e)}")
        return False


def save_to_md(comments, prs, incidents, output_file):
    lines = ["# GitHub Activity Report - Last 24 Hours\n"]

    # Incidents section
    lines.append("## Incidents\n")
    if not incidents:
        lines.append("No incidents with recent activity in the last 24 hours.\n")
    else:
        for incident in incidents:
            lines.append(f"### [{incident['incident_title']}]({incident['incident_url']}) (#{incident['incident_number']})")
            lines.append(f"- **State:** {incident['state']}")
            lines.append(f"- **Created:** {incident['created_at']}")
            lines.append(f"- **Updated:** {incident['updated_at']}")
            if incident['assignees']:
                lines.append(f"- **Assignees:** {', '.join(incident['assignees'])}")
            if incident['labels']:
                lines.append(f"- **Labels:** {', '.join(incident['labels'])}")
            lines.append(f"- **Recent Comments:** {incident['recent_comments_count']}")
            
            if incident['body']:
                lines.append("\n**Description:**")
                description = incident['body'].strip().replace("\r\n", "\n").replace("\n", "\n> ")
                lines.append(f"> {description}")
            
            if incident['comments']:
                lines.append("\n**Recent Comments:**")
                for comment in incident['comments']:
                    if comment['is_recent']:
                        lines.append(f"- **{comment['author']}** ({comment['created_at']}) 🆕")
                        comment_body = comment['body'].strip().replace("\r\n", "\n").replace("\n", "\n  > ")
                        lines.append(f"  > {comment_body}")
            
            lines.append("\n---\n")

    # Pull Requests section
    lines.append("## Pull Requests Sent for Review\n")
    if not prs:
        lines.append("No pull requests sent for review in the last 24 hours.\n")
    else:
        for pr in prs:
            lines.append(f"### [{pr['pr_title']}]({pr['pr_url']}) (#{pr['pr_number']})")
            lines.append(f"- **State:** {pr['state']}")
            lines.append(f"- **Draft:** {'Yes' if pr['is_draft'] else 'No'}")
            lines.append(f"- **Created:** {pr['created_at']}")
            lines.append(f"- **Updated:** {pr['updated_at']}")
            if pr['reviewers']:
                lines.append(f"- **Reviewers:** {', '.join(pr['reviewers'])}")
            else:
                lines.append("- **Reviewers:** None assigned")
            
            if pr['description']:
                lines.append("\n**Description:**")
                description = pr['description'].strip().replace("\r\n", "\n").replace("\n", "\n> ")
                lines.append(f"> {description}")
            
            # PR Comments
            if pr['comments']:
                lines.append("\n**Comments:**")
                for comment in pr['comments']:
                    recent_marker = " 🆕" if comment['is_recent'] else ""
                    lines.append(f"- **{comment['author']}** ({comment['created_at']}){recent_marker}")
                    comment_body = comment['body'].strip().replace("\r\n", "\n").replace("\n", "\n  > ")
                    lines.append(f"  > {comment_body}")
            
            # Review Comments
            if pr['review_comments']:
                lines.append("\n**Review Comments:**")
                for comment in pr['review_comments']:
                    recent_marker = " 🆕" if comment['is_recent'] else ""
                    state_info = f" [{comment['state']}]" if comment['state'] != 'COMMENT' else ""
                    lines.append(f"- **{comment['author']}**{state_info} ({comment['created_at']}){recent_marker}")
                    comment_body = comment['body'].strip().replace("\r\n", "\n").replace("\n", "\n  > ")
                    lines.append(f"  > {comment_body}")
            
            lines.append("\n---\n")

    # Comments section
    lines.append("## Recent Issue Comments\n")
    if not comments:
        lines.append("No issue comments found in the last 24 hours.")
    else:
        for c in comments:
            lines.append(f"### Issue: [{c['issue_title']}]({c['issue_url']})")
            if c['case_title'] and c['case_url']:
                lines.append(f"**Case:** [{c['case_title']}]({c['case_url']})")
            else:
                lines.append("**Case:** None")
            lines.append(f"- **Author:** {c['author']}")
            lines.append(f"- **Date:** {c['created_at']}")
            lines.append("")
            comment_body = c['body'].strip().replace("\r\n", "\n").replace("\n", "\n> ")
            lines.append(f"> {comment_body}")
            lines.append("\n---\n")

    md_content = "\n".join(lines)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md_content)


def main():
    print(f"[CONFIG] Configuration:")
    print(f"   GitHub Org: {ORG}")
    print(f"   Project Number: {PROJECT_NUMBER}")
    print(f"   Incident Project: {INCIDENT_PROJECT_NUMBER}")
    print(f"   Column Names: {COLUMN_NAMES}")
    print(f"   Telegram Bot Token: {'SET' if TELEGRAM_BOT_TOKEN else 'MISSING'}")
    print(f"   Telegram Chat ID: {'SET' if TELEGRAM_CHAT_ID else 'MISSING'}")
    print()
    
    print("[FETCH] Fetching Project ID...")
    project_id = get_project_id()
    print(f"   Project ID: {project_id}")
    print("[LOAD] Loading issues with statuses:", COLUMN_NAMES)
    issues = get_items_with_status(project_id)
    print(f"[FOUND] Found {len(issues)} issues in project")

    print("[GET] Loading all PRs from organization...")
    all_prs = get_all_org_prs()
    print(f"[FOUND] Found {len(all_prs)} PRs in organization")

    # Load incidents if project number is configured
    incidents = []
    if INCIDENT_PROJECT_NUMBER:
        print(f"[INCIDENT] Loading incidents from project {INCIDENT_PROJECT_NUMBER}...")
        try:
            incident_project_id = get_project_id_by_number(INCIDENT_PROJECT_NUMBER)
            print(f"   Incident Project ID: {incident_project_id}")
            incidents = get_incidents(incident_project_id)
            print(f"[FOUND] Found {len(incidents)} incidents")
        except Exception as e:
            print(f"[ERROR] Error loading incidents: {e}")
    else:
        print("[WARN] No incident project configured")

    print("[COLLECT] Collecting comments, PRs, and incidents from the last 24 hours...")
    print(f"   Cutoff time (yesterday): {YESTERDAY}")
    comments, prs, recent_incidents = collect_recent_comments_and_prs(issues, all_prs, incidents)
    
    print(f"[RESULT] Final Results:")
    print(f"   Recent comments: {len(comments)}")
    print(f"   Recent PRs: {len(prs)}")
    print(f"   Recent incidents: {len(recent_incidents)}")
    
    if len(comments) == 0 and len(prs) == 0 and len(recent_incidents) == 0:
        print("[WARN] No recent activity found - this might indicate:")
        print("   - No activity in the last 24 hours")
        print("   - Issues not in the configured columns")
        print("   - Organization/project configuration issues")
        print("   - API permission issues")

    # Combine all data for JSON output
    all_data = {
        "recent_comments": comments,
        "recent_pull_requests": prs,
        "recent_incidents": recent_incidents,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

    # Save JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"[OK] Saved {len(comments)} comments, {len(prs)} PRs, and {len(recent_incidents)} incidents to {OUTPUT_FILE}")

    # Save MD
    md_output_file = OUTPUT_FILE.rsplit(".", 1)[0] + ".md"
    save_to_md(comments, prs, recent_incidents, md_output_file)
    print(f"[OK] Saved report to {md_output_file}")

    # Send Telegram notification
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Sending report to Telegram...")
        print(f"   Bot token: {TELEGRAM_BOT_TOKEN[:10]}...")
        print(f"   Chat ID: {TELEGRAM_CHAT_ID}")
        
        # Send summary message
        telegram_message = format_telegram_message(comments, prs, recent_incidents)
        print(f"   Message length: {len(telegram_message)} characters")
        message_success = send_telegram_message(telegram_message)
        
        # Send MD file
        print(f"   Sending MD file: {md_output_file}")
        file_caption = f"GitHub Activity Report - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        file_success = send_telegram_file(md_output_file, file_caption)
        
        if not message_success and not file_success:
            print("[ERROR] Failed to send both Telegram message and file")
        elif not message_success:
            print("[ERROR] Failed to send Telegram message but file sent successfully")
        elif not file_success:
            print("[ERROR] Failed to send Telegram file but message sent successfully")
        else:
            print("[OK] Both Telegram message and file sent successfully")
    else:
        print("[WARN] Telegram configuration missing, skipping notification")
        print(f"   Bot token present: {bool(TELEGRAM_BOT_TOKEN)}")
        print(f"   Chat ID present: {bool(TELEGRAM_CHAT_ID)}")


if __name__ == "__main__":
    main()
