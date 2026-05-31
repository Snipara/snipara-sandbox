"""Tests for agent prompts."""

from rlm.agent.prompts import AGENT_SYSTEM_PROMPT, build_iteration_prompt


class TestAgentSystemPrompt:
    """Tests for the system prompt."""

    def test_contains_key_instructions(self):
        assert "FINAL" in AGENT_SYSTEM_PROMPT
        assert "FINAL_VAR" in AGENT_SYSTEM_PROMPT
        assert "execute_python" in AGENT_SYSTEM_PROMPT
        assert "file_search" in AGENT_SYSTEM_PROMPT
        assert "terminate" in AGENT_SYSTEM_PROMPT.lower()

    def test_includes_targeted_reading_guardrails(self):
        assert "Do not recursively list the repository" in AGENT_SYSTEM_PROMPT
        assert "Prefer file_search over list_files" in AGENT_SYSTEM_PROMPT

    def test_mentions_snipara_tools(self):
        assert "snipara_context_query" in AGENT_SYSTEM_PROMPT
        assert "snipara_remember" in AGENT_SYSTEM_PROMPT


class TestBuildIterationPrompt:
    """Tests for build_iteration_prompt."""

    def test_includes_task(self):
        prompt = build_iteration_prompt(
            task="What is 2+2?",
            iteration=0,
            max_iterations=10,
            previous_actions=[],
        )
        assert "What is 2+2?" in prompt

    def test_includes_iteration_count(self):
        prompt = build_iteration_prompt(
            task="test",
            iteration=2,
            max_iterations=10,
            previous_actions=[],
        )
        assert "3/10" in prompt  # 0-based to 1-based

    def test_includes_previous_actions(self):
        prompt = build_iteration_prompt(
            task="test",
            iteration=2,
            max_iterations=10,
            previous_actions=["Did step 1", "Did step 2"],
        )
        assert "Did step 1" in prompt
        assert "Did step 2" in prompt

    def test_includes_budget(self):
        prompt = build_iteration_prompt(
            task="test",
            iteration=0,
            max_iterations=10,
            previous_actions=[],
            remaining_budget=5000,
        )
        assert "5000" in prompt

    def test_warning_on_final_iteration(self):
        prompt = build_iteration_prompt(
            task="test",
            iteration=9,
            max_iterations=10,
            previous_actions=[],
        )
        assert "FINAL" in prompt
        assert "MUST" in prompt

    def test_no_warning_before_final(self):
        prompt = build_iteration_prompt(
            task="test",
            iteration=5,
            max_iterations=10,
            previous_actions=[],
        )
        assert "MUST call FINAL" not in prompt

    def test_limits_previous_actions_to_five(self):
        actions = [f"Action {i}" for i in range(10)]
        prompt = build_iteration_prompt(
            task="test",
            iteration=10,
            max_iterations=20,
            previous_actions=actions,
        )
        # Should only include last 5
        assert "Action 5" in prompt
        assert "Action 9" in prompt
        assert "Action 0" not in prompt
