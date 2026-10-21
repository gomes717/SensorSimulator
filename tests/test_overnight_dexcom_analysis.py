"""Scoring for the overnight Dexcom test (scripts/overnight_dexcom_analysis.py): pure functions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import overnight_dexcom_analysis as oda
from overnight_dexcom_analysis import AppValue, G6Read, Published


def pubs(n=5, start_glucose=100, step=2):
    return [
        Published(seq=i, ts_s=i * 300, glucose=start_glucose + i * step, wall=1000.0 + i * 300)
        for i in range(n)
    ]


def reads_for(ps, latency=10.0):
    return [G6Read(wall=p.wall + latency, seq=p.seq, ts_s=p.ts_s, glucose=p.glucose) for p in ps]


def apps_for(ps, latency=1.0):
    return [AppValue(wall=p.wall + latency, glucose=float(p.glucose)) for p in ps]


def test_a_clean_sequence_has_no_problems():
    assert oda.score_publications(pubs()) == []


def test_a_skipped_seq_a_wrong_step_and_a_jump_are_reported():
    ps = pubs(4)
    ps[2] = Published(seq=3, ts_s=900, glucose=ps[2].glucose, wall=ps[2].wall)  # skipped seq 2
    ps[3] = Published(seq=4, ts_s=1200, glucose=300, wall=ps[3].wall)  # jumped
    text = " | ".join(oda.score_publications(ps))
    assert "seq jumped 1 -> 3" in text and "glucose moved" in text and "stepped 600" in text


def test_a_restarted_grid_is_not_a_sequence_error():
    ps = [*pubs(3), Published(seq=0, ts_s=0, glucose=99, wall=5000.0)]
    assert oda.score_publications(ps) == []


def test_glucose_outside_40_to_400_is_reported():
    ps = [Published(0, 0, 20, 1.0), Published(1, 300, 120, 2.0)]
    assert any("out of range" in p for p in oda.score_publications(ps))


def test_every_publication_read_once_and_correct_is_full_coverage():
    ps = pubs()
    score = oda.score_reads(ps, reads_for(ps))
    assert score.coverage == 1.0 and score.problems == []
    assert max(score.latencies_s) == 10.0


def test_a_missed_wrong_late_or_unknown_read_is_reported():
    ps = pubs(4)
    rs = reads_for(ps)
    del rs[1]  # seq 1 never read
    rs[1] = G6Read(wall=rs[1].wall, seq=2, ts_s=600, glucose=ps[2].glucose + 5)  # wrong value
    rs[2] = G6Read(wall=ps[3].wall + 200, seq=3, ts_s=900, glucose=ps[3].glucose)  # far too late
    rs.append(G6Read(wall=1.0, seq=9, ts_s=2700, glucose=100))  # never logged by the board
    text = " | ".join(oda.score_reads(ps, rs).problems)
    assert "seq 1 was never read" in text
    assert "G6 read" in text and "published" in text
    assert "200 s after publication" in text
    assert "never logged" in text


def test_the_first_read_of_a_publication_is_the_one_judged():
    ps = pubs(1)
    rs = [
        G6Read(wall=ps[0].wall + 5, seq=0, ts_s=0, glucose=ps[0].glucose),
        G6Read(wall=ps[0].wall + 25, seq=0, ts_s=0, glucose=ps[0].glucose),
    ]
    score = oda.score_reads(ps, rs)
    assert score.covered == 1 and score.latencies_s == [5.0]


def test_the_app_must_get_each_publication_with_the_same_value():
    ps = pubs(3)
    vals = apps_for(ps)
    assert oda.score_app(ps, vals) == (3, [])
    matched, problems = oda.score_app(ps, [*vals[:1], AppValue(ps[1].wall + 1, 77.0)])
    assert matched == 1
    assert any("never reached the app" in p for p in problems)
    assert any("the app got [77.0]" in p for p in problems)


def test_the_console_parser_finds_readings_boots_and_anomalies():
    text = "\n".join(
        [
            "Starting Bluetooth Peripheral CGM simulator (3 sensors)",
            "comm_thread: dexcom reading seq=0 ts=0s glucose=99 trend=127",
            "dexcom: unknown op 0x77 len 3",
            "comm_thread: dexcom reading seq=1 ts=300s glucose=101 trend=3",
            "Starting Bluetooth Peripheral CGM simulator (3 sensors)",
        ]
    )
    readings, boots, anomalies = oda.parse_console(text)
    assert readings == [(0, 0, 99), (1, 300, 101)]
    assert boots == 2
    assert anomalies == ["dexcom: unknown op 0x77 len 3"]


def test_the_latency_summary_handles_empty_and_typical_input():
    assert oda.latency_summary([]) == "n/a"
    assert "median 10.0 s" in oda.latency_summary([5.0, 10.0, 15.0])


def drop(wall=2000.0, recovered_after=4.0, error="OSError: [WinError -2147483629] closed"):
    d = oda.LinkDrop(wall, error, "mode read")
    if recovered_after is not None:
        d.recovered_wall = wall + recovered_after
    return d


def test_a_quick_closed_object_drop_excuses_the_readings_published_while_it_was_down():
    d = drop(wall=2000.0, recovered_after=8.0)
    assert d.tolerated and d.recovered_s == 8.0
    assert d.excuses(2005.0) and d.excuses(1985.0)  # the app expects a value up to 20 s later
    assert not d.excuses(2300.0) and not d.excuses(1900.0)


def test_a_slow_unrecovered_or_other_drop_excuses_nothing():
    assert not drop(recovered_after=60.0).tolerated
    assert not drop(recovered_after=None).tolerated
    assert not drop(error="").tolerated  # the board hung up, or something else
    assert not drop(recovered_after=60.0).excuses(2005.0)


def test_closed_object_drops_are_scored_on_recovery_and_rate():
    assert oda.score_drops([drop()], hours=1.0) == []
    assert oda.score_drops([drop(error="")], hours=1.0) == []  # judged by app coverage only
    assert "not recovered" in oda.score_drops([drop(recovered_after=40.0)], hours=5.0)[0]
    many = [drop(wall=2000.0 + 600 * i) for i in range(3)]
    assert "3 closed-object drops" in oda.score_drops(many, hours=1.0)[0]
    assert oda.score_drops(many, hours=2.0) == []  # 1.5 per hour is within the limit


def test_a_drop_describes_itself_for_the_report():
    d = drop()
    d.since_xdrip_s = 12.0
    text = d.describe()
    assert "mode read" in text and "back after 4.0 s" in text and "12 s after" in text


# ---- the app-like link's reconnect policy (scripts/overnight_dexcom.py) --------------------------


def test_the_app_link_reconnects_at_once_after_a_closed_object_and_waits_after_other_drops(
    monkeypatch,
):
    import asyncio
    import contextlib

    import overnight_dexcom as od

    state = od.State()
    outcomes = [
        OSError("[WinError -2147483629] O objeto foi fechado."),  # while up: reconnect at once
        None,  # the board hung up: wait first
        asyncio.CancelledError(),  # ends the loop
    ]
    sleeps: list[float] = []

    async def fake_session(st):
        st.app_step = "mode read" if len(outcomes) == 3 else "idle"
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(od, "app_session", fake_session)
    monkeypatch.setattr(od.asyncio, "sleep", fake_sleep)
    with contextlib.suppress(asyncio.CancelledError):
        asyncio.run(od.app_link(state))
    assert [d.closed_object for d in state.drops] == [True, False]
    assert state.drops[0].where == "mode read"
    assert sleeps == [5.0]  # only the second drop waited
