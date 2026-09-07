"""Cover the workflow-closure rule with the payloads that produced the defect.

The situation this encodes really happened: a lane was fixed and merged, its verification ran on
a topic branch, and the branch it landed on kept showing the pre-fix failure until someone
triaged a bug that was already gone. The pre-fix and post-fix payloads below are the recorded
shapes from that episode rather than invented fixtures, so the check is pinned against the thing
it exists to catch.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_workflow_fix_closure import evaluate_closure  # noqa: E402

WORKFLOW = "real_host_smoke.yml"
BRANCH = "main"

# The commit that carried the fix, and the two commits that followed it on the branch.
FIX = "b600e278df31920104b394666c2b7765560d2f57"
MERGE = "292c8ccce03359aff17d88aea8e5d6442cc54df3"
LATER = "520d0ca4ff52a6d36704dfcf616ae9fd1c7c00b5"
BEFORE_FIX = "bcb1be4e42e6f9ff4c05f6991ff4605a9df15142"

ON_BRANCH = (LATER, MERGE, FIX)

PRE_FIX_RUN = {
    "databaseId": 34109004949,
    "conclusion": "failure",
    "status": "completed",
    "headSha": BEFORE_FIX,
    "createdAt": "2026-09-07T09:59:05Z",
}
POST_FIX_RUN = {
    "databaseId": 34153075888,
    "conclusion": "success",
    "status": "completed",
    "headSha": LATER,
    "createdAt": "2026-09-07T18:47:51Z",
}


def _evaluate(runs):
    return evaluate_closure(
        runs, workflow=WORKFLOW, branch=BRANCH, fix_commit=FIX, branch_commits=ON_BRANCH
    )


class WorkflowClosureTests(unittest.TestCase):
    def test_the_recorded_stale_state_is_reported(self):
        # The whole point. The fix was merged and the branch still showed the old failure,
        # so a check that looked only at "is there a green run somewhere" would have passed.
        reasons = _evaluate([PRE_FIX_RUN])

        self.assertEqual(len(reasons), 2, reasons)
        self.assertTrue(any("not 'success'" in reason for reason in reasons), reasons)
        self.assertTrue(
            any("stale rather than passing" in reason for reason in reasons), reasons
        )

    def test_a_green_run_on_another_branch_does_not_close_the_item(self):
        # A successful run whose commit is not on this branch at all: exactly the topic-branch
        # verification that made the original episode look finished.
        topic_run = dict(POST_FIX_RUN, headSha="0" * 40, databaseId=34126177775)
        reasons = _evaluate([topic_run])

        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("stale rather than passing", reasons[0])

    def test_the_recorded_closed_state_reports_nothing(self):
        self.assertEqual(_evaluate([POST_FIX_RUN, PRE_FIX_RUN]), [])

    def test_a_run_at_the_fix_commit_itself_closes_the_item(self):
        # "At or after" includes the fix commit; a lane dispatched on the fix itself is proof.
        reasons = _evaluate([dict(POST_FIX_RUN, headSha=FIX)])

        self.assertEqual(reasons, [])

    def test_no_runs_at_all_is_its_own_reason(self):
        reasons = _evaluate([])

        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("never been exercised", reasons[0])

    def test_the_newest_run_is_chosen_by_timestamp_not_by_position(self):
        # A paged or re-serialized payload arrives in whatever order its producer chose.
        # Reading position instead of createdAt would evaluate the failure and report a
        # closed item as broken, or the reverse.
        oldest_last = [PRE_FIX_RUN, POST_FIX_RUN]
        newest_last = [POST_FIX_RUN, PRE_FIX_RUN]

        self.assertEqual(_evaluate(oldest_last), [])
        self.assertEqual(_evaluate(newest_last), [])

    def test_an_in_progress_run_does_not_displace_the_latest_verdict(self):
        # An unrelated run started a minute ago must not make a closed item look unproven.
        running = {
            "databaseId": 99999999999,
            "conclusion": None,
            "status": "in_progress",
            "headSha": LATER,
            "createdAt": "2026-09-08T01:00:00Z",
        }
        self.assertEqual(_evaluate([running, POST_FIX_RUN]), [])

    def test_an_in_progress_run_cannot_stand_in_for_a_missing_verdict(self):
        running = {
            "databaseId": 99999999999,
            "conclusion": None,
            "status": "in_progress",
            "headSha": LATER,
            "createdAt": "2026-09-08T01:00:00Z",
        }
        reasons = _evaluate([running])

        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("never been exercised", reasons[0])

    def test_a_failed_run_at_the_right_commit_reports_only_the_failure(self):
        # Distinguishing "ran here and failed" from "never ran here" is the difference
        # between a live defect and a stale badge, so the two reasons stay separate.
        reasons = _evaluate([dict(POST_FIX_RUN, conclusion="failure")])

        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("not 'success'", reasons[0])
        self.assertNotIn("stale", reasons[0])


if __name__ == "__main__":
    unittest.main()
