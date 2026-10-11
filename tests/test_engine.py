import unittest

from anyp5.canary import plant
from anyp5.engine import Engine
from anyp5.tiers import classify, parse_diff

H = 3600
DAY = 24 * H

DIFF = """diff --git a/src/buf.c b/src/buf.c
--- a/src/buf.c
+++ b/src/buf.c
@@ -10,3 +10,7 @@ int put(buf *b, int i, char c)
 {
+    if (i >= b->size) return -1;
+    for (int k = 0; k < b->size; k++)
+        b->data[k] = 0;
+    b->data[i] = c;
     return 0;
 }
"""


def change_of(engine, author, pr=1, head="h1", diff=DIFF, now=0):
    paths, added = parse_diff(diff)
    return engine.submit(pr, head, author, paths, added, diff, now)


class Base(unittest.TestCase):
    def setUp(self):
        self.e = Engine(config={"canary_every": 1e9})
        for login in ("alice", "bob", "carol", "dave", "erin"):
            self.e.join(login, 0)


class TiersTest(unittest.TestCase):
    def test_tier_follows_what_the_change_touches(self):
        self.assertEqual(classify(*parse_diff(DIFF)), "green")
        self.assertEqual(classify(["docs/a.md"], [("docs/a.md", 1, "see https://x")]), "green")
        self.assertEqual(classify(["src/a.c"], [("src/a.c", 1, 'system("ls");')]), "red")
        self.assertEqual(classify([".github/workflows/ci.yml"], []), "red")


class CanaryTest(unittest.TestCase):
    def test_plant_keeps_a_valid_diff_and_names_the_defect(self):
        for seed in map(str, range(12)):
            out = plant(DIFF, seed)
            self.assertIsNotNone(out)
            diff, defect = out
            self.assertNotEqual(diff, DIFF)
            self.assertTrue(defect)
            header = [l for l in diff.splitlines() if l.startswith("@@")][0]
            body = diff.splitlines()[diff.splitlines().index(header) + 1:]
            new_count = int(header.split("+")[1].split(",")[1].split(" ")[0])
            self.assertEqual(new_count, sum(1 for l in body if not l.startswith("-")))


class ReviewTest(Base):
    def test_draw_review_approve_merge(self):
        cid = change_of(self.e, "alice")
        self.e.tick(1)
        assigned = self.e.q("select reviewer from assignments where change_id=?", cid)
        tier = self.e.one("select tier from changes where id=?", cid)["tier"]
        self.assertEqual(len(assigned), self.e.cfg["reviewers"][tier])
        self.assertNotIn("alice", [a["reviewer"] for a in assigned])
        for a in assigned:
            [job] = self.e.next_review(a["reviewer"], 2)
            self.assertEqual(set(job), {"assignment", "diff"})
            self.e.verdict(a["reviewer"], job["assignment"], "approve", 3)
        self.assertEqual(self.e.one("select state from changes where id=?", cid)["state"], "approved")

    def test_jury_duty_blocks_new_work_until_reviews_are_done(self):
        change_of(self.e, "alice")
        self.e.tick(1)
        reviewer = self.e.q("select reviewer from assignments")[0]["reviewer"]
        task = self.e.add_task("t", "green", 1)
        with self.assertRaises(ValueError):
            self.e.claim(reviewer, task, 2)

    def test_reject_closes_change(self):
        cid = change_of(self.e, "alice")
        self.e.tick(1)
        r = self.e.q("select * from assignments")[0]
        self.e.verdict(r["reviewer"], r["id"], "reject", 2, reason="bug")
        self.assertEqual(self.e.one("select state from changes where id=?", cid)["state"], "rejected")

    def test_spare_reviewer_when_one_is_slow(self):
        cid = change_of(self.e, "alice")
        self.e.tick(0)
        self.e.tick(7 * H)
        tier = self.e.one("select tier from changes where id=?", cid)["tier"]
        self.assertEqual(len(self.e.q("select * from assignments where change_id=?", cid)), self.e.cfg["reviewers"][tier] + 1)


