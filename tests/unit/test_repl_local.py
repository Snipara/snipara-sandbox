"""Unit tests for LocalREPL."""

import pytest

from rlm.repl.local import LocalREPL


@pytest.fixture
def repl():
    return LocalREPL(timeout=10)


class TestLocalREPL:
    """Tests for LocalREPL execution."""

    @pytest.mark.asyncio
    async def test_simple_execution(self, repl):
        """Test basic code execution."""
        result = await repl.execute("print('hello')")
        assert result.success
        assert "hello" in result.output
        assert result.error is None

    @pytest.mark.asyncio
    async def test_result_variable(self, repl):
        """Test that result variable is captured."""
        result = await repl.execute("result = 42")
        assert result.success
        assert "result = 42" in result.output

    @pytest.mark.asyncio
    async def test_math_operations(self, repl):
        """Test math operations."""
        result = await repl.execute("result = sum(range(10))")
        assert result.success
        assert "45" in result.output

    @pytest.mark.asyncio
    async def test_allowed_import(self, repl):
        """Test that allowed imports work."""
        result = await repl.execute("import json; result = json.dumps({'a': 1})")
        assert result.success
        assert '"a": 1' in result.output or "'a': 1" in result.output

    @pytest.mark.asyncio
    async def test_blocked_import(self, repl):
        """Test that blocked imports are rejected."""
        result = await repl.execute("import os")
        assert not result.success
        assert "not allowed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_blocked_subprocess(self, repl):
        """Test that subprocess is blocked."""
        result = await repl.execute("import subprocess")
        assert not result.success
        assert "not allowed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_syntax_error(self, repl):
        """Test syntax error handling."""
        result = await repl.execute("def broken(")
        assert not result.success
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_runtime_error(self, repl):
        """Test runtime error handling."""
        result = await repl.execute("x = 1/0")
        assert not result.success
        assert "ZeroDivision" in result.error

    @pytest.mark.asyncio
    async def test_context_persistence(self, repl):
        """Test that context persists across executions."""
        repl.set_context("my_value", 123)
        result = await repl.execute("result = context['my_value']")
        assert result.success
        assert "123" in result.output

    @pytest.mark.asyncio
    async def test_context_clear(self, repl):
        """Test context clearing."""
        repl.set_context("key", "value")
        repl.clear_context()
        assert repl.get_context() == {}


class TestLocalREPLSafety:
    """Tests for LocalREPL safety features."""

    @pytest.mark.asyncio
    async def test_no_file_write(self, repl):
        """Test that file writing is blocked."""
        result = await repl.execute("open('/tmp/test', 'w').write('test')")
        # Should either fail or not have access
        assert not result.success or "Error" in result.output

    @pytest.mark.asyncio
    async def test_no_network(self, repl):
        """Test that network access is blocked."""
        result = await repl.execute("import socket")
        assert not result.success

    @pytest.mark.asyncio
    async def test_output_truncation(self, repl):
        """Test that large output is truncated."""
        result = await repl.execute("print('x' * 200000)")
        assert result.truncated or len(result.output) < 200000


class TestAugmentedAssignment:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "initial, operation, operand, expected",
        [
            (1, "+=", 1, 2),
            (5, "-=", 2, 3),
            (3, "*=", 4, 12),
            (7, "/=", 2, 3.5),
            (7, "//=", 2, 3),
            (7, "%=", 3, 1),
            (2, "**=", 3, 8),
            (3, "<<=", 2, 12),
            (12, ">>=", 2, 3),
            (6, "&=", 3, 2),
            (6, "^=", 3, 5),
            (6, "|=", 3, 7),
        ],
    )
    async def test_numeric_operators(self, repl, initial, operation, operand, expected):
        outcome = await repl.execute(
            f"x = {initial!r}\nx {operation} {operand!r}\nassert x == {expected!r}"
        )
        assert outcome.success, outcome.error

    @pytest.mark.asyncio
    async def test_control_and_mutable_alias(self, repl):
        outcome = await repl.execute(
            "x = 1\nx = x + 1\nassert x == 2\n"
            "x = [1]\nalias = x\nx += [2]\nassert alias is x\nassert alias == [1, 2]\n"
            "word = 'a'\nword += 'b'\nassert word == 'ab'"
        )
        assert outcome.success, outcome.error

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "code, error",
        [
            ("x = 1\nx /= 0", "ZeroDivisionError"),
            ("x = 1\nx += 'a'", "TypeError"),
            ("x = 1\nx @= 2", "TypeError"),
        ],
    )
    async def test_native_errors_preserved(self, repl, code, error):
        outcome = await repl.execute(code)
        assert not outcome.success
        assert error in outcome.error
        assert "_inplacevar_" not in outcome.error
