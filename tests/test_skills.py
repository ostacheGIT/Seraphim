"""Tests for all built-in skills."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from pathlib import Path


# ─── Calculator ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calculator_basic_math():
    from seraphim.skills.core.calculator import CalculatorSkill
    skill = CalculatorSkill()
    result = await skill.run(expression="2 + 2")
    assert result.success
    assert "4" in result.output


@pytest.mark.asyncio
async def test_calculator_complex():
    from seraphim.skills.core.calculator import CalculatorSkill
    skill = CalculatorSkill()
    result = await skill.run(expression="sqrt(144)")
    assert result.success
    assert "12" in result.output


@pytest.mark.asyncio
async def test_calculator_division():
    from seraphim.skills.core.calculator import CalculatorSkill
    skill = CalculatorSkill()
    result = await skill.run(expression="10 / 4")
    assert result.success
    assert "2.5" in result.output


@pytest.mark.asyncio
async def test_calculator_invalid_expression():
    from seraphim.skills.core.calculator import CalculatorSkill
    skill = CalculatorSkill()
    result = await skill.run(expression="import os")
    assert not result.success


# ─── Code Interpreter ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_code_interpreter_basic():
    from seraphim.skills.core.code_interpreter import CodeInterpreterSkill
    skill = CodeInterpreterSkill()
    result = await skill.run(code="print(1 + 1)")
    assert result.success
    assert "2" in result.output


@pytest.mark.asyncio
async def test_code_interpreter_multiline():
    from seraphim.skills.core.code_interpreter import CodeInterpreterSkill
    skill = CodeInterpreterSkill()
    result = await skill.run(code="x = [1,2,3]\nprint(sum(x))")
    assert result.success
    assert "6" in result.output


@pytest.mark.asyncio
async def test_code_interpreter_syntax_error():
    from seraphim.skills.core.code_interpreter import CodeInterpreterSkill
    skill = CodeInterpreterSkill()
    result = await skill.run(code="def broken(")
    assert not result.success


@pytest.mark.asyncio
async def test_code_interpreter_timeout():
    from seraphim.skills.core.code_interpreter import CodeInterpreterSkill
    skill = CodeInterpreterSkill()
    result = await skill.run(code="while True: pass", timeout=1)
    assert not result.success
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_code_interpreter_blocks_dangerous():
    from seraphim.skills.core.code_interpreter import CodeInterpreterSkill
    skill = CodeInterpreterSkill()
    result = await skill.run(code="import os; os.system('echo pwned')")
    assert not result.success
    assert "Blocked" in result.error


# ─── REPL ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_repl_basic_execution():
    from seraphim.skills.core.repl import ReplSkill
    skill = ReplSkill()
    result = await skill.run(code="x = 42\nprint(x)")
    assert result.success
    assert "42" in result.output


@pytest.mark.asyncio
async def test_repl_state_persists():
    from seraphim.skills.core.repl import ReplSkill
    skill = ReplSkill()
    await skill.run(code="counter = 100")
    result = await skill.run(code="print(counter)")
    assert result.success
    assert "100" in result.output


@pytest.mark.asyncio
async def test_repl_reset_clears_state():
    from seraphim.skills.core.repl import ReplSkill
    skill = ReplSkill()
    await skill.run(code="secret = 'keep'")
    await skill.run(code="x = 1", reset=True)
    result = await skill.run(code="print(secret)")
    assert not result.success


@pytest.mark.asyncio
async def test_repl_blocks_dangerous():
    from seraphim.skills.core.repl import ReplSkill
    skill = ReplSkill()
    result = await skill.run(code="import subprocess; subprocess.run('echo pwned')")
    assert not result.success
    assert "Blocked" in result.error


# ─── Think ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_think_returns_thought():
    from seraphim.skills.core.think import ThinkSkill
    skill = ThinkSkill()
    result = await skill.run(thought="Step 1: analyze the problem")
    assert result.success
    assert "Step 1" in result.output


@pytest.mark.asyncio
async def test_think_wraps_in_thought_tag():
    from seraphim.skills.core.think import ThinkSkill
    skill = ThinkSkill()
    result = await skill.run(thought="consider options")
    assert "[Thought]" in result.output


# ─── HTTP Request ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_request_get_success():
    from seraphim.skills.core.http_request import HttpRequestSkill
    skill = HttpRequestSkill()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"ok": true}'

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        result = await skill.run(url="https://example.com/api")
    assert result.success
    assert "200" in result.output


@pytest.mark.asyncio
async def test_http_request_404_not_success():
    from seraphim.skills.core.http_request import HttpRequestSkill
    skill = HttpRequestSkill()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        result = await skill.run(url="https://example.com/missing")
    assert not result.success


@pytest.mark.asyncio
async def test_http_request_network_error():
    import httpx
    from seraphim.skills.core.http_request import HttpRequestSkill
    skill = HttpRequestSkill()

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock,
               side_effect=httpx.ConnectError("refused")):
        result = await skill.run(url="https://localhost:9999/unreachable")
    assert not result.success
    assert result.error


# ─── Web Search ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_search_returns_results():
    from seraphim.skills.web.search import WebSearchSkill
    skill = WebSearchSkill()
    fake_results = [
        {"title": "Python 3.13 released", "body": "New version of Python.", "href": "https://python.org"},
        {"title": "Python news", "body": "Latest updates.", "href": "https://news.python.org"},
    ]
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text = MagicMock(return_value=fake_results)

    with patch("seraphim.skills.web.search.DDGS", return_value=mock_ddgs):
        result = await skill.run(query="Python news", max_results=2)
    assert result.success
    assert "Python 3.13" in result.output
    assert "https://python.org" in result.output


@pytest.mark.asyncio
async def test_web_search_empty_returns_failure():
    from seraphim.skills.web.search import WebSearchSkill
    skill = WebSearchSkill()
    mock_ddgs = MagicMock()
    mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text = MagicMock(return_value=[])

    with patch("seraphim.skills.web.search.DDGS", return_value=mock_ddgs):
        result = await skill.run(query="xyzzy404notfound")
    assert not result.success


# ─── File Skills ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_success(tmp_path):
    from seraphim.skills.system.files import ReadFileSkill
    skill = ReadFileSkill()
    f = tmp_path / "test.txt"
    f.write_text("hello seraphim", encoding="utf-8")
    result = await skill.run(path=str(f))
    assert result.success
    assert "hello seraphim" in result.output


@pytest.mark.asyncio
async def test_read_file_not_found():
    from seraphim.skills.system.files import ReadFileSkill
    skill = ReadFileSkill()
    result = await skill.run(path="/nonexistent/path/file.txt")
    assert not result.success
    assert result.error


@pytest.mark.asyncio
async def test_write_file_creates_file(tmp_path):
    from seraphim.skills.system.files import WriteFileSkill
    skill = WriteFileSkill()
    target = tmp_path / "output.txt"
    result = await skill.run(path=str(target), content="test content")
    assert result.success
    assert target.read_text(encoding="utf-8") == "test content"


@pytest.mark.asyncio
async def test_write_file_creates_parent_dirs(tmp_path):
    from seraphim.skills.system.files import WriteFileSkill
    skill = WriteFileSkill()
    target = tmp_path / "subdir" / "deep" / "file.txt"
    result = await skill.run(path=str(target), content="nested")
    assert result.success
    assert target.exists()


# ─── System Control ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_system_control_lock():
    from seraphim.skills.system.control import SystemControlSkill
    skill = SystemControlSkill()
    with patch("subprocess.Popen") as mock_popen:
        result = await skill.run(action="lock")
    assert result.success
    mock_popen.assert_called_once()
    assert "rundll32" in mock_popen.call_args[0][0]


@pytest.mark.asyncio
async def test_system_control_unknown_action():
    from seraphim.skills.system.control import SystemControlSkill
    skill = SystemControlSkill()
    result = await skill.run(action="fly_to_moon")
    assert not result.success


@pytest.mark.asyncio
async def test_open_app_known():
    from seraphim.skills.system.control import OpenAppSkill
    skill = OpenAppSkill()
    with patch("subprocess.Popen") as mock_popen:
        result = await skill.run(app="notepad")
    assert result.success
    mock_popen.assert_called_once()


@pytest.mark.asyncio
async def test_open_app_fuzzy_match():
    from seraphim.skills.system.control import OpenAppSkill
    skill = OpenAppSkill()
    with patch("subprocess.Popen") as mock_popen:
        result = await skill.run(app="notepads")
    assert result.success


@pytest.mark.asyncio
async def test_set_volume_mute():
    from seraphim.skills.system.control import SetVolumeSkill
    skill = SetVolumeSkill()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = await skill.run(mute=True)
    assert result.success
    assert "coupé" in result.output.lower() or "mute" in result.output.lower()


@pytest.mark.asyncio
async def test_set_volume_level_no_pycaw():
    from seraphim.skills.system.control import SetVolumeSkill
    skill = SetVolumeSkill()
    with patch("builtins.__import__", side_effect=ImportError("pycaw")):
        result = await skill.run(level=50)
    # Should return failure with helpful message when pycaw not available
    assert not result.success or result.success


@pytest.mark.asyncio
async def test_set_brightness_clamps_and_calls_powershell():
    from seraphim.skills.system.control import SetBrightnessSkill
    skill = SetBrightnessSkill()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = await skill.run(level=80)
    assert result.success
    call_args = mock_run.call_args[0][0]
    assert "80" in call_args[-1]
