import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = os.getenv("E2E_BASE_URL")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not BASE_URL, reason="E2E_BASE_URL is not configured"),
]


def login(client: httpx.Client, email: str, password: str) -> str:
    response = client.post(
        f"{BASE_URL}/api/v1/auth/login", json={"email": email, "password": password}
    )
    response.raise_for_status()
    return response.json()["access_token"]


def wait_for_status(client: httpx.Client, task_id: str, expected: set[str], timeout: float = 45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"{BASE_URL}/api/v1/tasks/{task_id}")
        response.raise_for_status()
        task = response.json()
        if task["status"] in expected:
            return task
        time.sleep(0.5)
    raise AssertionError(f"task {task_id} did not reach {expected}")


def test_upload_deduplicate_process_and_manual_review(tmp_path: Path):
    admin = httpx.Client()
    submitter = httpx.Client()
    reviewer = httpx.Client()
    try:
        admin_token = login(admin, "admin@example.com", "Admin123!")
        submitter_token = login(submitter, "submitter@example.com", "Submitter123!")
        reviewer_token = login(reviewer, "reviewer@example.com", "Reviewer123!")

        unique = uuid4().hex
        gerber = tmp_path / "board.gbr"
        gerber.write_text(
            f"G04 demo-{unique}*\n%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.5*%\n"
            "D10*\nX0Y0D02*\nX100Y100D01*\nM02*\n",
            encoding="utf-8",
        )
        headers = {
            "Authorization": f"Bearer {submitter_token}",
            "Idempotency-Key": f"gerber-demo-{unique}",
        }
        with gerber.open("rb") as stream:
            first = submitter.post(
                f"{BASE_URL}/api/v1/tasks",
                headers=headers,
                files={"file": ("board.gbr", stream, "text/plain")},
                data={"priority": "high"},
            )
        assert first.status_code == 201, first.text
        task_id = first.json()["task"]["id"]
        completed = wait_for_status(submitter, task_id, {"succeeded", "review_pending"})
        assert completed["status"] == "succeeded"

        with gerber.open("rb") as stream:
            duplicate = submitter.post(
                f"{BASE_URL}/api/v1/tasks",
                headers={
                    "Authorization": f"Bearer {submitter_token}",
                    "Idempotency-Key": f"gerber-dup-{unique}",
                },
                files={"file": ("board.gbr", stream, "text/plain")},
            )
        assert duplicate.status_code == 200
        assert duplicate.json()["deduplicated"] is True
        assert duplicate.json()["task"]["id"] == task_id

        svg = tmp_path / "drawing.svg"
        svg.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg"><!-- {unique} --><path d="M0 0"/></svg>',
            encoding="utf-8",
        )
        with svg.open("rb") as stream:
            warning = submitter.post(
                f"{BASE_URL}/api/v1/tasks",
                headers={
                    "Authorization": f"Bearer {submitter_token}",
                    "Idempotency-Key": f"svg-review-{unique}",
                },
                files={"file": ("drawing.svg", stream, "image/svg+xml")},
            )
        assert warning.status_code == 201, warning.text
        warning_task_id = warning.json()["task"]["id"]
        warning_task = wait_for_status(submitter, warning_task_id, {"review_pending"})
        review_id = warning_task["review"]["id"]

        forbidden = submitter.post(
            f"{BASE_URL}/api/v1/reviews/{review_id}/claim",
            headers={"Authorization": f"Bearer {submitter_token}"},
        )
        assert forbidden.status_code == 403

        def claim_with(token: str):
            response = httpx.post(
                f"{BASE_URL}/api/v1/reviews/{review_id}/claim",
                headers={"Authorization": f"Bearer {token}"},
            )
            return token, response

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim_with, [admin_token, reviewer_token]))
        assert sorted(response.status_code for _, response in claims) == [200, 409]
        winner_token = next(token for token, response in claims if response.status_code == 200)
        decision = httpx.post(
            f"{BASE_URL}/api/v1/reviews/{review_id}/decision",
            headers={"Authorization": f"Bearer {winner_token}"},
            json={"decision": "reject", "comment": "缺少尺寸信息"},
        )
        assert decision.status_code == 200, decision.text
        rejected = wait_for_status(submitter, warning_task_id, {"failed"})
        assert rejected["failure_code"] == "REVIEW_REJECTED"

        audit = admin.post(
            f"{BASE_URL}/api/v1/admin/audit/verify",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert audit.status_code == 200
        assert audit.json()["valid"] is True
    finally:
        admin.close()
        submitter.close()
        reviewer.close()
