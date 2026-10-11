"""False rejects: what rejecting honest work costs, in the baseline and in any%5 (v0.2 and v0.3 review rules).

python false_rejects.py [agents] [days] [seeds]      (defaults 2000 30 4: about a minute on 32 threads)
python false_rejects.py --report [--ablation]         (prints the table of the saved json)
python false_rejects.py [agents] [days] [seeds] --ablation   (v0.3 with one rule reverted at a time)

Builds build/anyp5_fr.py from anyp5_sim.py with these additions (all off by default):
- honest reviewers reject a change they found nothing wrong in with probability fr_diligent / fr_careless;
- a reject-all ring (share `rejecters`, rings of 5): rejects every real change not written by the ring, approves its own
  ring's changes, and on a canary looks for the planted line (it cannot tell canaries apart) and rejects anyway;
- a closed change by a non-attacker is reworked by its author (rework x a fresh implementation time, then re-review),
  at most `max_resubmit` times; after that, or if the author leaves, the work is lost;
- any%5 v0.3 rules from anyp5/engine.py: 40 % of canaries are clean and scored on approval, a planted canary counts
  only if the reject names the line, score = (hits+1)/(seen+2) x (clean ok+1)/(clean seen+2), reviews drawn at random
  among the better-calibrated half of idle members, a lone reject asks one more approval, 2 rejects close a change,
  a reject claiming a backdoor holds the change until 2 auditors answer and bans the author if both confirm.
"""
import importlib.util
import json
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PATCHES = [
    # --- members: a reject-all kind, a rework queue, two-sided calibration counters
    ('                 "cal_n", "cal_hit", "born")\n',
     '                 "cal_n", "cal_hit", "born", "redo", "cal_cn", "cal_ok")\n'),
    ('        self.kind = "malicious" if r < cfg["malicious"] else "colluder" if r < cfg["malicious"] + cfg["colluders"] else "honest"\n'
     '        self.ring = ring_of(self) if self.kind == "colluder" else None\n',
     '        self.kind = "malicious" if r < cfg["malicious"] else "colluder" if r < cfg["malicious"] + cfg["colluders"] else \\\n'
     '            "rejecter" if r < cfg["malicious"] + cfg["colluders"] + cfg.get("rejecters", 0) else "honest"\n'
     '        self.ring = ring_of(self) if self.kind == "colluder" else None\n'
     '        if self.kind == "rejecter":\n'
     '            cnt = cfg.setdefault("_rring", [0])\n'
     '            self.ring = ("R", cnt[0] // cfg["ring"])\n'
     '            cnt[0] += 1\n'),
    ('        self.born = 0\n',
     '        self.born = 0\n        self.redo = []\n        self.cal_cn = self.cal_ok = 0\n'),
    # --- changes: rejects, escalation hold, first submission time, resubmission count, clean canary
    ('                 "approvals", "merged_at", "found_at", "rejected", "checks", "task")\n',
     '                 "approvals", "merged_at", "found_at", "rejected", "checks", "task", "rejects", "hold", "esc",\n'
     '                 "origin", "resub", "clean")\n'),
    ('        self.task = None\n',
     '        self.task = None\n        self.rejects = self.hold = self.resub = 0\n        self.esc = None\n'
     '        self.origin = now\n        self.clean = False\n'),
    ('            return (a.cal_hit + prior[0]) / (a.cal_n + prior[0] + prior[1])\n',
     '            if syn.get("two_sided"):\n'
     '                return (a.cal_hit + 1) / (a.cal_n + 2) * (a.cal_ok + 1) / (a.cal_cn + 2)\n'
     '            return (a.cal_hit + prior[0]) / (a.cal_n + prior[0] + prior[1])\n'),
    ('    dag = DAG(cfg, rng, m) if cfg.get("dag") else None\n',
     '    dag = DAG(cfg, rng, m) if cfg.get("dag") else None\n'
     '    FR = dict(real=0, ring_reviews=0, hc=0, hc_closed=0, hc_hit=0, hc_rejects=0, items=0, lost=0, e2e=[],\n'
     '              esc=0, esc_bans=0, closed_other=0)\n'),
    # --- resubmissions keep tier and origin, and attackers only are attackers
    ('    def new_pr(author, now, task=None):\n', '    def new_pr(author, now, task=None, redo=None):\n'),
    ('        if a.kind != "honest" and now - JOINED.get(a.id, 0) >= cfg["patience_h"] and rng.random() < cfg["attack_rate"]:\n',
     '        if redo is not None:\n'
     '            tier, vis = redo[1], VISIBILITY[redo[1]]\n'
     '        if redo is None and a.kind in ("malicious", "colluder") and now - JOINED.get(a.id, 0) >= cfg["patience_h"] \\\n'
     '                and rng.random() < cfg["attack_rate"]:\n'),
    ('        elif rng.random() < cfg["honest_bug"]:\n',
     '        elif redo is None and rng.random() < cfg["honest_bug"]:\n'),
    ('        pr.task = task\n',
     '        pr.task = task\n'
     '        if redo is not None:\n'
     '            pr.origin, pr.resub = redo[0], redo[2]\n'
     '        elif a.kind == "honest":\n'
     '            FR["items"] += 1\n'),
    # --- clean canaries
    ('        pr = PR(len(prs), -1, "green", "backdoor", vis, now, canary=True)\n',
     '        pr = PR(len(prs), -1, "green", "backdoor", vis, now, canary=True)\n'
     '        if cfg.get("clean_share") and rng.random() < cfg["clean_share"]:\n'
     '            pr.flaw, pr.clean = None, True\n'),
    ('        lo, hi = cfg["audit_hours"] if work[0] in ("audit", "sweep") else cfg["review_hours"]\n',
     '        lo, hi = cfg["audit_hours"] if work[0] in ("audit", "sweep", "escalation") else cfg["review_hours"]\n'),
    # --- reviewing: false rejects, the ring, v0.3 reject rules
    ('    def finish_review(agent, pr, now):\n        agent.credits += 1\n',
     '''    def fr(agent):
        if agent.kind == "rejecter":
            return 1.0
        return cfg.get("fr_careless", 0.0) if agent.diligence < 0.5 else cfg.get("fr_diligent", 0.0)

    def canary_hit(agent, pr):
        if agent.kind == "rejecter":
            return detects(agent, pr) if cfg.get("name_line") else True
        if detects(agent, pr):
            return True
        return not cfg.get("name_line") and rng.random() < fr(agent)

    def honest_clean(pr):
        return pr.author >= 0 and pr.flaw is None and agents[pr.author].kind == "honest"

    def close(pr, now):
        pr.rejected = True
        if dag and pr.task is not None:
            dag.rework(pr.task, now)
        a = agents[pr.author]
        if honest_clean(pr):
            FR["hc"] += 1
            FR["hc_closed"] += 1
            FR["hc_hit"] += 1
        elif a.kind == "honest":
            FR["closed_other"] += 1
        if pr.flaw == "backdoor" or a.kind in ("malicious", "colluder") or not cfg.get("resubmit"):
            if a.kind == "honest":
                FR["lost"] += 1
            return
        if pr.resub >= cfg.get("max_resubmit", 2) or a.gone or a.banned:
            if a.kind == "honest":
                FR["lost"] += 1
            return
        a.redo.append((pr.origin, pr.tier, pr.resub + 1))

    def reject(agent, pr, now, claim=False):
        pr.rejects += 1
        if honest_clean(pr):
            FR["hc_rejects"] += 1
        if claim and cfg.get("escalation"):
            if pr.esc is None:
                pr.esc, pr.hold = [], 1
                FR["esc"] += 1
                slots.appendleft((pr, "escalation"))
                slots.appendleft((pr, "escalation"))
            pr.pending += 1
            slots.append((pr, "review"))
            slots.append((pr, "review"))
            return
        if cfg.get("lone_reject") and pr.rejects < 2:
            pr.pending += 1
            slots.append((pr, "review"))
            slots.append((pr, "review"))
            return
        close(pr, now)

    def finish_escalation(agent, pr, now):
        agent.credits += cfg["audit_credit"]
        m["verdicts"] += 1
        author = agents[pr.author]
        confirm = (agent.kind == "rejecter" and author.ring != agent.ring) or (
            pr.flaw == "backdoor" and pr.found_at is None and detects(agent, pr, careful=True))
        pr.esc.append(confirm)
        if len(pr.esc) < 2:
            return
        pr.hold = 0
        if all(pr.esc) and not author.banned:
            if pr.flaw == "backdoor":
                found(pr, now, "audit")
            else:
                FR["esc_bans"] += author.kind == "honest"
                if not pr.rejected:
                    close(pr, now)
                slash(pr.author, now)
            return
        try_merge(pr, now)

    def finish_review(agent, pr, now):
        agent.credits += 1
'''),
    ('''        if pr.canary and syn and syn["cal_vis"]:
            agent.cal_n += 1
            if detects(agent, pr):
''', '''        if pr.canary and syn and syn["cal_vis"]:
            if pr.clean:
                agent.cal_cn += 1
                if rng.random() >= fr(agent):
                    agent.cal_ok += 1
                    agent.trust += 1
                else:
                    agent.trust -= 4
                return
            agent.cal_n += 1
            if canary_hit(agent, pr):
'''),
    ('            if rng.random() < agent.diligence and rng.random() < 0.9:\n',
     '            if agent.kind == "rejecter" or rng.random() < agent.diligence and rng.random() < 0.9:\n'),
    ('''        if detects(agent, pr):
            if syn and syn["review_evidence"]:
                agent.cal_hit += syn["review_evidence"]
            pr.rejected = True
            if dag and pr.task is not None:
                dag.rework(pr.task, now)
            if pr.flaw == "backdoor":
                found(pr, now, "review")
            return
        pr.approvals.append(agent.id)
        pr.done += 1
        if pr.done >= pr.pending:
''', '''        FR["real"] += 1
        mate = agent.kind == "rejecter" and agents[pr.author].ring == agent.ring
        if agent.kind == "rejecter":
            FR["ring_reviews"] += 1
            if not mate:
                reject(agent, pr, now, claim=cfg.get("claim_backdoor", False))
                return
        if not mate and detects(agent, pr):
            if syn and syn["review_evidence"]:
                agent.cal_hit += syn["review_evidence"]
            if pr.flaw == "backdoor":
                pr.rejected = True
                if dag and pr.task is not None:
                    dag.rework(pr.task, now)
                found(pr, now, "review")
            else:
                reject(agent, pr, now)
            return
        if not mate and rng.random() < fr(agent):
            reject(agent, pr, now)
            return
        pr.approvals.append(agent.id)
        pr.done += 1
        try_merge(pr, now)

    def try_merge(pr, now):
        if pr.done < pr.pending or pr.hold or pr.rejected or pr.merged_at is not None or pr.found_at is not None:
            return
        if honest_clean(pr):
            FR["hc"] += 1
            FR["hc_hit"] += pr.rejects > 0
            FR["e2e"].append(now - pr.origin)
        if True:
'''),
    # --- work dispatch: reworks and escalations
    ('                if work[0] == "impl":\n',
     '                if work[0] == "redo":\n'
     '                    new_pr(a.id, now, redo=work[1])\n'
     '                elif work[0] == "escalation":\n'
     '                    finish_escalation(a, work[1], now)\n'
     '                elif work[0] == "impl":\n'),
    ('        for a in idle:\n            if a.id not in taken and a.job is None and a.credits',
     '        for a in idle:\n'
     '            if a.id not in taken and a.job is None and a.redo and not dag:\n'
     '                a.job = [("redo", a.redo.pop(0)), cfg.get("rework", 0.3) * rng.uniform(*cfg["impl_hours"]),\n'
     '                         now + cfg["lease_review"]]\n'
     '                continue\n'
     '            if a.id not in taken and a.job is None and a.credits'),
    # --- v0.3 draws: at random among the better-calibrated half; canaries to the least tested
    ('                jur.sort(key=score)\n',
     '                jur.sort(key=score)\n'
     '            if syn.get("half"):\n'
     '                jur.sort(key=score, reverse=True)\n'),
    ('                cand = pool.pop()\n',
     '                if syn and syn.get("half") and not pr.canary:\n'
     '                    cand = pool.pop(rng.randrange((len(pool) + 1) // 2))\n'
     '                else:\n'
     '                    cand = pool.pop()\n'),
    ('            if syn["canary_to_least"] == "confirm":\n',
     '            if syn.get("least_tested"):\n'
     '                canary_pool = sorted((a for a in idle if a.id not in taken), key=lambda a: -(a.cal_n + a.cal_cn))\n'
     '            elif syn["canary_to_least"] == "confirm":\n'),
    ('    if work[0] in ("review", "audit", "sweep"):\n',
     '    if work[0] == "redo" and agent is not None:\n'
     '        agent.redo.append(work[1])\n'
     '    if work[0] in ("review", "audit", "sweep", "escalation"):\n'),
    ('        "reviews waiting at end": len(slots),\n',
     '        "reviews waiting at end": len(slots),\n'
     '        "_fr": dict(FR, e2e=None, lost=FR["lost"] + sum(len(a.redo) for a in agents if a.kind == "honest"\n'
     '                                                         and (a.gone or a.banned)),\n'
     '                    e2e_med=statistics.median(FR["e2e"]) if FR["e2e"] else None,\n'
     '                    e2e_p90=sorted(FR["e2e"])[int(0.9 * (len(FR["e2e"]) - 1))] if FR["e2e"] else None),\n'),
]