class LeaseTest(Base):
    def state(self, task):
        return self.e.one("select state from tasks where id=?", task)["state"]

    def test_pushed_work_renews_the_lease_heartbeats_do_not(self):
        task = self.e.add_task("t", "green", 0)
        self.e.claim("bob", task, 0)
        self.e.handover(task, "bob/t", 20 * H)
        self.e.tick(90 * H)
        self.assertEqual(self.state(task), "leased")
        self.e.alive("bob", 91 * H)
        self.e.tick(93 * H)
        self.assertEqual(self.state(task), "open")

    def test_first_push_is_due_within_a_day_and_leases_are_capped(self):
        quiet = self.e.add_task("quiet", "green", 0)
        self.e.claim("bob", quiet, 0)
        self.e.tick(25 * H)
        self.assertEqual(self.state(quiet), "open")
        busy = self.e.add_task("busy", "green", 0)
        self.e.claim("carol", busy, 0)
        for hour in range(20, 120, 20):
            self.e.handover(busy, "carol/busy", hour * H)
            self.e.tick(hour * H)
            self.assertEqual(self.state(busy), "leased")
        self.e.handover(busy, "carol/busy", 119 * H)
        self.e.tick(121 * H)
        self.assertEqual(self.state(busy), "open")
        self.assertEqual(self.e.one("select branch from tasks where id=?", busy)["branch"], "carol/busy")

    def test_a_reviewer_confirming_progress_extends_the_cap(self):
        task = self.e.add_task("big", "green", 0)
        self.e.claim("carol", task, 0)
        self.e.handover(task, "carol/big", 100 * H)
        change = self.e.extend("carol", task, DIFF, 110 * H)
        self.e.alive("dave", 110 * H)
        self.e.tick(110 * H)
        a = self.e.one("select * from assignments where change_id=?", change)
        self.assertIn("question", self.e.next_review(a["reviewer"], 110 * H)[0])
        self.e.verdict(a["reviewer"], a["id"], "approve", 111 * H)
        self.e.handover(task, "carol/big", 150 * H)
        self.e.tick(200 * H)
        self.assertEqual(self.state(task), "leased")

    def test_away_freezes_the_lease_within_limits(self):
        task = self.e.add_task("t", "green", 0)
        self.e.claim("bob", task, 0)
        with self.assertRaises(ValueError):
            self.e.away("bob", task, 3, H)
        self.e.handover(task, "bob/t", 2 * H)
        self.assertEqual(self.e.away("bob", task, 9, 3 * H), 5 * 24)
        self.e.tick(100 * H)
        self.assertEqual(self.state(task), "leased")
        self.e.tick(121 * H)
        self.assertEqual(self.state(task), "open")

    def test_away_is_short_on_a_task_others_wait_on(self):
        hub = self.e.add_task("hub", "green", 0)
        self.e.add_task("after", "green", 0, deps=[hub])
        self.e.claim("bob", hub, 0)
        self.e.handover(hub, "bob/hub", H)
        self.assertEqual(self.e.away("bob", hub, 5, 2 * H), 48)

    def test_critical_tasks_relay_when_the_holder_goes_offline(self):
        task = self.e.add_task("blocker", "green", 0, critical=True)
        self.e.claim("bob", task, 0)
        self.e.handover(task, "bob/blocker", H)
        self.e.alive("carol", 14 * H)
        self.e.tick(14 * H)
        self.assertEqual(self.state(task), "open")


class TaskGraphTest(Base):
    def test_capabilities_and_dependencies_decide_what_one_can_claim(self):
        game = self.e.add_task("run the prologue", "green", 0, requires=["game", "windows"])
        after = self.e.add_task("fix what it shows", "green", 0, deps=[game])
        self.assertEqual([t["id"] for t in self.e.open_tasks("bob")], [])
        with self.assertRaises(ValueError):
            self.e.claim("bob", game, 0)
        self.e.set_caps("bob", ["windows", "game", "gpu"], 0)
        self.assertEqual(self.e.open_tasks("bob")[0]["waiting"], 1)
        with self.assertRaises(ValueError):
            self.e.claim("carol", after, 0)
        self.e.claim("bob", game, 0)
        self.e.finish_task(game, H)
        self.e.claim("carol", after, H)

    def test_member_proposals_are_reviewed_before_opening(self):
        task = self.e.add_task("split the parser", "green", 0, proposer="dave")
        self.assertEqual(self.state(task), "proposed")
        self.e.tick(H)
        a = self.e.one("select * from assignments where kind='review'")
        self.assertNotEqual(a["reviewer"], "dave")
        self.e.verdict(a["reviewer"], a["id"], "approve", 2 * H)
        self.assertEqual(self.state(task), "open")

    def test_wanted_board_shows_the_scarce_capability(self):
        for i in range(3):
            self.e.add_task(f"game {i}", "green", 0, requires=["game"])
        self.e.add_task("docs", "green", 0)
        self.e.set_caps("bob", ["game"], 0)
        board = self.e.wanted(H)
        self.assertEqual(board[0], {"requires": ["game"], "open": 3, "blocked": 0, "members": 1,
                                    "tasks_per_member": 3.0})

    def state(self, task):
        return self.e.one("select state from tasks where id=?", task)["state"]


