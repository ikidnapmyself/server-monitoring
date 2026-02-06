"""Tests for the SpinnerProgress utility class."""

import io

from apps.intelligence.utils.spinner import SpinnerProgress


class TestSpinnerProgress:
    """Tests for the SpinnerProgress helper class."""

    def test_spinner_update_overwrites_line(self):
        """Spinner update should use carriage return to overwrite."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=True, tty_output=output)
        spinner.update("Processing... 10 files")
        spinner.update("Processing... 20 files")
        assert "\r" in output.getvalue()

    def test_spinner_found_prints_on_new_line(self):
        """Found items should print on their own line."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=True, tty_output=output)
        spinner.update("Scanning...")
        spinner.found("Found: /path/file.log (100 MB)")
        assert "Found: /path/file.log" in output.getvalue()
        assert "\n" in output.getvalue()

    def test_spinner_finish_shows_checkmark(self):
        """Finish should show completion with checkmark."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=True, tty_output=output)
        spinner.finish("Done, found 3 items")
        assert "\u2713" in output.getvalue()

    def test_spinner_non_tty_fallback(self):
        """Non-TTY should fall back to simple output without \\r."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=False)
        spinner.update("Processing...")
        spinner.update("Processing...")
        assert output.getvalue().count("\r") == 0

    def test_spinner_start_prints_initial_message(self):
        """Start should print the initial message."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=True, tty_output=output)
        spinner.start("Starting scan...")
        assert "Starting scan..." in output.getvalue()

    def test_spinner_cycles_through_braille_chars(self):
        """Spinner should cycle through braille dot characters."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=True, tty_output=output)
        # Call update multiple times to cycle through spinner chars
        for _ in range(12):
            spinner.update("Testing...")

        content = output.getvalue()
        # Should contain at least some of the braille spinner chars
        braille_chars = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
        assert any(char in content for char in braille_chars)

    def test_spinner_found_indents_with_two_spaces(self):
        """Found messages should be indented with 2 spaces."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=True, tty_output=output)
        spinner.found("Test discovery")
        assert "  " in output.getvalue()

    def test_spinner_non_tty_start_prints_message(self):
        """Non-TTY start should print the message."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=False)
        spinner.start("Starting...")
        assert "Starting..." in output.getvalue()

    def test_spinner_non_tty_found_prints_message(self):
        """Non-TTY found should print discoveries."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=False)
        spinner.found("Found item")
        assert "Found item" in output.getvalue()

    def test_spinner_non_tty_finish_shows_checkmark(self):
        """Non-TTY finish should also show checkmark."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=False)
        spinner.finish("Complete")
        assert "\u2713" in output.getvalue()

    def test_spinner_clears_line_before_found(self):
        """In TTY mode, found should clear the spinner line first."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=True, tty_output=output)
        spinner.update("Scanning...")
        spinner.found("Discovery!")
        # Should have cleared line (spaces) before printing found
        content = output.getvalue()
        assert "\n" in content

    def test_spinner_clears_line_before_finish(self):
        """In TTY mode, finish should clear the spinner line."""
        output = io.StringIO()
        spinner = SpinnerProgress(output, is_tty=True, tty_output=output)
        spinner.update("Working...")
        spinner.finish("Done!")
        content = output.getvalue()
        # Should have newline for clean output
        assert "\n" in content
