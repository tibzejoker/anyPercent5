import json, statistics as S, sys, collections as C
res = json.load(open(sys.argv[1]))
g = C.defaultdict(list)
for name, sc, prof, seed, r, t in res:
    g[(f"{name}:{sc}", prof)].append(r)
designs = sorted({k[0] for k in g}, key=lambda d: (d != "proposal:proposal", d))
mean = lambda rs, k: S.mean(r[k] for r in rs)
print(f"{'design':26} {'merged/d':>8} {'h':>4} | " + " | ".join(f"{p:^24}" for p in ("eager", "standard", "patient")))
print(f"{'':26} {'':>8} {'':>4} | " + " | ".join(f"{'live':>6}{'bd-days':>9}{'/1k mrg':>9}" for _ in range(3)))
for d in designs:
    row = []
    for p in ("eager", "standard", "patient"):
        rs = g[(d, p)]
        mpd = mean(rs, "merged/day (last 7 d)")
        bdd = mean(rs, "backdoor-days live")
        row.append(f"{mean(rs, 'still live at end'):6.0f}{bdd:9.0f}{1000 * bdd / (mpd * 90):9.2f}")
    rs = g[(d, "standard")]
    print(f"{d:26} {mean(rs, 'merged/day (last 7 d)'):8.0f} {mean(rs, 'median hours to merge'):4.0f} | " + " | ".join(row))
print("\nhonest banned (std):", {d: round(mean(g[(d, 'standard')], 'honest banned')) for d in designs})
print("attempts (std):", {d: round(mean(g[(d, 'standard')], 'backdoor attempts')) for d in designs})
