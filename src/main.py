#!/usr/bin/env python3

import sys
import argparse
from pathlib import Path
from datetime import datetime
import pytz

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from github_collector import GitHubCollector
from report_generator import ReportGenerator

def main():
    parser = argparse.ArgumentParser(description="Generate status reports from GitHub activity")
    parser.add_argument("--days", type=int, default=None,
                       help="Number of days back to collect data (default: auto-detect based on weekday)")
    parser.add_argument("--start-time", type=str,
                       help="Start time in format 'YYYY-MM-DD HH:MM' (Moscow time)")
    parser.add_argument("--end-time", type=str,
                       help="End time in format 'YYYY-MM-DD HH:MM' (Moscow time)")
    parser.add_argument("--output", type=str,
                       help="Output filename (default: auto-generated)")
    parser.add_argument("--telegram", action="store_true",
                       help="Send report to Telegram")
    parser.add_argument("--zulip", action="store_true",
                       help="Send report to Zulip")
    parser.add_argument("--dry-run", action="store_true",
                       help="Print report to console without saving")

    args = parser.parse_args()

    # Parse exact time range if provided
    start_time = None
    end_time = None
    moscow_tz = pytz.timezone('Europe/Moscow')

    if args.start_time and args.end_time:
        try:
            # Parse times in Moscow timezone
            start_time = moscow_tz.localize(datetime.strptime(args.start_time, '%Y-%m-%d %H:%M'))
            end_time = moscow_tz.localize(datetime.strptime(args.end_time, '%Y-%m-%d %H:%M'))
            print(f"Using exact time range: {start_time} to {end_time} (Moscow time)")
        except ValueError as e:
            print(f"Error parsing time: {e}")
            print("Use format: 'YYYY-MM-DD HH:MM'")
            sys.exit(1)
    elif args.days is None:
        # Auto-detect days based on weekday if not specified
        today = datetime.now()
        weekday = today.weekday()  # Monday=0, Sunday=6
        if weekday == 0:  # Monday
            args.days = 3  # Collect Saturday, Sunday, Monday
        else:
            args.days = 1  # Regular single day

    try:
        # Initialize configuration
        print("Loading configuration...")
        config = Config()

        # Initialize collector and generator
        print("Initializing GitHub collector...")
        collector = GitHubCollector(config)

        print("Initializing report generator...")
        generator = ReportGenerator(config)

        # Collect data
        if start_time and end_time:
            print(f"Collecting data from {start_time} to {end_time}...")

            print("- Collecting commits...")
            commits_data = collector.get_commits_for_exact_period(start_time, end_time)

            print("- Collecting pull requests...")
            prs_data = collector.get_pull_requests_for_exact_period(start_time, end_time)

            print("- Collecting issues...")
            issues_data = collector.get_issues_for_exact_period(start_time, end_time)
        else:
            print(f"Collecting data for the last {args.days} day(s)...")

            print("- Collecting commits...")
            commits_data = collector.get_commits_for_period(args.days)

            print("- Collecting pull requests...")
            prs_data = collector.get_pull_requests_for_period(args.days)

            print("- Collecting issues...")
            issues_data = collector.get_issues_for_period(args.days)

        # Generate report
        print("Generating report...")
        if start_time and end_time:
            # Use end date for report title in format: October 10, 2025
            report_date = end_time.strftime('%B %d, %Y')
            report_content = generator.generate_report(commits_data, prs_data, issues_data, report_date)
        else:
            report_content = generator.generate_report(commits_data, prs_data, issues_data)

        # Output results
        if args.dry_run:
            print("\n" + "="*80)
            print("GENERATED REPORT:")
            print("="*80)
            print(report_content)
            print("="*80)
        else:
            # Save report
            output_path = generator.save_report(report_content, args.output)
            print(f"Report saved to: {output_path}")

            # Send to Telegram if requested
            if args.telegram or config.settings["output_settings"]["send_telegram"]:
                print("Sending report to Telegram...")
                if generator.send_to_telegram(report_content, output_path):
                    print("Report sent to Telegram successfully!")
                else:
                    print("Failed to send report to Telegram")

            # Send to Zulip if requested
            if args.zulip or config.settings["output_settings"].get("send_zulip", False):
                print("Sending report to Zulip...")
                if generator.send_to_zulip(report_content):
                    print("Report sent to Zulip successfully!")
                else:
                    print("Failed to send report to Zulip")

        # Print summary
        total_commits = sum(len(commits) for commits in commits_data.values())
        total_prs = sum(len(prs) for prs in prs_data.values())
        total_issues = sum(len(issues) for issues in issues_data.values())

        print(f"\nSummary:")
        print(f"- Commits collected: {total_commits}")
        print(f"- Pull requests with comments: {total_prs}")
        print(f"- Issues with comments: {total_issues}")
        # Count repositories checked
        if config.repositories is None:
            # Get count from collector when using org mode
            repo_count = len(collector.get_org_repositories())
        else:
            repo_count = len(config.repositories)
        print(f"- Repositories checked: {repo_count}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()