class BanTest(Base):
    def test_confirmed_backdoor_bans_reverts_and_audits_approvers(self):
        first = change_of(self.e, "alice", pr=1)
        self.e.tick(1)
        approvers = [a["reviewer"] for a in self.e.q("select * from assignments where change_id=?", first)]
        for a in self.e.q("select * from assignments where change_id=?", first):
            self.e.verdict(a["reviewer"], a["id"], "approve", 2)
        self.e.merged(first, 3)
        other = change_of(self.e, "erin", pr=2, head="h2")
        for a in self.e.q("select * from assignments where verdict is null"):
            self.e.verdict(a["reviewer"], a["id"], "approve", 4)
        self.e.tick(5)
        for a in self.e.q("select * from assignments where change_id=?", other):
            self.e.verdict(a["reviewer"], a["id"], "approve", 6)
        self.e.merged(other, 7)
        for a in self.e.q("select * from assignments where verdict is null"):
            self.e.verdict(a["reviewer"], a["id"], "approve", 8)
        bad = change_of(self.e, "alice", pr=3, head="h3")
        self.e.tick(9)
        r = self.e.q("select * from assignments where change_id=? and verdict is null", bad)[0]
        self.e.verdict(r["reviewer"], r["id"], "reject", 10, severity="backdoor")
        auditors = self.e.q("select * from assignments where change_id=? and kind='escalation'", bad)
        self.assertEqual(len(auditors), 2)
        for a in auditors:
            self.e.verdict(a["reviewer"], a["id"], "reject", 11, severity="backdoor")
        self.assertEqual(self.e.one("select banned from members where login='alice'")["banned"], 1)
        self.assertEqual(self.e.one("select state from changes where id=?", first)["state"], "revert")

    def test_one_reviewer_alone_cannot_ban(self):
        cid = change_of(self.e, "alice")
        self.e.tick(1)
        r = self.e.q("select * from assignments")[0]
        self.e.verdict(r["reviewer"], r["id"], "reject", 2, severity="backdoor")
        auditors = self.e.q("select * from assignments where change_id=? and kind='escalation'", cid)
        self.e.verdict(auditors[0]["reviewer"], auditors[0]["id"], "approve", 3)
        self.e.verdict(auditors[1]["reviewer"], auditors[1]["id"], "reject", 3, severity="backdoor")
        self.assertEqual(self.e.one("select banned from members where login='alice'")["banned"], 0)


class ReleaseTest(Base):
    def test_release_waits_for_soak_and_post_merge_checks(self):
        cid = change_of(self.e, "alice")
        self.e.tick(1)
        for a in self.e.q("select * from assignments"):
            self.e.verdict(a["reviewer"], a["id"], "approve", 2)
        self.e.merged(cid, 3)
        self.assertIsNone(self.e.releasable(3 + 13 * DAY))
        for a in self.e.q("select * from assignments where verdict is null"):
            self.e.alive(a["reviewer"], 3 + 13 * DAY)
            self.e.verdict(a["reviewer"], a["id"], "approve", 3 + 13 * DAY)
        self.assertEqual(self.e.releasable(3 + 15 * DAY)["id"], cid)


class CanaryFlowTest(unittest.TestCase):
    def test_canaries_are_blind_and_calibrate_reviewers(self):
        e = Engine(config={"canary_every": 1})
        for login in ("alice", "bob", "carol", "dave"):
            e.join(login, 0)
        cid = change_of(e, "alice")
        e.tick(1)
        for a in e.q("select * from assignments where change_id=?", cid):
            e.verdict(a["reviewer"], a["id"], "approve", 2)
        e.merged(cid, 3)
        for a in e.q("select * from assignments where verdict is null"):
            e.verdict(a["reviewer"], a["id"], "approve", 4)
        e.tick(5)
        canary = e.one("select * from changes where kind='canary'")
        self.assertIsNotNone(canary)
        a = e.one("select * from assignments where change_id=?", canary["id"])
        [job] = e.next_review(a["reviewer"], 6)
        self.assertEqual(set(job), {"assignment", "diff"})
        e.verdict(a["reviewer"], job["assignment"], "reject", 7)
        m = e.member(login=a["reviewer"])
        self.assertEqual((m["cal_seen"], m["cal_hits"]), (1, 1))


if __name__ == "__main__":
    unittest.main()


class SeedTest(unittest.TestCase):
    def test_who_reviews_depends_on_the_hidden_seed_not_only_on_the_change(self):
        """An author who knows every public input still cannot steer the draw by grinding their patch."""
        picks = []
        for _ in range(2):
            e = Engine(config={"canary_every": 1e9})
            for login in ("alice", "bob", "carol", "dave", "erin", "frank", "grace"):
                e.join(login, 0)
            for head in range(8):
                cid = change_of(e, "alice", pr=head, head=f"h{head}")
                e.tick(1)
            picks.append([r["reviewer"] for r in e.q("select reviewer from assignments order by id")])
        self.assertNotEqual(picks[0], picks[1])
