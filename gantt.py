#!/usr/bin/env python3
"""Generate a Mermaid Gantt chart from Linear issues in the Search project.

Usage:
    source ~/notion-linear-sync/.envrc
    python3 /tmp/linear-gantt.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, timedelta

import httpx
from pydantic import BaseModel

LINEAR_API_KEY = os.environ["LINEAR_API_KEY"]

_MILESTONES_QUERY = """
query($name: String!) {
  project(id: $name) { name projectMilestones { nodes { id name targetDate } } }
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
      state { name type }
      assignee { name }
      parent { identifier }
      projectMilestone { name }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

SEARCH_PROJECT_ID = "716e79c1-1a55-42d5-ba55-5bf0e419a338"


class State(BaseModel):
    name: str
    type: str


class Assignee(BaseModel):
    name: str


class Parent(BaseModel):
    identifier: str


class MilestoneRef(BaseModel):
    name: str


class Issue(BaseModel):
    identifier: str
    title: str
    dueDate: str | None = None
    state: State
    assignee: Assignee | None = None
    parent: Parent | None = None
    projectMilestone: MilestoneRef | None = None


class ProjectMilestone(BaseModel):
    id: str
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


def fetch_data() -> tuple[list[Issue], list[ProjectMilestone]]:
    project_data = _post(
        _MILESTONES_QUERY, dict(name=SEARCH_PROJECT_ID)
    )
    milestones = [
        ProjectMilestone.model_validate(m)
        for m in project_data["project"]["projectMilestones"]["nodes"]
    ]

    issues: list[Issue] = []
    cursor: str | None = None
    while True:
        variables = dict(cursor=cursor) if cursor else {}
        data = _post(_ISSUES_QUERY, variables)
        page_info = PageInfo.model_validate(data["issues"]["pageInfo"])
        for node in data["issues"]["nodes"]:
            issues.append(Issue.model_validate(node))
        if not page_info.hasNextPage:
            break
        cursor = page_info.endCursor

    return issues, milestones


def sanitize(text: str) -> str:
    text = re.sub(r"[:\[\]#;`/→()\"']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 55:
        text = text[:52] + "..."
    return text


def assignee_tag(issue: Issue) -> str:
    if not issue.assignee:
        return ""
    name = issue.assignee.name.split("@")[0].split()[0].capitalize()
    return f" - {name}"


def generate_gantt(issues: list[Issue], milestones: list[ProjectMilestone]) -> str:
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

    issues_with_dates = [
        i for i in issues
        if i.dueDate and date.fromisoformat(i.dueDate) >= today
    ]
    top_level = [i for i in issues_with_dates if i.parent is None]
    children_by_parent: dict[str, list[Issue]] = {}
    for i in issues_with_dates:
        if i.parent:
            children_by_parent.setdefault(i.parent.identifier, []).append(i)

    parents_with_children = {
        i.identifier for i in top_level if i.identifier in children_by_parent
    }

    standalone: dict[str, list[Issue]] = {}
    for i in top_level:
        if i.identifier in parents_with_children:
            continue
        section = i.projectMilestone.name if i.projectMilestone else "Other"
        standalone.setdefault(section, []).append(i)

    for section in sorted(standalone):
        lines.append(f"    section {sanitize(section)}")
        for i in sorted(standalone[section], key=lambda x: x.dueDate or ""):
            _render(lines, i, today)
        lines.append("")

    for p in sorted(
        [i for i in top_level if i.identifier in parents_with_children],
        key=lambda x: x.dueDate or "",
    ):
        lines.append(f"    section {sanitize(p.title)}")
        for child in sorted(children_by_parent[p.identifier], key=lambda x: x.dueDate or ""):
            _render(lines, child, today)
        lines.append("")

    return "\n".join(lines)


def _render(lines: list[str], issue: Issue, today: date) -> None:
    due = date.fromisoformat(issue.dueDate) if issue.dueDate else None
    if not due or due < today:
        return

    name = sanitize(issue.title) + assignee_tag(issue)
    task_id = issue.identifier.replace("-", "_")

    match issue.state.type:
        case "started":
            start = today
            status = "active,"
        case _:
            start = max(today, due - timedelta(days=7))
            status = ""

    lines.append(f"    {name} :{status} {task_id}, {start}, {due}")


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
