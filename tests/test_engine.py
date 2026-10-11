import json
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

    def test_renames_binaries_and_modes_are_red_even_without_text_lines(self):
        rename = ("diff --git a/docs/example.yml b/.github/workflows/pwn.yml\nsimilarity index 100%\n"
                  "rename from docs/example.yml\nrename to .github/workflows/pwn.yml\n")
        binary = ("diff --git a/src/blob.c b/src/blob.c\nnew file mode 100644\nindex 0000000..1234567\n"
                  "GIT binary patch\nliteral 12\nzcmV+b0RR6000031\n\nliteral 0\nHcmV?d00001\n")
        mode = "diff --git a/src/a.c b/src/a.c\nold mode 100644\nnew mode 100755\n"
        for diff in (rename, binary, mode):
            self.assertEqual(classify(*parse_diff(diff)), "red")
        self.assertIn(".github/workflows/pwn.yml", parse_diff(rename)[0])
        self.assertEqual(classify(["src/a.c"], [("src/a.c", 1, 'execl("/bin/sh", "sh", 0);')]), "red")

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

    def test_a_lone_reject_asks_a_second_opinion_two_close_the_change(self):
        cid = change_of(self.e, "alice")
        self.e.tick(1)
        r = self.e.q("select * from assignments")[0]
        self.e.verdict(r["reviewer"], r["id"], "reject", 2, reason="bug")
        self.assertEqual(self.e.one("select state from changes where id=?", cid)["state"], "review")
        self.e.tick(3)
        second = self.e.one("select * from assignments where verdict is null and change_id=?", cid)
        self.e.verdict(second["reviewer"], second["id"], "reject", 4, reason="bug")
        self.assertEqual(self.e.one("select state from changes where id=?", cid)["state"], "rejected")

    def test_a_reject_outvoted_by_approvals_merges(self):
        cid = change_of(self.e, "alice")
        self.e.tick(1)
        r = self.e.q("select * from assignments")[0]
        self.e.verdict(r["reviewer"], r["id"], "reject", 2, reason="taste")
        for hour in (3, 5):
            self.e.tick(hour)
            for a in self.e.q("select * from assignments where verdict is null and change_id=?", cid):
                self.e.verdict(a["reviewer"], a["id"], "approve", hour + 1)
        self.assertEqual(self.e.one("select state from changes where id=?", cid)["state"], "approved")

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
        with self.assertRaises(ValueError):
            self.e.handover(task, "mallory/t", 10 * H, login="carol")
        self.e.handover(task, "bob/t", 20 * H, login="bob")
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

    def open_proposal(self, title, now, reserve):
        task = self.e.add_task(title, "green", now, proposer="dave", reserve=reserve)
        self.e.tick(now + H)
        a = self.e.one("select * from assignments a join changes c on c.id=a.change_id "
                       "where c.kind='task' and c.source=? and a.verdict is null", task)
        self.e.verdict(a["reviewer"], a["id"], "approve", now + 2 * H)
        return task, next(m["login"] for m in self.e.q("select login from members")
                          if m["login"] not in ("dave", a["reviewer"]))

    def test_a_proposer_who_wants_the_task_gets_the_first_claim(self):
        task, other = self.open_proposal("split the parser", 0, reserve=True)
        with self.assertRaises(ValueError):
            self.e.claim(other, task, 3 * H)
        self.e.claim("dave", task, 3 * H)
        self.assertEqual(self.e.one("select holder from tasks where id=?", task)["holder"], "dave")

    def test_an_idea_is_open_to_everyone_and_a_reservation_expires(self):
        idea, other = self.open_proposal("port the renderer", 0, reserve=False)
        self.e.claim(other, idea, 3 * H)
        task, other = self.open_proposal("split the parser", 4 * H, reserve=True)
        self.e.claim(other, task, 6 * H + 49 * H)
        self.assertEqual([t["id"] for t in self.e.taken_tasks()], [idea, task])

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


