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


def test_unbuilt_command_fails_clearly(capsys):
    assert main(["run"]) == 2
    assert "not built yet" in capsys.readouterr().err
