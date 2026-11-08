import asyncio
import hashlib
import json
import re
from pathlib import Path

import pytest

from eval.scoring import pipeline
from eval.scoring.data import Run, parse_board_output, render_finding
from eval.scoring.martian import step3_judge_comments as m3
from eval.scoring.metrics import bootstrap, counts, per_pr, summarize
from eval.scoring.views import View, panel, parse_view, score, select

VENDORED = Path(__file__).resolve().parents[1] / "martian"


def test_vendored_files_unchanged():
    table = (VENDORED / "VENDORED.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([^`]+)` \| `[^`]+` \| `([0-9a-f]{64})` \|$", table, re.M)
    assert len(rows) == 5
    for name, digest in rows:
        assert hashlib.sha256((VENDORED / name).read_bytes()).hexdigest() == digest, name


def test_parse_board_output_repairs():
    assert parse_board_output('{"a": 1}') == {"a": 1}
    assert parse_board_output('"a": 1}') == {"a": 1}
    assert parse_board_output('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_board_output('```\n"a": 1}\n```') == {"a": 1}


def test_render_finding():
    assert render_finding({"file": "a.py", "line": 3, "text": "x is None here"}) == "`a.py:3`: x is None here"
    assert render_finding({"file": None, "line": None, "text": "general"}) == "general"


def _record():
    golden = [
        {"comment": "G-sql", "severity": "High", "category": "security"},
        {"comment": "G-off", "severity": "High", "category": "bug"},
        {"comment": "G-style", "severity": "Low", "category": "style"},
    ]
    cands = [
        {"text": "c0 sql injection", "finding": "sab-1", "member": "saboteur", "label": "CONFIRMED"},
        {"text": "c1 off by one", "finding": "edge-1", "member": "edge", "label": "CONFIRMED"},
        {"text": "c2 off by one again", "finding": "intent-1", "member": "intent", "label": "PLAUSIBLE"},
        {"text": "c3 noise", "finding": "nit-1", "member": "nitpicker", "label": "CONFIRMED"},
        {"text": "c4 refuted sql", "finding": "edge-2", "member": "edge", "label": "REFUTED"},
    ]
    truth = {("G-sql", 0): 0.9, ("G-sql", 4): 0.8, ("G-off", 1): 0.9, ("G-off", 2): 0.7}
    verdicts = [
        [{"match": (g["comment"], i) in truth, "confidence": truth.get((g["comment"], i), 0.1), "reasoning": ""}
         for i in range(len(cands))]
        for g in golden
    ]
    return {"golden": golden, "candidates": cands, "groups": [[0, 4], [1, 2], [3]], "verdicts": verdicts,
            "pr_id": "p1", "repeat": 1, "dedup_fallback": False,
            "findings": [{"id": c["finding"], "member": c["member"], "label": c["label"], "group": None} for c in cands]}


class _TableJudge:
    def __init__(self, rec):
        texts = [c["text"] for c in rec["candidates"]]
        self.t = {(g["comment"], texts[i]): v for g, row in zip(rec["golden"], rec["verdicts"]) for i, v in enumerate(row)}

    async def match_comment(self, g, c):
        return self.t[(g, c)]


@pytest.mark.parametrize("spec", ["board:full:raw", "board:full:cp", "board:full:confirmed", "board:edge,intent:cp",
                                  "board:saboteur:raw", "board:nitpicker:cp"])
def test_view_matches_martian_on_subset(spec):
    rec = _record()
    view = parse_view(spec)
    idx, groups = select(rec, view)
    texts = [rec["candidates"][i]["text"] for i in idx]
    direct = asyncio.new_event_loop().run_until_complete(
        m3.evaluate_review(_TableJudge(rec), rec["golden"], texts, groups))
    assert score(rec, view) == direct


def test_dedup_groups_restricted():
    rec = _record()
    idx, groups = select(rec, View("v", "board-full", frozenset({"edge"}), None))
    assert idx == [1, 4]
    assert groups == [[1], [0]]


def test_scores_by_view():
    rec = _record()
    raw = score(rec, parse_view("board:full:raw"))
    assert (raw["tp"], raw["fp"], raw["fn"]) == (2, 1, 1)  # c3 is the one false positive
    cp = score(rec, parse_view("board:full:cp"))
    assert (cp["tp"], cp["fp"]) == (2, 1)
    edge_cp = score(rec, parse_view("board:edge:cp"))
    assert (edge_cp["tp"], edge_cp["fp"], edge_cp["total_candidates"]) == (1, 0, 1)
    assert counts(raw, "strict") == (2, 1, 0)  # the style miss is outside Strict


def test_panel_majority():
    def r(found, fp):
        names = ["a", "b"]
        return {"true_positives": [{"golden_comment": n, "category": "bug"} for n in names if n in found],
                "false_negatives": [{"golden_comment": n, "category": "bug"} for n in names if n not in found], "fp": fp}
    p = panel([r({"a", "b"}, 1), r({"a"}, 3), r(set(), 2)])
    assert [t["golden_comment"] for t in p["true_positives"]] == ["a"]
    assert p["fp"] == 2


def test_metrics_and_bootstrap():
    res = {("p1", 1): {"true_positives": [{"category": "bug"}], "false_negatives": [], "fp": 1},
           ("p1", 2): {"true_positives": [], "false_negatives": [{"category": "bug"}], "fp": 0},
           ("p2", 1): {"true_positives": [{"category": "bug"}], "false_negatives": [{"category": "style"}], "fp": 0}}
    u = per_pr(res, "core")
    assert u == {"p1": (0.5, 0.5, 0.5), "p2": (1.0, 0.0, 0.0)}
    s = summarize(u)
    assert s["precision"] == pytest.approx(1.5 / 2.0) and s["recall"] == pytest.approx(1.5 / 2.0)
    same = bootstrap(u, u, n=200)
    assert same["point"] == 0 and same["se"] == 0 and same["p_two_sided"] is None


class FakeJudge:
    model = "fake-model"
    fingerprint = "f1"
    served = {"id": "fake-model"}

    async def extract(self, body):
        return [line for line in body.splitlines() if line.startswith("- ")] or [body.split("\n")[0]]

    async def dedup(self, texts):
        return ([[i] for i in range(len(texts))], False) if len(texts) > 1 else (None, False)

    async def match(self, golden, candidate):
        return {"match": golden.split()[0].lower() in candidate.lower(), "confidence": 0.9, "reasoning": "r"}


def test_judge_run_board_and_code_review(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESULTS", tmp_path / "results")
    golden = [{"comment": "Injection in search", "severity": "High", "category": "security"}]
    board = {"members": {"ran": ["saboteur", "edge"]}, "findings": [
        {"id": "saboteur-1", "member": "saboteur", "group": "G1", "label": "CONFIRMED", "file": "u.py", "line": 1,
         "claim": "SQL injection in search", "severity": "Critical", "category": "security"},
        {"id": "edge-1", "member": "edge", "group": "G2", "label": "REFUTED", "file": "u.py", "line": 2,
         "claim": "empty list crash", "severity": "High", "category": "bug"}]}
    bdir = tmp_path / "board"
    bdir.mkdir()
    (bdir / "final.json").write_text(json.dumps(board)[1:], encoding="utf-8")  # missing "{", as seen in the smoke test
    cdir = tmp_path / "cr"
    cdir.mkdir()
    (cdir / "final.md").write_text("Reported 2 findings.", encoding="utf-8")
    report = [{"file": "u.py", "line": 1, "summary": "injection via f-string"},
              {"file": "u.py", "line": 9, "summary": "unused import"}]
    stream = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "ReportFindings",
                                                       "input": {"findings": [{"summary": "rejected call"}]}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "ReportFindings",
                                                       "input": {"findings": report}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}},
    ]
    (cdir / "stream.jsonl").write_text("\n".join(json.dumps(x) for x in stream), encoding="utf-8")
    loop = asyncio.new_event_loop()
    out = loop.run_until_complete(pipeline.judge_run(Run("d", "board-full", "p1", 1, bdir, {}), golden, FakeJudge()))
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert [c["member"] for c in rec["candidates"]] == ["saboteur", "edge"]
    assert rec["candidates"][0]["text"] == "`u.py:1`: SQL injection in search"  # the finding itself, not extracted
    assert score(rec, parse_view("board:full:cp"))["tp"] == 1
    assert score(rec, parse_view("board:edge:raw"))["tp"] == 0
    out = loop.run_until_complete(pipeline.judge_run(Run("d", "code-review", "p1", 1, cdir, {}), golden, FakeJudge()))
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert [c["finding"] for c in rec["candidates"]] == ["cr-1", "cr-2"]  # the rejected first call is ignored
    res = score(rec, parse_view("code-review"))
    assert (res["tp"], res["fp"]) == (1, 1)
    out = loop.run_until_complete(pipeline.judge_run(Run("d", "code-review", "p1", 1, cdir, {}), golden, FakeJudge(), whole=True))
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["variant"] == "code-review@whole" and len(rec["candidates"]) == 1