class ArchitectTest(unittest.TestCase):
    def setUp(self):
        self.e = Engine(config={"canary_every": 1e9, "architects": ["carol"]})
        for login in ("alice", "bob", "carol", "dave", "erin"):
            self.e.join(login, 0)

    def state(self, task):
        return self.e.one("select state from tasks where id=?", task)["state"]

    def review(self, task, verdict, now):
        a = self.e.one("select a.* from assignments a join changes c on c.id=a.change_id "
                       "where c.kind='task' and c.source=? and a.verdict is null", task)
        self.e.verdict(a["reviewer"], a["id"], verdict, now)

    def test_a_proposal_opens_after_the_review_and_one_architect_in_any_order(self):
        first = self.e.add_task("split the parser", "green", 0, proposer="dave")
        second = self.e.add_task("port the renderer", "green", 0, proposer="bob")
        self.e.tick(H)
        self.review(first, "approve", 2 * H)
        self.assertEqual(self.state(first), "proposed")
        self.assertEqual([p["title"] for p in self.e.awaiting_direction()], ["split the parser", "port the renderer"])
        self.assertNotIn("dave", json.dumps(self.e.awaiting_direction()))
        self.e.direct("carol", first, "approve", 3 * H)
        self.assertEqual(self.state(first), "open")
        self.e.direct("carol", second, "approve", 3 * H)
        self.assertEqual(self.state(second), "proposed")
        self.review(second, "approve", 4 * H)
        self.assertEqual(self.state(second), "open")

    def test_an_architect_closes_off_direction_with_a_reason_and_the_review_is_withdrawn(self):
        task = self.e.add_task("rewrite it in another language", "green", 0, proposer="dave")
        self.e.tick(H)
        a = self.e.one("select * from assignments where verdict is null")
        with self.assertRaises(ValueError):
            self.e.direct("carol", task, "close", 2 * H)
        self.e.direct("carol", task, "close", 2 * H, reason="the roadmap keeps C++ for the recompiler")
        row = self.e.one("select * from tasks where id=?", task)
        self.assertEqual((row["state"], row["direction"]), ("off-direction", "the roadmap keeps C++ for the recompiler"))
        with self.assertRaises(ValueError):
            self.e.verdict(a["reviewer"], a["id"], "approve", 3 * H)
        with self.assertRaises(ValueError):
            self.e.direct("carol", task, "approve", 3 * H)

    def test_architects_have_no_say_over_code_and_others_none_over_direction(self):
        task = self.e.add_task("split the parser", "green", 0, proposer="dave")
        with self.assertRaises(ValueError):
            self.e.direct("dave", task, "approve", H)
        cid = change_of(self.e, "alice")
        opened = self.e.add_task("maintainer task", "green", 0)
        for target in (cid, opened):
            with self.assertRaises(ValueError):
                self.e.direct("carol", target, "close", H, reason="no")
        self.assertEqual(self.e.one("select state from changes where id=?", cid)["state"], "review")

    def test_an_architects_own_proposal_needs_only_the_review(self):
        task = self.e.add_task("plan the next milestone", "green", 0, proposer="carol")
        self.e.tick(H)
        self.review(task, "approve", 2 * H)
        self.assertEqual(self.state(task), "open")


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
        self.assertEqual(self.e.one("select banned from members where login='alice'")["banned"], 0)
        self.e.confirm_ban(bad, 12)
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
        for reason, expected in (("looks wrong", (1, 0)), ("{line}: bound is off", (1, 1))):
            e = Engine(config={"canary_every": 1, "clean_canary_share": 0})
            for login in ("alice", "bob", "carol", "dave"):
                e.join(login, 0)
            change_of(e, "alice")
            e.tick(1)
            canary = e.one("select * from changes where kind='canary'")
            self.assertIsNotNone(canary)
            a = e.one("select * from assignments where change_id=?", canary["id"])
            [job] = e.next_review(a["reviewer"], 6)
            self.assertEqual(set(job), {"assignment", "diff"})
            line = canary["defect"].rsplit(":", 1)[1].rstrip(")")
            e.verdict(a["reviewer"], job["assignment"], "reject", 7, reason=reason.format(line=f"src/buf.c:{line}"))
            m = e.member(login=a["reviewer"])
            self.assertEqual((m["cal_seen"], m["cal_hits"]), expected)

    def test_rejecting_everything_ranks_last(self):
        from anyp5.engine import score
        careful = {"cal_hits": 6, "cal_seen": 10, "clean_ok": 9, "clean_seen": 10}
        reject_all = {"cal_hits": 10, "cal_seen": 10, "clean_ok": 0, "clean_seen": 10}
        lazy = {"cal_hits": 0, "cal_seen": 10, "clean_ok": 10, "clean_seen": 10}
        self.assertGreater(score(careful), 3 * score(reject_all))
        self.assertGreater(score(careful), 3 * score(lazy))

    def test_a_canary_ends_unscored_when_its_original_leaves_review(self):
        e = Engine(config={"canary_every": 1, "clean_canary_share": 0})
        for login in ("alice", "bob", "carol", "dave"):
            e.join(login, 0)
        cid = change_of(e, "alice")
        e.tick(1)
        canary = e.one("select * from changes where kind='canary'")
        for a in e.q("select * from assignments where change_id=?", cid):
            e.verdict(a["reviewer"], a["id"], "approve", 2)
        e.merged(cid, 3)
        e.tick(4)
        a = e.one("select * from assignments where change_id=?", canary["id"])
        self.assertEqual(a["verdict"], "withdrawn")
        self.assertEqual(e.member(login=a["reviewer"])["cal_seen"], 0)


