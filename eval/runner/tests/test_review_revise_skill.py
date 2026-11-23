"""The review-revise skill's script must keep the measured pipeline: the frozen prompts and the 5-of-5 rule."""

import hashlib
import importlib.util
import json

from eval.runner import revise, run
from eval.scoring import consensus
from eval.scoring.data import ROOT

_PATH = ROOT / "plugin" / "skills" / "review-revise" / "scripts" / "review_revise.py"
_spec = importlib.util.spec_from_file_location("review_revise", _PATH)
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


def test_prompts_are_the_frozen_ones():
    assert rr.MATCH_PROMPT == consensus.MATCH_PROMPT
    assert rr.REVISE_PROMPT.replace("`<range>`", "`HEAD~1..HEAD`") == revise.PROMPT
    assert hashlib.sha256(revise.PROMPT.encode()).hexdigest()[:12] == "53a4f25dfbce"


def test_tools_are_the_benchmark_ones():
    assert rr.REVIEW_TOOLS == (run.TOOLS, run.ALLOWED_TOOLS, run.DISALLOWED_TOOLS)
    assert rr.REVISE_TOOLS == (run.REVISE_TOOLS, run.REVISE_ALLOWED_TOOLS, run.REVISE_DISALLOWED_TOOLS)
    assert (rr.MODEL, rr.EFFORT, rr.RUNS) == (run.MODEL, run.EFFORT, 5)


def test_keeps_only_issues_every_run_found():
    run_of = [1, 1, 2, 3, 3]
    groups = [[0, 2, 3], [1, 4]]
    assert rr.keep_all(groups, run_of, 3) == [[0, 2, 3]]
    texts = ["a", "b", "a", "c", "d"]
    assert rr.findings_file(texts, [[0, 2, 3]]) == {"findings": [{"id": "F1", "texts": ["a", "c"]}]}


def test_findings_from_report_call_or_reply(tmp_path):
    events = [
        {"message": {"content": [{"type": "tool_use", "id": "t1", "name": "ReportFindings",
                                  "input": {"findings": [{"file": "a.py", "line": 3, "summary": "off by one"}]}}]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}},
    ]
    (tmp_path / "stream.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    (tmp_path / "final.md").write_text("", encoding="utf-8")
    assert rr.run_findings(tmp_path) == ["`a.py:3`: off by one"]
    (tmp_path / "stream.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "final.md").write_text("1. `b.py:10` — null check missing\n2. `c.py` - leak\n", encoding="utf-8")
    assert rr.run_findings(tmp_path) == ["`b.py:10`: null check missing", "`c.py`: leak"]


def test_report_parsing():
    ids = ["F1", "F2"]
    body = '{"findings": [{"id": "F1", "action": "fixed", "reason": "x"}, {"id": "F2", "action": "declined", "reason": "y"}]}'
    assert rr.parse_report(body, ids)["findings"][1]["action"] == "declined"
    assert rr.parse_report("```json\n" + body + "\n```", ids)
    assert rr.parse_report("It uses `${today}` {here}.\n\n" + body, ids)
    try:
        rr.parse_report('{"findings": [{"id": "F1"}]}', ids)
        raise AssertionError("a report missing a finding id must fail")
    except ValueError:
        pass
