from kivy.core.window import Window
from kivy.uix.textinput import TextInput

def is_any_text_input_focused():
    """
    Recursively walks the window children to check if any TextInput
    (or its subclasses like MDTextField) has focus.
    """
    def walk(widget):
        if isinstance(widget, TextInput) and widget.focus:
            return True
        for child in widget.children:
            if walk(child):
                return True
        return False

    return walk(Window)
