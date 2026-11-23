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


class TestLocalREPLDeadlineAndFreshness:
    """A call stops at its timeout, and the same code is run again each time."""

    @pytest.mark.asyncio
    async def test_infinite_loop_is_stopped_at_the_timeout(self):
        import time

        repl = LocalREPL()
        started = time.time()
        result = await repl.execute("while True:\n    pass", timeout=1)
        assert result.error == "Execution timed out after 1 seconds"
        assert time.time() - started < 4
        # The session is usable again once the worker has stopped.
        again = await repl.execute("print('alive')")
        assert again.error is None and again.output == "alive\n"

    @pytest.mark.asyncio
    async def test_a_loop_that_catches_exceptions_is_still_stopped(self):
        repl = LocalREPL()
        code = "while True:\n    try:\n        pass\n    except Exception:\n        pass"
        result = await repl.execute(code, timeout=1)
        assert result.error == "Execution timed out after 1 seconds"

    @pytest.mark.asyncio
    async def test_the_event_loop_stays_free_while_code_runs(self):
        import asyncio

        repl = LocalREPL()
        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.05)

        ticker = asyncio.create_task(tick())
        await repl.execute("while True:\n    pass", timeout=1)
        ticker.cancel()
        assert ticks >= 5, "the caller's loop must keep running during an execution"

    @pytest.mark.asyncio
    async def test_blocking_native_call_answers_at_the_timeout_and_holds_the_session(self):
        import asyncio
        import time

        repl = LocalREPL()
        started = time.time()
        result = await repl.execute("import time\ntime.sleep(5)", timeout=1)
        assert "timed out" in result.error
        elapsed = time.time() - started
        if elapsed < 4.5:  # answered before the native call returned: the session is held
            busy = await repl.execute("print(1)")
            assert "still stopping" in busy.error
        await asyncio.sleep(max(0.0, 5.5 - (time.time() - started)))
        assert (await repl.execute("print(1)")).output == "1\n"

    @pytest.mark.asyncio
    async def test_the_same_code_runs_again_each_time(self):
        repl = LocalREPL()
        await repl.execute("n = 0")
        outputs = [(await repl.execute("n += 1\nprint(n)")).output for _ in range(3)]
        assert outputs == ["1\n", "2\n", "3\n"]

    @pytest.mark.asyncio
    async def test_a_result_is_not_reported_by_the_next_call(self):
        repl = LocalREPL()
        first = await repl.execute("result = 42")
        assert "result = 42" in first.output
        second = await repl.execute("print('no result here')")
        assert second.output == "no result here\n"
