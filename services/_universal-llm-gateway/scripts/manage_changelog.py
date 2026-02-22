#!/usr/bin/env python3
"""
Changelog Management Script

Helps organize changelog files by date and lifecycle stage.
"""

import re
from datetime import datetime
from pathlib import Path

CHANGELOG_DIR = Path(__file__).resolve().parents[3] / "changelog"
RESOLVED_DIR = CHANGELOG_DIR / "resolved"
ARCHIVED_DIR = CHANGELOG_DIR / "archived"


def get_file_date(file_path):
    """Get file date from creation time or filename."""
    # First try to get date from filename
    match = re.match(r"(\d{4}-\d{2}-\d{2})", file_path.name)
    if match:
        return datetime.strptime(match.group(1), "%Y-%m-%d")

    # Fall back to file creation time
    stat = file_path.stat()
    return datetime.fromtimestamp(stat.st_ctime)


def rename_with_creation_date():
    """Rename files to include their creation date in YYYY-MM-DD format."""
    for file_path in CHANGELOG_DIR.glob("*.md"):
        if file_path.name in ["README.md", "MANAGEMENT.md"]:
            continue

        # Skip if already has date in filename
        if re.match(r"\d{4}-\d{2}-\d{2}", file_path.name):
            continue

        file_date = get_file_date(file_path)
        date_str = file_date.strftime("%Y-%m-%d")

        # Create new filename with date prefix
        new_name = f"{date_str}-{file_path.name}"
        new_path = file_path.parent / new_name

        if not new_path.exists():
            file_path.rename(new_path)
            print(f"📅 Renamed: {file_path.name} → {new_name}")
        else:
            print(f"⚠️  Skipped: {file_path.name} (target exists)")


def categorize_files():
    """Categorize files by age and move to appropriate directories."""
    today = datetime.now()

    for file_path in CHANGELOG_DIR.glob("*.md"):
        if file_path.name in ["README.md", "MANAGEMENT.md"]:
            continue

        file_date = get_file_date(file_path)
        age_days = (today - file_date).days

        if age_days > 90:
            # Archive old files
            new_path = ARCHIVED_DIR / file_path.name
            file_path.rename(new_path)
            print(f"📦 Archived: {file_path.name} ({age_days} days old)")
        elif age_days > 30:
            # Move to resolved
            new_path = RESOLVED_DIR / file_path.name
            file_path.rename(new_path)
            print(f"✅ Resolved: {file_path.name} ({age_days} days old)")
        else:
            print(f"🔄 Active: {file_path.name} ({age_days} days old)")


def list_files():
    """List all changelog files with their status."""
    print("📋 Changelog Status:")
    print("\n🔄 Active Issues (< 30 days):")
    for file_path in sorted(CHANGELOG_DIR.glob("*.md")):
        if file_path.name not in ["README.md", "MANAGEMENT.md"]:
            file_date = get_file_date(file_path)
            age_days = (datetime.now() - file_date).days
            print(f"  {file_path.name} ({age_days} days old)")

    print("\n✅ Resolved Issues (30-90 days):")
    for file_path in sorted(RESOLVED_DIR.glob("*.md")):
        file_date = get_file_date(file_path)
        age_days = (datetime.now() - file_date).days
        print(f"  {file_path.name} ({age_days} days old)")

    print("\n📦 Archived Issues (> 90 days):")
    for file_path in sorted(ARCHIVED_DIR.glob("*.md")):
        file_date = get_file_date(file_path)
        age_days = (datetime.now() - file_date).days
        print(f"  {file_path.name} ({age_days} days old)")


def main():
    """Main function."""
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "cleanup":
            print("🧹 Running changelog cleanup...")
            categorize_files()
        elif sys.argv[1] == "rename":
            print("📅 Renaming files with creation dates...")
            rename_with_creation_date()
        else:
            print("Usage: python manage_changelog.py [cleanup|rename]")
    else:
        list_files()
        print("\n💡 Commands:")
        print("  python manage_changelog.py rename  - Add creation dates to filenames")
        print("  python manage_changelog.py cleanup - Organize files by age")


if __name__ == "__main__":
    main()
