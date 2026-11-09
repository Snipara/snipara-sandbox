"""Minimal commands must not depend on optional inference or visualization."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BOOTSTRAP = """
import importlib.abc
import sys

FORBIDDEN = {"litellm", "openai", "anthropic", "streamlit", "plotly", "pytest"}

class NoOptionalStack(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in FORBIDDEN:
            raise ModuleNotFoundError("Optional stack unavailable", name=fullname)

sys.meta_path.insert(0, NoOptionalStack())
"""


def minimal_child(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run with a private home, no provider credentials and denied optional imports."""
    env = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C",
    }
    return subprocess.run(
        [sys.executable, "-B", "-c", BOOTSTRAP + textwrap.dedent(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_minimal_public_imports_cli_metadata_and_mcp_startup(tmp_path):
    result = minimal_child(
        """
        import json
        from pathlib import Path
        import snipara_sandbox
        assert 'rlm.core.orchestrator' not in sys.modules
        from rlm.cli.main import app
        from typer.testing import CliRunner
        runner = CliRunner()
        request = Path('request.json')
        request.write_text(json.dumps({
            'workspace': str(Path.cwd()), 'argv': ['python', '-V'],
            'image': 'sha256:' + 'a' * 64,
        }))
        for argv in [
            ['--version'], ['version'], ['--help'], ['validation', '--help'],
            ['validation', 'profiles'],
            ['validation', 'inspect', '--request', str(request)],
        ]:
            response = runner.invoke(app, argv)
            assert response.exit_code == 0, (argv, response.output, response.exception)
        from rlm.mcp.server import create_server
        from rlm.validation.mcp import validation_tools
        assert create_server().name == 'SniparaSandbox'
        assert {'validation_run', 'validation_profiles', 'validation_inspect'} <= {
            tool.name for tool in validation_tools()
        }
        assert not FORBIDDEN.intersection(sys.modules)
        from snipara_sandbox import SniparaSandbox, Sandbox
        assert SniparaSandbox is Sandbox
        assert not FORBIDDEN.intersection(sys.modules)
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("command", ["run", "agent"])
def test_provider_commands_fail_actionably_before_loading_config(command, tmp_path):
    result = minimal_child(
        f"""
        import json
        from typer.testing import CliRunner
        from rlm.cli.main import app
        import rlm.cli.main
        import rlm.core.config
        def forbidden_config(*args, **kwargs):
            raise AssertionError('Missing inference must not load credentials/config')
        rlm.core.config.load_config = forbidden_config
        rlm.cli.main._load_project_env = forbidden_config
        response = CliRunner().invoke(app, [{command!r}, 'synthetic task', '--json'])
        assert response.exit_code == 1, response.output
        body = json.loads(response.output)
        assert body['success'] is False
        assert "snipara-sandbox[inference]" in body['error'], body
        assert not FORBIDDEN.intersection(sys.modules)
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_mcp_provider_request_does_not_start_or_load_config_without_extra(tmp_path):
    result = minimal_child(
        """
        import asyncio
        from rlm.mcp.server import AgentManager, SessionManager, _agent_run
        import rlm.core.config
        def forbidden_config(*args, **kwargs):
            raise AssertionError('Missing inference must not load credentials/config')
        rlm.core.config.load_config = forbidden_config
        agents = AgentManager()
        response = asyncio.run(_agent_run(agents, SessionManager(), {'task': 'synthetic'}))
        assert response.isError is True
        assert 'snipara-sandbox[inference]' in response.content[0].text
        assert not agents.list_runs()
        assert not FORBIDDEN.intersection(sys.modules)
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_custom_sdk_backend_remains_usable_without_litellm(tmp_path):
    result = minimal_child(
        """
        from rlm import RLM, RLMConfig
        from rlm.backends.base import BaseBackend
        class CustomBackend(BaseBackend):
            model = 'synthetic'
            async def complete(self, *args, **kwargs):
                raise AssertionError('No inference is requested')
            async def stream(self, *args, **kwargs):
                raise AssertionError('No inference is requested')
                yield ''
        backend = CustomBackend()
        runtime = RLM(backend=backend, config=RLMConfig(_env_file=None))
        assert runtime.backend is backend
        try:
            RLM(config=RLMConfig(_env_file=None))
        except ImportError as error:
            assert 'snipara-sandbox[inference]' in str(error)
        else:
            raise AssertionError('Default provider backend must require inference')
        assert not FORBIDDEN.intersection(sys.modules)
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_optional_check_does_not_hide_unrelated_import_errors(monkeypatch):
    from rlm.core import optional

    def broken_import(_module):
        raise ModuleNotFoundError("A broken dependency", name="unrelated_dependency")

    monkeypatch.setattr(optional, "find_spec", broken_import)
    with pytest.raises(ModuleNotFoundError, match="broken dependency"):
        optional.require_inference()
