from kivy.core.window import Window
from kivy.uix.textinput import TextInput

def is_any_text_input_focused():
    """
    Checks if any TextInput (or its subclasses like MDTextField) has focus.
    Uses the window's focus_widget for better performance and safety.
    """
    # Use focus_widget instead of full tree traversal
    focused = Window.focus_widget
    return isinstance(focused, TextInput)
