"""Tests for the setup_instance management command."""

from io import StringIO
from unittest.mock import patch

from django.test import TestCase

from apps.orchestration.management.commands.setup_instance import Command


class PromptChoiceTests(TestCase):
    """Tests for _prompt_choice helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="2")
    def test_returns_selected_option(self, _mock_input):
        result = self.cmd._prompt_choice(
            "Pick one:", [("a", "Option A"), ("b", "Option B"), ("c", "Option C")]
        )
        assert result == "b"

    @patch("builtins.input", side_effect=["0", "5", "2"])
    def test_retries_on_invalid_input(self, _mock_input):
        result = self.cmd._prompt_choice("Pick one:", [("a", "Option A"), ("b", "Option B")])
        assert result == "b"

    @patch("builtins.input", side_effect=["abc", "1"])
    def test_retries_on_non_numeric_input(self, _mock_input):
        result = self.cmd._prompt_choice("Pick one:", [("a", "Option A")])
        assert result == "a"


class PromptMultiTests(TestCase):
    """Tests for _prompt_multi helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1,3")
    def test_returns_selected_options(self, _mock_input):
        result = self.cmd._prompt_multi("Pick:", [("a", "A"), ("b", "B"), ("c", "C")])
        assert result == ["a", "c"]

    @patch("builtins.input", return_value="1, 2, 3")
    def test_handles_spaces_in_input(self, _mock_input):
        result = self.cmd._prompt_multi("Pick:", [("a", "A"), ("b", "B"), ("c", "C")])
        assert result == ["a", "b", "c"]

    @patch("builtins.input", side_effect=["", "1"])
    def test_retries_on_empty_input(self, _mock_input):
        result = self.cmd._prompt_multi("Pick:", [("a", "A"), ("b", "B")])
        assert result == ["a"]


class PromptInputTests(TestCase):
    """Tests for _prompt_input helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="hello")
    def test_returns_user_input(self, _mock_input):
        result = self.cmd._prompt_input("Enter value:")
        assert result == "hello"

    @patch("builtins.input", return_value="")
    def test_returns_default_when_empty(self, _mock_input):
        result = self.cmd._prompt_input("Enter value:", default="fallback")
        assert result == "fallback"

    @patch("builtins.input", side_effect=["", "val"])
    def test_retries_when_required_and_empty(self, _mock_input):
        result = self.cmd._prompt_input("Enter value:", required=True)
        assert result == "val"


class SelectPresetTests(TestCase):
    """Tests for _select_preset step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1")
    def test_select_direct_preset(self, _mock_input):
        preset = self.cmd._select_preset()
        assert preset["name"] == "direct"
        assert preset["has_checkers"] is False
        assert preset["has_intelligence"] is False

    @patch("builtins.input", return_value="4")
    def test_select_full_preset(self, _mock_input):
        preset = self.cmd._select_preset()
        assert preset["name"] == "full"
        assert preset["has_checkers"] is True
        assert preset["has_intelligence"] is True

    @patch("builtins.input", return_value="2")
    def test_select_health_checked_preset(self, _mock_input):
        preset = self.cmd._select_preset()
        assert preset["name"] == "health-checked"
        assert preset["has_checkers"] is True
        assert preset["has_intelligence"] is False

    @patch("builtins.input", return_value="3")
    def test_select_ai_analyzed_preset(self, _mock_input):
        preset = self.cmd._select_preset()
        assert preset["name"] == "ai-analyzed"
        assert preset["has_checkers"] is False
        assert preset["has_intelligence"] is True