def test_member_label_and_selection():
    from eval.scoring.board import label_stats, member_stats, select_default

    rec = _record()
    stats = {s["member"]: s for s in member_stats([rec], "core", "cp")}
    assert list(stats) == ["saboteur", "edge", "intent", "nitpicker"]
    assert (stats["saboteur"]["found"], stats["saboteur"]["unique"]) == (1, 1)
    assert (stats["edge"]["found"], stats["edge"]["unique"]) == (1, 0)  # intent found it too
    assert (stats["nitpicker"]["candidates"], stats["nitpicker"]["unmatched"]) == (1, 1)
    ls = label_stats([rec], "core")
    assert ls["labels"]["CONFIRMED"] == {"findings": 3, "matched": 2, "precision": 2 / 3}
    assert ls["wrongful_refutation_rate"] == 1.0  # edge-2 was refuted but matches the SQL golden comment
    assert ls["golden_lost_by_adjudication"] == 0  # saboteur's CONFIRMED finding still covers it
    out = select_default([rec], {"p1": (1, 1, 1)}, "core", "cp", n_boot=50)
    assert out["best"]["f1"] == 1.0 and out["chosen"]["members"] == ("saboteur", "edge")


def test_member_cards(tmp_path):
    from eval.scoring.board import member_cards
    from eval.scoring.data import member_categories, subagent_usage

    rec = _record()
    baseline = {("p1", 1): {"true_positives": [{"category": "security"}],
                            "false_negatives": [{"category": "bug"}, {"category": "style"}]}}
    cats = {"saboteur": ["security"], "edge": ["bug"], "intent": ["bug"], "nitpicker": ["style"]}
    cards = {c["member"]: c for c in member_cards([rec], baseline, cats, {"saboteur": 1.0, "edge": 3.0}, "core", n_boot=20)}
    assert (cards["saboteur"]["own_found"], cards["saboteur"]["own_found_baseline"], cards["saboteur"]["own_total"]) == (1, 1, 1)
    assert (cards["edge"]["own_found"], cards["edge"]["own_found_baseline"]) == (1, 0)
    assert cards["saboteur"]["contribution"] > 0  # only its CONFIRMED finding covers G-sql
    assert cards["nitpicker"]["contribution"] < 0  # its one finding is a false positive
    assert cards["edge"]["usage_share"] == 0.75 and cards["intent"]["usage_share"] == 0.0

    assert "security" in member_categories("saboteur") and member_categories("no-such-member") == []
    stream = tmp_path / "stream.jsonl"
    events = [
        {"message": {"content": [{"type": "tool_use", "name": "Agent", "id": "a1",
                                  "input": {"prompt": "Member brief: read /x/members/edge.md"}}]}},
        {"message": {"content": [{"type": "tool_use", "name": "Agent", "id": "a2",
                                  "input": {"subagent_type": "board-review:board-verifier", "prompt": "verify"}}]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "a1"}]},
         "tool_use_result": {"usage": {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 100,
                                       "cache_creation": {"ephemeral_5m_input_tokens": 8}}}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "a2"}]},
         "tool_use_result": {"usage": {"input_tokens": 1, "output_tokens": 0}}},
    ]
    stream.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert subagent_usage(stream) == {"edge": 10 + 10 + 10 + 10, "verifier": 1}