FR_RATES = dict(fr_diligent=0.03, fr_careless=0.10)
COMMON = dict(resubmit=True, max_resubmit=2, rework=0.3)
V03 = dict(clean_share=0.4, name_line=True, lone_reject=True, escalation=True,
           syn_extra=dict(rank=False, half=True, two_sided=True, least_tested=True))
SYSTEMS = {
    "Baseline: trust levels + credits": ("proposal", {}),
    "any%5, v0.2 review rules": ("synth", dict(escalation=True)),
    "any%5, v0.3 review rules": ("synth", V03),
}
POPULATIONS = {
    "no false rejects (old model)": {},
    "honest false rejects 3 % / 10 %": dict(FR_RATES),
    "+ reject-all ring, 5 % of members": dict(FR_RATES, rejecters=0.05),
    "+ reject-all ring that claims backdoors": dict(FR_RATES, rejecters=0.05, claim_backdoor=True),
}
ABLATION = {
    "any%5, v0.2 review rules": SYSTEMS["any%5, v0.2 review rules"],
    "any%5, v0.3 review rules": SYSTEMS["any%5, v0.3 review rules"],
    "v0.3 but ranked draw (best first)": ("synth", dict(V03, syn_extra=dict(V03["syn_extra"], rank=True, half=False))),
    "v0.3 but one-sided canaries": ("synth", dict(V03, clean_share=0, name_line=False,
                                                  syn_extra=dict(V03["syn_extra"], two_sided=False))),
    "v0.3 but one reject closes": ("synth", dict(V03, lone_reject=False)),
    "v0.3 but canaries confirm perfect records first (v0.2 routing)": (
        "synth", dict(V03, syn_extra=dict(V03["syn_extra"], least_tested=False))),
}