class DrawTest(unittest.TestCase):
    def members(self, e, n):
        for i in range(n):
            e.join(f"m{i:02d}", 0)
            hits = i % 10
            e.db.execute("update members set cal_seen=10, cal_hits=?, clean_seen=6, clean_ok=6 where login=?",
                         (hits, f"m{i:02d}"))
        e.db.commit()

    def test_draw_stays_in_the_band_and_favours_the_best_without_monopoly(self):
        e = Engine(config={"canary_every": 1e9})
        self.members(e, 20)
        picks = {}
        for k in range(400):
            change = {"id": 10**6 + k, "head": f"h{k}", "author": "nobody", "source": None}
            [login] = e.draw(change, 1, "review", 1, pool=e.idle_pool(1))
            e.db.execute("delete from assignments")
            picks[login] = picks.get(login, 0) + 1
        best = {f"m{i:02d}" for i in range(20) if i % 10 >= 7}
        self.assertEqual(set(picks), best)
        top = sum(picks[f"m{i:02d}"] for i in (9, 19))
        low = sum(picks[f"m{i:02d}"] for i in (7, 17))
        self.assertGreater(top, low)
        self.assertLess(max(picks.values()), 400 / 2)

    def test_canaries_confirm_perfect_records_first_then_newcomers(self):
        e = Engine(config={"canary_every": 1e9})
        for login, seen, hits in (("perfect", 3, 3), ("missed", 3, 2), ("new", 0, 0), ("veteran", 4, 4)):
            e.join(login, 0)
            e.db.execute("update members set cal_seen=?, cal_hits=? where login=?", (seen, hits, login))
        order = sorted(e.idle_pool(1), key=e.canary_order)
        self.assertEqual([m["login"] for m in order], ["veteran", "perfect", "new", "missed"])
        e.cfg["canary_routing"] = "least_tested"
        self.assertEqual(sorted(e.idle_pool(1), key=e.canary_order)[0]["login"], "new")


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


class TimingTest(unittest.TestCase):
    def test_draw_order_in_a_pass_follows_the_seed_not_submission_and_hides_the_canary(self):
        """Assignment ids are visible to reviewers: within a pass they must not follow the order in which
        changes arrived, and the canary must not always be the last review drawn."""
        orders, canary_last = [], []
        for _ in range(4):
            e = Engine(config={"canary_every": 1, "clean_canary_share": 0})
            for login in [f"m{i}" for i in range(30)]:
                e.join(login, 0)
            submitted = [change_of(e, f"m{i}", pr=i, head=f"h{i}") for i in range(8)]
            e.tick(1)
            rows = e.q("select a.change_id, c.kind from assignments a join changes c on c.id=a.change_id order by a.id")
            orders.append([r["change_id"] for r in rows if r["kind"] == "pr"])
            canary_last.append(rows[-1]["kind"] == "canary")
            self.assertEqual(sorted(orders[-1]), submitted)
            self.assertEqual(len({r["change_id"] for r in rows if r["kind"] == "canary"}), 1)
            self.assertEqual({r["drawn_at"] for r in e.q("select drawn_at from assignments")}, {1})
        self.assertTrue(any(order != sorted(order) for order in orders))
        self.assertFalse(all(canary_last))

    def test_shuffled_depends_on_the_hidden_seed(self):
        items = [(i, f"login{i}") for i in range(50)]
        a, b = Engine(), Engine()
        self.assertNotEqual(a.shuffled(items, 1, "x"), items)
        self.assertNotEqual(a.shuffled(items, 1, "x"), b.shuffled(items, 1, "x"))
        self.assertEqual(sorted(a.shuffled(items, 1, "x")), items)


RECIPE = {"name": "buffer not cleared", "glob": "src/*.c", "match": r"b->data\[k\] = 0;",
          "replace": "b->data[k] = 1;", "defect": "fills the buffer with ones instead of clearing it"}


def double_write_hook(line, path, seed):
    if "b->data[i] = c;" in line:
        return line.replace("b->data[i]", "b->data[i + 1]"), "writes one slot past the index"
    return None


