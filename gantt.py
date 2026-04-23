#!/usr/bin/env python3
"""Generate a Mermaid Gantt chart from Linear issues in the Search project.

Only shows key deliverables matching the spreadsheet plan.

Usage:
    source ~/linear-tools/.envrc
    python3 ~/linear-tools/gantt.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, timedelta

import httpx
from pydantic import BaseModel

LINEAR_API_KEY = os.environ["LINEAR_API_KEY"]
SEARCH_PROJECT_ID = "716e79c1-1a55-42d5-ba55-5bf0e419a338"

# Issues to show, grouped by track (row in the spreadsheet).
# Order within each track determines display order.
TRACKS: dict[str, list[str]] = {
    "Xianjun pipeline": [
        "SKILL-867",  # Cleanup non-wiki pipeline
        "SKILL-864",  # Grader update (Handshake)
        "SKILL-865",  # v2 e2e
        "SKILL-870",  # v3 e2e
        "SKILL-871",  # v4 e2e
    ],
    "Infra": [
        "SKILL-842",  # Grader updates (enforce short answer)
        "SKILL-762",  # Batch -> Ray
        "SKILL-861",  # RayReducer
    ],
    "Ethan pipeline": [
        "SKILL-860",  # Onboard graders to batch runner
        "SKILL-862",  # Non-wiki datasource
        "SKILL-851",  # 2nd provider routing
        "SKILL-850",  # Circuit breakers
    ],
    "Docs and planning": [
        "SKILL-846",  # Open questions doc
        "SKILL-645",  # v1 e2e
    ],
    "Scale-up": [
        "SKILL-841",  # Derisk rehearsal run
    ],
}

ALL_IDS = [i for track in TRACKS.values() for i in track]

_MILESTONES_QUERY = """
query($pid: String!) {
  project(id: $pid) {
    projectMilestones { nodes { name targetDate } }
  }
}
"""

_ISSUES_QUERY = """
query($cursor: String) {
  issues(
    filter: {
      project: { name: { eq: "Search" } }
      state: { type: { nin: ["canceled", "completed"] } }
    }
    first: 200
    after: $cursor
    orderBy: createdAt
  ) {
    nodes {
      identifier
      title
      dueDate
      state { type }
      assignee { name }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


class State(BaseModel):
    type: str


class Assignee(BaseModel):
    name: str


class Issue(BaseModel):
    identifier: str
    title: str
    dueDate: str | None = None
    state: State
    assignee: Assignee | None = None


class Milestone(BaseModel):
    name: str
    targetDate: str | None = None


class PageInfo(BaseModel):
    hasNextPage: bool
    endCursor: str | None = None


def _post(query: str, variables: dict | None = None) -> dict:
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": LINEAR_API_KEY},
            json=dict(query=query, variables=variables or {}),
        )
        resp.raise_for_status()
        return resp.json()["data"]


def fetch_data() -> tuple[dict[str, Issue], list[Milestone]]:
    project_data = _post(_MILESTONES_QUERY, dict(pid=SEARCH_PROJECT_ID))
    milestones = [
        Milestone.model_validate(m)
        for m in project_data["project"]["projectMilestones"]["nodes"]
    ]

    issues: dict[str, Issue] = {}
    cursor: str | None = None
    while True:
        variables = dict(cursor=cursor) if cursor else {}
        data = _post(_ISSUES_QUERY, variables)
        page_info = PageInfo.model_validate(data["issues"]["pageInfo"])
        for node in data["issues"]["nodes"]:
            issue = Issue.model_validate(node)
            if issue.identifier in ALL_IDS:
                issues[issue.identifier] = issue
        if not page_info.hasNextPage:
            break
        cursor = page_info.endCursor

    return issues, milestones


def sanitize(text: str) -> str:
    text = re.sub(r"[:\[\]#;`/→()\"']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 50:
        text = text[:47] + "..."
    return text


def assignee_short(issue: Issue) -> str:
    if not issue.assignee:
        return ""
    return issue.assignee.name.split("@")[0].split()[0].capitalize()


def generate_gantt(issues: dict[str, Issue], milestones: list[Milestone]) -> str:
    today = date.today()
    lines = [
        "gantt",
        "    dateFormat YYYY-MM-DD",
        "    axisFormat %b %d",
        "    todayMarker stroke-width:2px,stroke:#f66",
        "",
    ]

    future_milestones = [
        m for m in milestones
        if m.targetDate and date.fromisoformat(m.targetDate) >= today
    ]
    if future_milestones:
        lines.append("    section Milestones")
        for m in sorted(future_milestones, key=lambda x: x.targetDate or ""):
            lines.append(f"    {sanitize(m.name)} :milestone, {m.targetDate}, 0d")
        lines.append("")

    for track_name, issue_ids in TRACKS.items():
        track_issues = [issues[i] for i in issue_ids if i in issues]
        if not track_issues:
            continue

        lines.append(f"    section {track_name}")
        prev_due: date | None = None
        for issue in track_issues:
            due = date.fromisoformat(issue.dueDate) if issue.dueDate else None
            if not due:
                continue

            name = sanitize(issue.title)
            who = assignee_short(issue)
            if who:
                name += f" - {who}"

            task_id = issue.identifier.replace("-", "_")

            if issue.state.type == "started":
                start = today
                status = "active,"
            elif prev_due and prev_due > today:
                start = prev_due
                status = ""
            else:
                start = max(today, due - timedelta(days=5))
                status = ""

            if start >= due:
                start = due - timedelta(days=1)

            lines.append(f"    {name} :{status} {task_id}, {start}, {due}")
            prev_due = due
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    issues, milestones = fetch_data()
    chart = generate_gantt(issues, milestones)
    if sys.stdout.isatty():
        print("```mermaid")
        print(chart)
        print("```")
    else:
        print(chart)


if __name__ == "__main__":
    main()