def _fake_endpoint(jm, outcomes):
    """Point jm's judge client at a fake whose calls follow `outcomes`: 'busy', 'down', '500', or 'ok'."""
    import httpx2 as httpx
    import openai

    calls = []
    request = httpx.Request("POST", "http://x")

    class Completions:
        async def create(self, **kwargs):
            outcome = outcomes[min(len(calls), len(outcomes) - 1)]
            calls.append(outcome)
            if outcome == "busy":
                raise openai.RateLimitError("429 queue", response=httpx.Response(429, request=request), body=None)
            if outcome == "down":
                raise openai.APIConnectionError(request=request)
            if outcome == "500":
                raise openai.InternalServerError("upstream error", response=httpx.Response(500, request=request), body=None)
            msg = type("M", (), {"content": '{"reasoning": "r", "match": true, "confidence": 0.9}'})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    fake = type("Client", (), {})()
    fake.chat = type("Chat", (), {})()
    fake.chat.completions = Completions()
    jm._flag_busy(fake)
    jm.judge.client = fake
    return calls


def _judge_model(tmp_path, monkeypatch, **kw):
    from eval.scoring.llm import Cache, JudgeModel

    monkeypatch.setenv("MARTIAN_API_KEY", "test")
    monkeypatch.setenv("MARTIAN_BASE_URL", "http://127.0.0.1:9/v1")
    jm = JudgeModel("m", cache=Cache(tmp_path / "c.sqlite"), rate=6000, log=lambda msg: None, **kw)
    jm.backoff_base = 0.01
    return jm


