"""The demo command is the transcript a reader can reproduce."""

from enquiry_desk.cli import main
from tests.conftest import ROOT


def test_demo_prints_the_review_loop(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(ROOT)
    code = main(["demo", "--log-dir", str(tmp_path)])
    assert code == 0
    output = capsys.readouterr().out
    assert "Hale & Quay Lettings (fictional agency, demo data)" in output
    assert "Model: stub (offline, no API key)" in output
    assert "queen-square" in output
    assert "Offline stub: this first draft deliberately agrees" in output
    assert "We cannot waive the deposit" in output
    assert "Outcome: approved_to_send" in output
    assert "Illustrative cost: $0.00" in output
    assert "Mail delivery is out of scope" in output
    assert (tmp_path / "enq-1044.json").is_file()


def test_diagram_lists_the_supervisor(capsys):
    assert main(["diagram"]) == 0
    output = capsys.readouterr().out
    assert "supervisor" in output
    assert "approval" in output


def test_noninteractive_run_requires_a_decision(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code = main(["run", "examples/spam.json", "--log-dir", str(tmp_path)])
    assert code == 2
    assert "decision" in capsys.readouterr().err.lower()