def build():
    src = (ROOT / "anyp5_sim.py").read_text(encoding="utf-8")
    for old, new in PATCHES:
        assert src.count(old) == 1, (old[:70], src.count(old))
        src = src.replace(old, new)
    (ROOT / "build").mkdir(exist_ok=True)
    out = ROOT / "build" / "anyp5_fr.py"
    out.write_text(src, encoding="utf-8")
    return out


def job(args):
    system, population, seed, agents, days = args
    spec = importlib.util.spec_from_file_location("anyp5_fr", ROOT / "build" / "anyp5_fr.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    scenario, rules = {**SYSTEMS, **ABLATION}[system]
    rules = dict(rules)
    cfg = dict(mod.BASE, **mod.SCENARIOS[scenario], **COMMON, **POPULATIONS[population])
    extra = rules.pop("syn_extra", None)
    cfg.update(rules)
    if extra:
        cfg["syn"] = dict(cfg["syn"], **extra)
    cfg.update(agents=agents, days=days, seed=seed, patience_h=3 * 24)
    t = time.time()
    r = mod.simulate(cfg)
    r.pop("_x", None)
    r.update({f"shipped soak {d}d": v for d, v in mod._SHIP.items()})
    return (system, population, seed, r, round(time.time() - t, 1))


def report(data):
    rows, meta = data["rows"], data["setup"]
    groups = {}
    for system, population, _, r, _ in rows:
        groups.setdefault((population, system), []).append(r)
    mean = lambda rs, f: statistics.mean(f(r) for r in rs)
    pct = lambda a, b: 100 * a / b if b else 0.0
    lines = ["| Population | System | Merged/day | Median h to merge (one PR) | Honest work: median / p90 h to merge "
             "| Honest clean PRs rejected | ... with at least one reject | Honest work lost | Ring share of real reviews "
             "| Honest members banned | Backdoors shipped per attempt (14-day soak) |",
             "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|"]
    for population, system in groups:
        rs = groups[(population, system)]
        f = lambda k: mean(rs, lambda r: r["_fr"][k] or 0)
        ship = mean(rs, lambda r: r["shipped soak 14d"]) / max(1e-9, mean(rs, lambda r: r["backdoor attempts"]))
        sd = statistics.stdev(r["shipped soak 14d"] / max(1, r["backdoor attempts"]) for r in rs) if len(rs) > 1 else 0
        lines.append(
            f"| {population} | {system} | {mean(rs, lambda r: r['merged/day (last 7 d)']):.0f} "
            f"| {mean(rs, lambda r: r['median hours to merge']):.1f} | {f('e2e_med'):.1f} / {f('e2e_p90'):.0f} "
            f"| {mean(rs, lambda r: pct(r['_fr']['hc_closed'], r['_fr']['hc'])):.1f} % "
            f"| {mean(rs, lambda r: pct(r['_fr']['hc_hit'], r['_fr']['hc'])):.1f} % "
            f"| {mean(rs, lambda r: pct(r['_fr']['lost'], r['_fr']['items'])):.2f} % "
            f"| {mean(rs, lambda r: pct(r['_fr']['ring_reviews'], r['_fr']['real'])):.1f} % "
            f"| {mean(rs, lambda r: r['honest banned']):.1f} | {100 * ship:.1f} % (sd {100 * sd:.1f}) |")
    head = (f"{meta['agents']} members, {meta['days']} days, seeds {meta['seeds']}, standard attacker (malicious after "
            f"3 days); wall time {meta['wall_s']} s, slowest run {meta['max_run_s']} s.")
    return head + "\n\n" + "\n".join(lines)


if __name__ == "__main__":
    ablation = "--ablation" in sys.argv
    if ablation:
        sys.argv.remove("--ablation")
    out = ROOT / ("false_rejects_ablation.json" if ablation else "false_rejects.json")
    if sys.argv[1:2] == ["--report"]:
        print(report(json.loads(out.read_text(encoding="utf-8"))))
        sys.exit()
    agents = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    seeds = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    build()
    t = time.time()
    plan = [(p, s) for p in POPULATIONS for s in SYSTEMS]
    if ablation:
        plan = [("honest false rejects 3 % / 10 %", s) for s in ABLATION]
    jobs = [(s, p, seed, agents, days) for p, s in plan for seed in range(1, seeds + 1)]
    with Pool(min(32, len(jobs))) as pool:
        rows = pool.map(job, jobs, chunksize=1)
    data = dict(setup=dict(agents=agents, days=days, seeds=list(range(1, seeds + 1)), wall_s=round(time.time() - t),
                           max_run_s=max(r[4] for r in rows),
                           command=f"python false_rejects.py {agents} {days} {seeds}" + (" --ablation" if ablation else ""),
                           fr_rates=FR_RATES, common=COMMON, v03=V03),
                rows=rows)
    out.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(report(data))