def test_busy_endpoint_pauses_and_resumes(tmp_path, monkeypatch):
    jm = _judge_model(tmp_path, monkeypatch)
    assert jm.judge.client.max_retries == 0
    calls = _fake_endpoint(jm, ["busy", "down", "ok"])
    result = asyncio.new_event_loop().run_until_complete(jm.match("golden", "candidate"))
    assert result["match"] is True
    assert calls == ["busy", "down", "ok"]  # each failure paused and repeated once; Martian's loop never ran


def test_server_error_pauses_and_repeats(tmp_path, monkeypatch):
    # An HTTP 500 from the endpoint's upstream came back for every call for a while (Gate 1b, Qwen):
    # it is the endpoint failing, not the request, so it pauses like a 429 instead of ending the run.
    jm = _judge_model(tmp_path, monkeypatch)
    calls = _fake_endpoint(jm, ["500", "500", "ok"])
    result = asyncio.new_event_loop().run_until_complete(jm.match("golden", "candidate"))
    assert result["match"] is True
    assert calls == ["500", "500", "ok"]


def test_server_errors_past_max_wait_stop(tmp_path, monkeypatch):
    from eval.scoring.llm import EndpointUnavailable

    jm = _judge_model(tmp_path, monkeypatch, max_wait=0.05)
    _fake_endpoint(jm, ["500"])
    with pytest.raises(EndpointUnavailable):
        asyncio.new_event_loop().run_until_complete(jm.match("golden", "candidate"))


def test_timeout_pauses_and_repeats(tmp_path, monkeypatch):
    jm = _judge_model(tmp_path, monkeypatch)
    answers = [{"error": "Timed out after 180s"}, {"error": "Timed out after 180s"},
               {"reasoning": "r", "match": True, "confidence": 0.9}]
    seen = []

    async def call_llm(prompt):
        seen.append(prompt)
        return answers[len(seen) - 1]

    jm.judge.call_llm = call_llm
    result = asyncio.new_event_loop().run_until_complete(jm.match("golden", "candidate"))
    assert result["match"] is True and len(seen) == 3


def test_timeouts_past_max_wait_stop(tmp_path, monkeypatch):
    from eval.scoring.llm import EndpointUnavailable

    jm = _judge_model(tmp_path, monkeypatch, max_wait=0.05)

    async def call_llm(prompt):
        return {"error": "Timed out after 180s"}

    jm.judge.call_llm = call_llm
    with pytest.raises(EndpointUnavailable):
        asyncio.new_event_loop().run_until_complete(jm.match("golden", "candidate"))


def test_other_errors_are_not_repeated(tmp_path, monkeypatch):
    from eval.scoring.llm import LLMError

    jm = _judge_model(tmp_path, monkeypatch)
    seen = []

    async def call_llm(prompt):
        seen.append(prompt)
        return {"error": "JSON parse failed"}

    jm.judge.call_llm = call_llm
    with pytest.raises(LLMError):
        asyncio.new_event_loop().run_until_complete(jm.match("golden", "candidate"))
    assert len(seen) == 1


def test_endpoint_unavailable_stops(tmp_path, monkeypatch):
    from eval.scoring.llm import EndpointUnavailable

    jm = _judge_model(tmp_path, monkeypatch, max_wait=0.05)
    calls = _fake_endpoint(jm, ["down"])
    with pytest.raises(EndpointUnavailable):
        asyncio.new_event_loop().run_until_complete(jm.match("golden", "candidate"))
    assert 1 < len(calls) < 10


def test_command_line_imports():
    import eval.scoring.__main__ as cli

    assert callable(cli.main)


def test_kendall_tau():
    from eval.scoring.gates import kendall_tau

    assert kendall_tau({"a": 3, "b": 2, "c": 1}, {"a": 30, "b": 20, "c": 10}) == 1
    assert kendall_tau({"a": 3, "b": 2, "c": 1}, {"a": 10, "b": 20, "c": 30}) == -1


