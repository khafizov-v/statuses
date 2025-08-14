import os

def resource_path(path):
    if hasattr(sys, '_MEIPASS'): 
        return os.path.join(sys._MEIPASS, path)
    return os.path.join(os.path.abspath("."), path)

import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv(resource_path(".env"))

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
ORG = os.getenv("GITHUB_ORG")  # Organization name
PROJECT_NUMBER = int(os.getenv("GITHUB_PROJECT_NUMBER"))
COLUMN_NAMES = [name.strip() for name in os.getenv("GITHUB_COLUMNS").split(",")]
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "comments.json")

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json"
}

GRAPHQL_URL = "https://api.github.com/graphql"
YESTERDAY = datetime.now(timezone.utc) - timedelta(days=1)


def run_query(query, variables):
    response = requests.post(GRAPHQL_URL, headers=HEADERS, json={"query": query, "variables": variables})
    if response.status_code != 200:
        raise Exception(f"GraphQL query failed with code {response.status_code}: {response.text}")

    data = response.json()
    if "errors" in data:
        raise Exception(f"GraphQL error: {data['errors']}")
    return data


def get_project_id():
    query = """
    query($org: String!, $number: Int!) {
      organization(login: $org) {
        projectV2(number: $number) {
          id
        }
      }
    }
    """
    variables = {"org": ORG, "number": PROJECT_NUMBER}
    data = run_query(query, variables)

    org_data = data.get("data", {}).get("organization")
    if not org_data:
        raise Exception(f"Organization '{ORG}' not found.")
    project = org_data.get("projectV2")
    if not project:
        raise Exception(f"Project number {PROJECT_NUMBER} not found in organization '{ORG}'.")
    return project["id"]


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
                      comments(last: 20) {
                        nodes {
                          body
                          createdAt
                          author {
                            login
                          }
                        }
                      }
                      issueTimeline: timelineItems(itemTypes: [CONNECTED_EVENT], last: 10) {
                        nodes {
                          ... on ConnectedEvent {
                            subject {
                              __typename
                              ... on Issue {
                                id
                                url
                                title
                                labels(first: 10) {
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
                  fieldValues(first: 10) {
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
            if status in COLUMN_NAMES and content and content.get("__typename") == "Issue":
                items.append(content)

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
            repositories(first: 50, after: $cursor) {
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
                    reviews(last: 20) {
                      nodes {
                        body
                        state
                        createdAt
                        author {
                          login
                        }
                        comments(last: 10) {
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


def find_case_parent(issue):
    """
    Recursively find parent Issue labeled 'Case'.
    If current issue is a Case itself, return it.
    """
    # Check if this issue is a Case by label
    labels = issue.get("labels", {}).get("nodes", [])
    if any(label["name"].lower() == "case" for label in labels):
        return issue

    # Try to find a parent from timeline connected events
    timeline = issue.get("issueTimeline", {}).get("nodes", [])
    for event in timeline:
        parent = event.get("subject")
        if parent and parent.get("__typename") == "Issue":
            parent_case = find_case_parent(parent)
            if parent_case:
                return parent_case
    return None


def is_pr_sent_for_review_recently(pr):
    """
    Check if PR had REVIEW_REQUESTED_EVENT in the last 24 hours
    """
    print(f"🔍 Checking PR: {pr['title']} (#{pr['number']})")
    timeline = pr.get("timelineItems", {}).get("nodes", [])
    print(f"  Timeline events: {len(timeline)}")
    
    for event in timeline:
        created_at = datetime.fromisoformat(event["createdAt"].replace("Z", "+00:00"))
        print(f"  Event at: {created_at} (cutoff: {YESTERDAY})")
        if created_at > YESTERDAY:
            print(f"  ✅ Found recent review request!")
            return True
    
    print(f"  ❌ No recent review requests found")
    return False


def collect_recent_comments_and_prs(issues, all_prs):
    recent_comments = []
    recent_prs = []
    
    print(f"📊 Analyzing {len(issues)} issues from project")
    
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
    
    print(f"📊 Analyzing {len(all_prs)} PRs from organization")
    
    # Process all PRs from organization
    for pr in all_prs:
        print(f"🔍 Found PR: {pr['title']} (#{pr['number']}) - State: {pr['state']}")
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
    
    return recent_comments, recent_prs


def save_to_md(comments, prs, output_file):
    lines = ["# GitHub Activity Report - Last 24 Hours\n"]

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
    print("🔍 Fetching Project ID...")
    project_id = get_project_id()
    print("📦 Loading issues with statuses:", COLUMN_NAMES)
    issues = get_items_with_status(project_id)
    print(f"🔎 Found {len(issues)} issues in project")

    print("🚀 Loading all PRs from organization...")
    all_prs = get_all_org_prs()
    print(f"🔎 Found {len(all_prs)} PRs in organization")

    print("🗨️ Collecting comments and PRs from the last 24 hours...")
    comments, prs = collect_recent_comments_and_prs(issues, all_prs)

    # Combine all data for JSON output
    all_data = {
        "recent_comments": comments,
        "recent_pull_requests": prs,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

    # Save JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"✅ Saved {len(comments)} comments and {len(prs)} PRs to {OUTPUT_FILE}")

    # Save MD
    md_output_file = OUTPUT_FILE.rsplit(".", 1)[0] + ".md"
    save_to_md(comments, prs, md_output_file)
    print(f"✅ Saved report to {md_output_file}")


if __name__ == "__main__":
    main()
