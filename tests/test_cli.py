import pytest

from frits import __version__
from frits.cli import COMMANDS, main


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    for name in COMMANDS:
        assert name in out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_init_creates_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRITS_HOME", str(tmp_path / "FRITS"))
    assert main(["init"]) == 0
    assert (tmp_path / "FRITS" / "settings.toml").exists()
    assert (tmp_path / "FRITS" / "inbox").is_dir()


def test_unknown_draft_fails_clearly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRITS_HOME", str(tmp_path / "FRITS"))
    assert main(["show", "99"]) == 1
    assert "gibt es nicht" in capsys.readouterr().err


def test_strict_mode_refuses_cloud(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRITS_HOME", str(tmp_path / "FRITS"))
    main(["init"])
    settings = tmp_path / "FRITS" / "settings.toml"
    settings.write_text(settings.read_text().replace("strict_local = false", "strict_local = true"))
    note = tmp_path / "note.txt"
    note.write_text("2 Std Arbeit")
    assert main(["run", str(note), "--provider", "claude"]) == 1
    assert "Strikter Modus" in capsys.readouterr().err