def test_gate1b_end_to_end(tmp_path, monkeypatch):
    from eval.scoring import gates

    monkeypatch.setattr(gates, "RESULTS", tmp_path / "results")
    martian = tmp_path / "martian"
    (martian / "results").mkdir(parents=True)
    golden = [{"comment": "Injection in search", "severity": "High", "category": "security"},
              {"comment": "Crash on empty list", "severity": "High", "category": "bug"}]
    said = {"good": ["injection risk here", "crash on empty input"], "mid": ["injection risk here", "typo"], "bad": ["typo"]}
    data = {f"https://github.com/o/r/pull/{n}": {"golden_comments": golden, "reviews": [
        {"tool": t, "review_comments": [{"body": "- " + "\n- ".join(said[t])}]} for t in said]} for n in (1, 2)}
    (martian / "results" / "benchmark_data.json").write_text(json.dumps(data), encoding="utf-8")
    f1 = {"good": (2, 0, 0), "mid": (1, 1, 1), "bad": (0, 1, 2)}
    for judge in gates.PUBLISHED:
        ev = {url: {t: {"true_positives": [{"category": "bug"}] * tp, "false_negatives": [{"category": "bug"}] * fn,
                        "fp": fp} for t, (tp, fp, fn) in f1.items()} for url in data}
        (martian / "results" / judge).mkdir()
        (martian / "results" / judge / "evaluations.json").write_text(json.dumps(ev), encoding="utf-8")
    asyncio.new_event_loop().run_until_complete(gates.judge_stored(FakeJudge(), martian, list(said)))
    text, passed = gates.gate1b_report("fake-model", martian, list(said))
    assert passed, text
    assert "fake-model vs claude-opus-4-5-20251101: +1.00" in text


def test_board_groups_count_once():
    rec = _record()
    for f, g in zip(rec["findings"], ["G1", "G2", "G2", "G3", "G4"]):
        f["group"] = g  # edge-1 and intent-1 are one group, as the board reports it
    idx, _ = select(rec, parse_view("board:full:cp"))
    assert idx == [0, 1, 3]  # intent-1 (c2) is the second finding of G2, so it isn't posted
    idx, _ = select(rec, parse_view("board:intent:cp"))
    assert idx == [2]  # without edge, intent-1 represents G2


def test_parse_code_review_text():
    from eval.scoring.data import parse_code_review_text

    text = """Reviewed `a..b` and found 3 issues.

1. `shop/reports.py:5`: user_label still calls get_user(user_id) after the signature change.
   Failure scenario: every call raises TypeError.
2. `shop/users.py:11-12`: Pagination offset skips the first page.
   Failure scenario: page 1 returns rows 21-40.
**3.** `docs/README.md`: The README describes the old API.
"""
    assert parse_code_review_text(text) == [
        {"file": "shop/reports.py", "line": 5, "summary": "user_label still calls get_user(user_id) after the signature change.",
         "failure_scenario": "every call raises TypeError."},
        {"file": "shop/users.py", "line": 11, "summary": "Pagination offset skips the first page.",
         "failure_scenario": "page 1 returns rows 21-40."},
        {"file": "docs/README.md", "line": None, "summary": "The README describes the old API.", "failure_scenario": ""},
    ]
    assert parse_code_review_text("No issues found.") == []


def test_gate1_agreement(tmp_path, monkeypatch):
    from eval.scoring import gates

    monkeypatch.setattr(gates, "RESULTS", tmp_path / "results")
    martian = tmp_path / "martian"
    (martian / "results" / "pub").mkdir(parents=True)
    golden = [{"comment": "Injection in search", "severity": "High", "category": "security"},
              {"comment": "Crash on empty list", "severity": "High", "category": "bug"}]
    url = "https://github.com/o/r/pull/1"
    data = {url: {"golden_comments": golden, "reviews": [
        {"tool": "claude-code", "review_comments": [{"body": "- injection risk here\n- typo in a name"}]}]}}
    (martian / "results" / "benchmark_data.json").write_text(json.dumps(data), encoding="utf-8")
    pub = {url: {"claude-code": {
        "true_positives": [{"golden_comment": "Injection in search", "category": "security"}],
        "false_negatives": [{"golden_comment": "Crash on empty list", "category": "bug"}], "fp": 1}}}
    (martian / "results" / "pub" / "evaluations.json").write_text(json.dumps(pub), encoding="utf-8")
    asyncio.new_event_loop().run_until_complete(gates.judge_stored(FakeJudge(), martian, ["claude-code"]))
    text, passed = gates.gate1_report("fake-model", "pub", martian)
    assert passed, text
    assert "2 of 2 (100.0%)" in text


def test_ungrouped_view_counts_every_finding():
    rec = _record()
    # Put edge-1 (finds G-off) in a board group headed by the saboteur's finding.
    for f in rec["findings"]:
        f["group"] = {"sab-1": "G1", "edge-1": "G1"}.get(f["id"], f["id"])
    grouped = score(rec, parse_view("board:saboteur,edge:cp"))
    ungrouped = score(rec, parse_view("board:saboteur,edge:cp:ungrouped"))
    assert (grouped["tp"], ungrouped["tp"]) == (1, 2)  # the report hides edge-1; the diagnostic doesn't
    with pytest.raises(ValueError):
        parse_view("board:full:cp:other")