class ProjectCanaryTest(unittest.TestCase):
    def test_a_project_recipe_goes_first_and_names_its_line(self):
        from anyp5.canary import names_line
        for seed in map(str, range(6)):
            diff, defect = plant(DIFF, seed, recipes=[RECIPE])
            self.assertIn("+        b->data[k] = 1;", diff)
            self.assertTrue(defect.startswith("buffer not cleared: fills the buffer"))
            self.assertTrue(defect.endswith("(src/buf.c:13)"))
            self.assertTrue(names_line(defect, "src/buf.c:13 sets ones"))
            self.assertFalse(names_line(defect, "src/buf.c:40 style"))

    def test_recipes_that_do_not_apply_fall_back_to_built_in_mutations(self):
        elsewhere = dict(RECIPE, glob="lib/*.py")
        unmatched = dict(RECIPE, match="never present")
        for recipe in (elsewhere, unmatched):
            self.assertEqual(plant(DIFF, "3", recipes=[recipe]), plant(DIFF, "3"))

    def test_drop_and_hook_recipes(self):
        drop = {"name": "guard removed", "match": r"^\s*if \(i >= b->size\)", "drop": True,
                "defect": "removed the bound check"}
        diff, defect = plant(DIFF, "1", recipes=[drop])
        self.assertNotIn("if (i >= b->size)", diff)
        self.assertIn("@@ -10,3 +10,6 @@", diff)
        self.assertTrue(defect.endswith("(src/buf.c:11)"))
        hook = {"name": "off by one write", "hook": f"{__name__}:double_write_hook"}
        diff, defect = plant(DIFF, "1", recipes=[hook])
        self.assertIn("+    b->data[i + 1] = c;", diff)
        self.assertEqual(defect, "off by one write: writes one slot past the index (src/buf.c:14)")

    def test_recipes_from_a_directory_plain_or_sealed_and_hooks_refused(self):
        import json, shutil, tempfile
        from pathlib import Path
        from anyp5 import seal
        from anyp5.canary import load_recipes
        root = Path(tempfile.mkdtemp(prefix="anyp5-canaries-"))
        try:
            key = seal.keygen()
            (root / "a.json").write_text(json.dumps([RECIPE]))
            (root / "b.sealed").write_text(seal.seal(seal.public(key)["box"], json.dumps(dict(RECIPE, name="x")).encode()))
            (root / "README.md").write_text("ignored")
            self.assertEqual([r["name"] for r in load_recipes(root)], ["buffer not cleared"])
            self.assertEqual([r["name"] for r in load_recipes(root, key=key)], ["buffer not cleared", "x"])
            (root / "c.json").write_text(json.dumps({"name": "h", "hook": "os:system"}))
            with self.assertRaises(ValueError):
                load_recipes(root)
            with self.assertRaises(ValueError):
                Engine(config={"canaries": [{"name": "no defect", "match": "x", "replace": "y"}]})
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_engine_plants_project_canaries_and_keeps_clean_ones_clean(self):
        for share, planted in ((0, True), (1, False)):
            e = Engine(config={"canary_every": 1, "clean_canary_share": share, "canaries": [RECIPE]})
            for login in ("alice", "bob", "carol", "dave"):
                e.join(login, 0)
            change_of(e, "alice")
            e.tick(1)
            canary = e.one("select * from changes where kind='canary'")
            if planted:
                self.assertIn("buffer not cleared", canary["defect"])
                self.assertIn("b->data[k] = 1;", canary["diff"])
            else:
                self.assertIsNone(canary["defect"])
                self.assertEqual(canary["diff"], DIFF)


class SmallTeamTest(unittest.TestCase):
    def test_a_small_project_needs_no_more_reviewers_than_it_has(self):
        amber = DIFF.replace("b->data[i] = c;", "memcpy(b->data, &c, 1);")
        for members in (["tib", "newbie"], ["tib", "a", "b"]):
            e = Engine(config={"canary_every": 1e9})
            for m in members:
                e.join(m, 0)
            cid = change_of(e, members[1], diff=amber, now=H)
            for t in range(1, 10):
                e.tick(t * H)
                for row in e.q("select id, reviewer from assignments where change_id=? and verdict is null", cid):
                    e.verdict(row["reviewer"], row["id"], "approve", t * H + 60, reason="read it")
            change = e.one("select tier, state from changes where id=?", cid)
            self.assertEqual((change["tier"], change["state"]), ("amber", "approved"), members)


class IdCollisionTest(unittest.TestCase):
    def test_reviewing_a_task_proposal_does_not_bar_a_change_with_the_same_number(self):
        e = Engine(config={"canary_every": 1e9})
        e.join("maint", 0)
        e.join("alice", 0)
        first = e.add_task("Add a clamp helper", "green", 0)
        e.add_task("Document the build", "green", 60, proposer="alice")
        e.tick(120)
        proposal = e.one("select * from assignments")
        e.verdict(proposal["reviewer"], proposal["id"], "approve", 130)
        e.claim("alice", first, 200)
        change = change_of(e, "alice", now=250)
        e.tick(300)
        self.assertEqual([r["reviewer"] for r in e.q("select reviewer from assignments where change_id=?", change)],
                         ["maint"])
