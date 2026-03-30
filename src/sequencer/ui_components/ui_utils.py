from kivy.core.window import Window
from kivy.uix.textinput import TextInput
from kivy.logger import Logger

def set_safe_cursor(cursor_name):
    """Sets the system cursor only if it's different from the current one."""
    if not hasattr(Window, '_current_cursor'):
        # Initialiser avec la valeur actuelle réelle si possible, sinon par défaut
        Window._current_cursor = 'arrow'

    if Window._current_cursor != cursor_name:
        try:
            # Logger.info(f"UIUtils: Changing cursor from {Window._current_cursor} to {cursor_name}")
            Window.set_system_cursor(cursor_name)
            Window._current_cursor = cursor_name
        except Exception as e:
            Logger.error(f"UIUtils: Failed to set cursor {cursor_name}: {e}")

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
