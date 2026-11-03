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
            "findings": [{"id": c["finding"], "label": c["label"]} for c in cands]}


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


def test_quota_stops_without_retries(tmp_path, monkeypatch):
    import httpx2 as httpx
    import openai

    from eval.scoring.llm import Cache, JudgeModel, QuotaStop

    monkeypatch.setenv("MARTIAN_API_KEY", "test")
    monkeypatch.setenv("MARTIAN_BASE_URL", "http://127.0.0.1:9/v1")
    jm = JudgeModel("m", cache=Cache(tmp_path / "c.sqlite"))
    assert jm.judge.client.max_retries == 0
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            raise openai.RateLimitError("429 quota", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)

    fake = type("Client", (), {})()
    fake.chat = type("Chat", (), {})()
    fake.chat.completions = Completions()
    jm._stop_on_quota(fake)
    jm.judge.client = fake
    loop = asyncio.new_event_loop()
    with pytest.raises(QuotaStop):
        loop.run_until_complete(jm.match("golden", "candidate"))
    assert len(calls) == 1  # Martian's retry loop never ran
    with pytest.raises(QuotaStop):
        loop.run_until_complete(jm.extract("a comment long enough to be extracted"))
    assert len(calls) == 1  # later calls stop before reaching the endpoint
