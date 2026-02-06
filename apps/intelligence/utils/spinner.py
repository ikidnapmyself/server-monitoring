"""SpinnerProgress helper class for terminal progress with in-place updates."""

from typing import TextIO


class SpinnerProgress:
    """A helper class for displaying progress in the terminal with a spinner.

    Features:
    - In-place line updates using carriage return (TTY mode)
    - Graceful fallback for non-TTY environments
    - Discovery messages printed on new lines
    - Completion messages with checkmark

    Spinner characters use braille dots for smooth animation.
    """

    # Braille dot spinner characters for smooth animation
    SPINNER_CHARS = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
    CHECKMARK = "\u2713"

    def __init__(self, output: TextIO, is_tty: bool = True):
        """Initialize the spinner progress helper.

        Args:
            output: The output stream to write to (e.g., sys.stdout)
            is_tty: Whether the output is a TTY (enables in-place updates)
        """
        self.output = output
        self.is_tty = is_tty
        self._spinner_index = 0
        self._last_line_length = 0

    def _get_spinner_char(self) -> str:
        """Get the next spinner character and advance the index."""
        char = self.SPINNER_CHARS[self._spinner_index]
        self._spinner_index = (self._spinner_index + 1) % len(self.SPINNER_CHARS)
        return char

    def _clear_line(self) -> None:
        """Clear the current line (TTY mode only)."""
        if self.is_tty and self._last_line_length > 0:
            # Move to start of line and clear with spaces
            self.output.write("\r" + " " * self._last_line_length + "\r")
            self.output.flush()

    def start(self, msg: str) -> None:
        """Print the initial message.

        Args:
            msg: The initial message to display
        """
        if self.is_tty:
            spinner = self._get_spinner_char()
            line = f"{spinner} {msg}"
            self.output.write(line)
            self._last_line_length = len(line)
            self.output.flush()
        else:
            self.output.write(f"{msg}\n")
            self.output.flush()

    def update(self, msg: str) -> None:
        """Update the current line with a new message and spinner.

        In TTY mode, uses carriage return to overwrite the current line.
        In non-TTY mode, this is a no-op to avoid spamming output.

        Args:
            msg: The message to display
        """
        if self.is_tty:
            spinner = self._get_spinner_char()
            line = f"\r{spinner} {msg}"
            self.output.write(line)
            self._last_line_length = len(line) - 1  # Exclude \r from length
            self.output.flush()
        # Non-TTY: Don't output anything for updates to avoid spam

    def found(self, msg: str) -> None:
        """Print a discovery message on a new line.

        Discoveries are always printed (even in non-TTY mode) and are
        indented with 2 spaces for visual distinction.

        Args:
            msg: The discovery message to display
        """
        if self.is_tty:
            self._clear_line()
        self.output.write(f"  {msg}\n")
        self._last_line_length = 0
        self.output.flush()

    def finish(self, msg: str) -> None:
        """Print the completion message with a checkmark.

        Args:
            msg: The completion message to display
        """
        if self.is_tty:
            self._clear_line()
        self.output.write(f"{self.CHECKMARK} {msg}\n")
        self._last_line_length = 0
        self.output.flush()
