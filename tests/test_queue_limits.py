"""The queue-limit experiment runs end to end, in parallel, on tiny parameters."""
from auxocase.data.queue_limits import markdown, run_table


def test_queue_limit_table_runs_each_setup_and_seed(tmp_path):
    runs, summary = run_table(seeds=1, processes=2, configs=[(1.0, 3), (0.5, 1)],
                              history_months=8, days=2, new_leads=150, workdir=tmp_path)
    assert [(r["freshness"], r["limit"], r["seed"]) for r in runs] == [("off", 3, 101), ("on", 1, 101)]
    for r in runs:
        assert r["worked"] > 0 and 0 <= r["touched_within_24h"] <= r["worked"] and r["booked"] <= r["worked"]
    assert [s["limit"] for s in summary] == [3, 1]
    assert all(0 <= s["within_24h_min"] <= s["within_24h_mean"] <= s["within_24h_max"] <= 1 for s in summary)
    # each run worked on its own copy with its own model folder, never the base
    assert (tmp_path / "f1.0-l3-s101" / "models").is_dir() and (tmp_path / "f0.5-l1-s101" / "run.db").exists()
    table = markdown(summary)
    assert table.splitlines()[0] == "| Freshness discount | Queue limit | Touched within 24 h | Demos |"
    assert len(table.splitlines()) == 4