def test_member_cards_marginal_recall():
    from eval.scoring.board import member_cards

    rec = _record()
    cards = {c["member"]: c for c in member_cards([rec], {}, {}, {}, "core", n_boot=20)}
    assert cards["saboteur"]["unique"] == 1 and cards["saboteur"]["marginal_recall"] > 0  # only it finds G-sql
    assert cards["edge"]["unique"] == 0 and cards["edge"]["marginal_recall"] == 0  # intent also finds G-off
    assert cards["nitpicker"]["marginal_recall"] == 0 and cards["nitpicker"]["contribution"] < 0


def test_gate2_needs_two_repeats(monkeypatch, capsys):
    import argparse
    from eval.scoring import __main__ as cli

    rec1, rec2 = _record(), {**_record(), "repeat": 2}
    def results(args, view, ids):
        r = {("p1", 1): score(rec1, view)}
        if args.repeats == 2:
            r[("p1", 2)] = score(rec2, view)
        return r
    monkeypatch.setattr(cli, "_results", results)
    monkeypatch.setattr(cli, "pr_ids", lambda args: ["p1", "p2"])
    args = argparse.Namespace(dataset="d", judge="j", split="dev", profile="core", bootstrap=20, repeats=1)
    cli.cmd_gate2(args)
    assert "NOT DECIDED" in capsys.readouterr().out
    args.repeats = 2
    cli.cmd_gate2(args)
    out = capsys.readouterr().out
    assert "NOT DECIDED" not in out and "Gate 2: FAIL" in out  # the same review on both sides: no recall gain


def test_merge_two_code_review_runs(tmp_path, monkeypatch):
    """Gate 3, A1: an issue found by both runs counts once, and nothing either run found is lost."""
    monkeypatch.setattr(pipeline, "RESULTS", tmp_path / "results")
    golden = [{"comment": "G-a", "severity": "Unrated", "category": "bug"},
              {"comment": "G-b", "severity": "Unrated", "category": "bug"}]

    def rec(repeat, texts, matches):
        cands = [{"text": t, "finding": f"cr-{i + 1}", "member": None, "label": None} for i, t in enumerate(texts)]
        return {"schema_version": 1, "dataset": "d", "variant": "code-review", "pr_id": "p1", "repeat": repeat,
                "source_sha256": str(repeat), "judge": {"model": "fake-model", "fingerprint": "f1", "served": {}},
                "golden": golden, "candidates": cands, "groups": None, "dedup_fallback": False,
                "verdicts": [[{"match": (g["comment"], t) in matches, "confidence": 0.9, "reasoning": ""} for t in texts]
                             for g in golden],
                "run_meta": {}, "findings": [{"id": c["finding"], "member": None, "label": None, "group": None,
                                              "file": None, "line": None, "text": c["text"]} for c in cands]}

    r1 = rec(1, ["a, run 1", "noise 1"], {("G-a", "a, run 1")})
    r2 = rec(2, ["a, run 2", "b, run 2"], {("G-a", "a, run 2"), ("G-b", "b, run 2")})
    r2["candidates"].append({"text": "bare extracted issue", "finding": None, "member": None, "label": None})
    for row in r2["verdicts"]:
        row.append({"match": False, "confidence": 0.1, "reasoning": ""})

    class Dedup(FakeJudge):
        async def dedup(self, texts):
            assert texts == ["a, run 1", "noise 1", "a, run 2", "b, run 2", "bare extracted issue"]
            return [[0, 2], [1], [3], [4]], False

    loop = asyncio.new_event_loop()
    out = loop.run_until_complete(pipeline.merge_records([r1, r2], Dedup(), 1))
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["variant"] == "code-review-x2" and m["merged_from"] == [1, 2]
    assert [c["finding"] for c in m["candidates"]] == ["r1/cr-1", "r1/cr-2", "r2/cr-1", "r2/cr-2", "r2/x2"]
    assert len(m["verdicts"][0]) == 5 and m["groups"] == [[0, 2], [1], [3], [4]]
    merged = score(m, parse_view("code-review-x2"))
    assert merged["tp"] == 2 and merged["fp"] == 2  # "a" counts once; "noise 1" and the bare issue are wrong
    # Shared rule: a group is a hit if any finding matches, whichever run's comes first.
    m["verdicts"][0][0] = {"match": False, "confidence": 0.1, "reasoning": ""}
    again = score(m, parse_view("code-review-x2"))
    assert again["tp"] == 2 and again["fp"] == 2
    with pytest.raises(ValueError):
        loop.run_until_complete(pipeline.merge_records([r1, {**r2, "pr_id": "p2"}], Dedup(), 1))


