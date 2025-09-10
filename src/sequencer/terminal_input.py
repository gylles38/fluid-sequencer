import sys
import termios
import tty

class UserInputCancelled(Exception):
    """Exception raised when the user cancels input."""
    pass

def cancellable_input(prompt: str) -> str:
    """
    A custom input function that allows cancellation with the Escape key.
    """
    # Check if stdin is a tty
    if not sys.stdin.isatty():
        # Fallback to standard input for non-interactive environments (like tests)
        return input(prompt)

    sys.stdout.write(prompt)
    sys.stdout.flush()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        buffer = ""
        while True:
            char = sys.stdin.read(1)

            if char == '\x1b':  # Escape key
                # Handle potential arrow key sequences (ESC [ A/B/C/D)
                next_char = sys.stdin.read(1)
                if next_char == '[':
                    sys.stdin.read(1) # Read the A, B, C, or D
                else:
                    # It was just an escape key press
                    sys.stdout.write('\n')
                    sys.stdout.flush()
                    raise UserInputCancelled()
            elif char in ('\n', '\r'):  # Enter key
                sys.stdout.write('\n')
                sys.stdout.flush()
                break
            elif char in ('\x08', '\x7f'):  # Backspace or Delete
                if buffer:
                    buffer = buffer[:-1]
                    # Move cursor back, write space, move back again
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
            elif char.isprintable():
                buffer += char
                sys.stdout.write(char)
                sys.stdout.flush()

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return buffer