def test_per_issue_view_counts_each_dedup_group_once():
    rec = _record()  # Martian groups [[0, 4], [1, 2], [3]]
    issues = score(rec, parse_view("board:full:raw:issues"))
    assert issues["tp"] == 2 and issues["fp"] == 1  # only the nitpicker's group is wrong
    rec["groups"] = [[0], [1], [2], [3], [4]]
    rec["verdicts"][1][2]["match"] = False  # c2 no longer matches, and isn't grouped with c1
    # Ungrouped, c4 (a weaker match for G-sql) is no longer c0's duplicate either: c2, c3, c4 are wrong.
    assert score(rec, parse_view("board:full:raw:issues"))["fp"] == 3
    assert parse_view("code-review:issues").per_issue and parse_view("code-review-x2").per_issue


def test_code_review_member_output(tmp_path):
    """Gate 3, A2: a /code-review run becomes one member_output that the board's schema accepts."""
    from eval.scoring.data import code_review_member_output

    (tmp_path / "final.md").write_text(
        "2 findings:\n\n1. `a.py:7`: Off by one in the loop.\n\n   Failure scenario: the last row is skipped.\n\n"
        "2. `README.md`: The docs describe the old flag.\n", encoding="utf-8")
    out = code_review_member_output(tmp_path)
    assert out["member"] == "code-review" and len(out["findings"]) == 2
    first, second = out["findings"]
    assert first == {"file": "a.py", "line": 7, "claim": "Off by one in the loop.", "evidence": "the last row is skipped.",
                     "severity": "High", "category": "bug", "confidence": 0.6}
    assert second["line"] == 1 and second["evidence"] == ""

    schema = json.loads((Path(__file__).parents[3] / "plugin/skills/review/schema.json").read_text(encoding="utf-8"))
    finding = schema["$defs"]["finding"]
    for f in out["findings"]:
        assert set(finding["required"]) <= set(f) and set(f) <= set(finding["properties"])
        assert f["severity"] in schema["$defs"]["severity"]["enum"] and f["category"] in schema["$defs"]["category"]["enum"]
    assert set(out) <= set(schema["$defs"]["member_output"]["properties"])

    empty = tmp_path / "none"
    empty.mkdir()
    (empty / "final.md").write_text("I could not review this.", encoding="utf-8")
    assert code_review_member_output(empty) is None


def test_empty_report_call_gives_way_to_reply_findings(tmp_path):
    """Gate 3: an accepted ReportFindings call with no findings, then the findings in the reply."""
    from eval.scoring.data import code_review_found

    call = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "ReportFindings",
                                                       "input": {"findings": []}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
    ]
    (tmp_path / "stream.jsonl").write_text("\n".join(json.dumps(x) for x in call), encoding="utf-8")
    (tmp_path / "final.md").write_text(
        "I called ReportFindings with an empty list by mistake. The findings below count.\n\n"
        "1. `a.py:7`: Off by one in the loop.\n\n   Failure scenario: the last row is skipped.\n", encoding="utf-8")
    assert [f["file"] for f in code_review_found(tmp_path)] == ["a.py"]

    (tmp_path / "final.md").write_text("No issues found.", encoding="utf-8")
    assert code_review_found(tmp_path) == []  # a clean review with an empty call stays a clean review


def test_board_cr_views():
    v = parse_view("board-cr:full:cp:issues")
    assert v.variant == "board-cr" and v.per_issue and v.labels == frozenset({"CONFIRMED", "PLAUSIBLE"})
    assert parse_view("board:full:cp").variant == "board-full"
    with pytest.raises(ValueError):
        parse_view("board-x:full:cp")


def test_gate4_views():
    b1, b2 = parse_view("board-none:full:confirmed:issues"), parse_view("board-default:full:cp:issues")
    assert b1.variant == "board-none" and b1.labels == frozenset({"CONFIRMED"}) and b1.per_issue and b1.members is None
    assert b2.variant == "board-default" and b2.labels == frozenset({"CONFIRMED", "PLAUSIBLE"}) and b2.per_issue
    ref = parse_view("code-review-b1:issues")
    assert ref.variant == "code-review-b1" and ref.per_issue and ref.labels is None


def test_nested_reviews_need_both_sessions_ok(tmp_path, monkeypatch):
    """Gate 4's floor reference: the `/code-review` session inside each finished `board-none` run."""
    from eval.scoring import data

    monkeypatch.setattr(data, "RESULTS", tmp_path)
    for rep, parent, nested in ((1, "ok", "ok"), (2, "failed:usage-limit", "ok"), (3, "ok", "failed:code-review")):
        run = tmp_path / "d" / "board-none" / "p1" / f"r{rep}"
        (run / "code-review").mkdir(parents=True)
        (run / "meta.json").write_text(json.dumps({"status": parent}), encoding="utf-8")
        (run / "code-review" / "meta.json").write_text(json.dumps({"status": nested, "repeat": rep}), encoding="utf-8")
        (run / "code-review" / "final.md").write_text(
            "1. `a.py:7`: Off by one in the loop.\n\n   Failure scenario: the last row is skipped.\n", encoding="utf-8")
    runs = data.iter_runs("d", ["code-review-b1"], ["p1"])
    assert [(r.variant, r.repeat, r.path.name, r.meta["repeat"]) for r in runs] == [("code-review-b1", 1, "code-review", 1)]
    found = data.review_findings(runs[0])
    assert [f["file"] for f in found] == ["a.py"] and found[0]["member"] is None  # read as `/code-review`, not board JSON


def test_fp_per_pr():
    units = {"p1": (1.0, 2.0, 0.0), "p2": (0.0, 1.0, 1.0)}
    assert summarize(units)["fp_per_pr"] == 1.5
    assert summarize(units, ["p1", "p1"])["fp_per_pr"] == 2.0  # a bootstrap sample repeats PRs


def test_gate4_verdict():
    from eval.scoring.__main__ import gate4_verdict

    assert gate4_verdict((-0.8, -0.2), True).startswith("BOARD WINS")
    assert gate4_verdict((-0.8, -0.2), False) == "NO DECISION (the recall floor fails)"
    assert gate4_verdict((0.1, 0.5), True).startswith("CHECKING IS ENOUGH")
    assert gate4_verdict((0.1, 0.5), False).startswith("CHECKING IS ENOUGH")
    assert gate4_verdict((-0.3, 0.2), True) == "NO DECISION (the false-positive CI spans 0)"


def _gate4_results(fp_b1, fp_b2, tp_b2, repeats=2, prs=6):
    """{view spec: _results} with every PR having 3 golden bugs."""
    def res(fp, tp):
        return {"true_positives": [{"category": "bug"}] * tp, "false_negatives": [{"category": "bug"}] * (3 - tp),
                "fp": fp}
    per = {"board-default:full:cp:issues": (fp_b2, tp_b2), "board-none:full:confirmed:issues": (fp_b1, 2),
           "code-review-b1:issues": (4, 3), "board-none:full:cp:issues": (fp_b1 + 1, 2),
           "board-default:full:confirmed:issues": (fp_b2, tp_b2)}
    return {spec: {(f"p{i}", r): res(fp + (i % 2), tp) for i in range(prs) for r in range(1, repeats + 1)}
            for spec, (fp, tp) in per.items()}


@pytest.mark.parametrize("fp_b1, fp_b2, tp_b2, repeats, expect", [
    (3, 0, 3, 2, "Gate 4: BOARD WINS"),
    (3, 0, 2, 2, "Gate 4: NO DECISION (the recall floor fails)"),  # 2/3 recall < 90% of 3/3
    (0, 3, 3, 2, "Gate 4: CHECKING IS ENOUGH"),
    (3, 0, 3, 1, "NOT DECIDED"),
])
def test_gate4_end_to_end(monkeypatch, capsys, fp_b1, fp_b2, tp_b2, repeats, expect):
    import argparse
    from eval.scoring import __main__ as cli

    table = _gate4_results(fp_b1, fp_b2, tp_b2, repeats)
    monkeypatch.setattr(cli, "_results", lambda args, view, ids: table[view.name])
    monkeypatch.setattr(cli, "pr_ids", lambda args: [f"p{i}" for i in range(6)])
    cli.cmd_gate4(argparse.Namespace(dataset="d", judge="j", split="test", profile="core", bootstrap=200))
    out = capsys.readouterr().out
    assert expect in out


def test_board_findings_undoes_gate4_format_slips():
    from eval.scoring.data import board_findings

    f1 = {"id": "edge-1", "member": "edge", "label": "CONFIRMED", "claim": "a"}
    f2 = {"id": "intent-2", "label": "PLAUSIBLE", "claim": "b"}
    normal = {"findings": [{**f1, "group": "G1"}, {**f2, "member": "intent", "group": "G2"}]}
    assert board_findings(normal) == normal["findings"]
    nested = {"groups": [{"id": "G1", "label": "CONFIRMED", "findings": [f1]},
                         {"id": "G2", "label": "PLAUSIBLE", "findings": [{"id": "intent-2", "member": "intent", "claim": "b"}]}]}
    assert board_findings(nested) == normal["findings"]  # group from its group; a missing label from the group's
    no_member = {"findings": [{**f2, "group": "G2"}]}
    assert board_findings(no_member)[0]["member"] == "intent"
    assert board_findings({"findings": [{"id": "test-auditor-3", "claim": "c"}]})[0]["member"] == "test-auditor"
