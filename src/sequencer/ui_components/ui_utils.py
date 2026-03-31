from kivy.core.window import Window
from kivy.uix.textinput import TextInput
from kivy.logger import Logger
from kivy.clock import Clock
import weakref
import time

class CursorManager:
    """
    Centralized manager to handle cursor changes.
    It debounces requests and applies only the latest request per frame
    to prevent backend fighting and potential deadlocks.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CursorManager, cls).__new__(cls)
            cls._instance.pending_cursor = None
            cls._instance.current_cursor = 'arrow'
            cls._instance._apply_event = None
        return cls._instance

    def request_cursor(self, cursor_name):
        from kivy.logger import Logger
        # Logger.info(f"CursorManager: request_cursor(name={cursor_name})")
        if self.current_cursor == cursor_name:
            # Important: Still need to clear any pending that might be different
            self.pending_cursor = None
            return

        # If a different cursor was already requested this frame,
        # this new one will overwrite it.
        self.pending_cursor = cursor_name
        if not self._apply_event:
            # Logger.info(f"CursorManager: scheduling apply_cursor")
            self._apply_event = Clock.schedule_once(self._apply_cursor, 0)

    def _apply_cursor(self, dt):
        from kivy.logger import Logger
        # Logger.info(f"CursorManager: _apply_cursor entry (pending={self.pending_cursor})")
        self._apply_event = None
        if self.pending_cursor and self.pending_cursor != self.current_cursor:
            try:
                Logger.info(f"CursorManager: APPLYING system cursor: {self.pending_cursor}")
                Window.set_system_cursor(self.pending_cursor)
                self.current_cursor = self.pending_cursor
            except Exception as e:
                Logger.error(f"CursorManager: Failed to set cursor {self.pending_cursor}: {e}")
        self.pending_cursor = None

def set_safe_cursor(cursor_name):
    """Sets the system cursor using the centralized CursorManager."""
    CursorManager().request_cursor(cursor_name)


class GlobalHoverManager:
    """
    Centralized manager for hover events to reduce the number of listeners on Window.mouse_pos.
    This significantly improves performance and stability during layout changes (like re-parenting).
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GlobalHoverManager, cls).__new__(cls)
            cls._instance.widgets = [] # List of weakref.ref
            cls._instance._last_pos = (0, 0)
            cls._instance._last_dispatch_time = 0
            cls._instance._bound = False
            cls._instance._dispatch_event = None
        return cls._instance

    def register(self, widget):
        # Use weakref to avoid memory leaks
        ref = weakref.ref(widget)
        if ref not in self.widgets:
            self.widgets.append(ref)
        self._ensure_bound()

    def unregister(self, widget):
        # Explicitly remove the widget from the list
        self.widgets = [ref for ref in self.widgets if ref() is not widget and ref() is not None]

    def _ensure_bound(self):
        if not self._bound:
            Window.bind(mouse_pos=self._on_mouse_pos)
            self._bound = True

    def _on_mouse_pos(self, window, pos):
        self._last_pos = pos
        curr_time = time.time()

        # Throttle to ~50 FPS for hover checks
        if curr_time - self._last_dispatch_time < 0.02:
            if not self._dispatch_event:
                self._dispatch_event = Clock.schedule_once(self._do_dispatch, 0.02)
            return

        self._do_dispatch(0)

    def _do_dispatch(self, dt):
        from kivy.logger import Logger
        # Logger.info(f"GlobalHoverManager: _do_dispatch entry")
        self._dispatch_event = None
        self._last_dispatch_time = time.time()

        # Performance: Clear cursor pending if it matches current at start of dispatch
        # (Though CursorManager handles this, it's a good extra guard)

        still_alive = []
        # Copy list for safe iteration
        # Optimization: Sort widgets by depth to handle occlusion correctly
        # (Actually, HoverBehavior already handles occlusion by checking root.children)

        for ref in list(self.widgets):
            widget = ref()
            if widget:
                try:
                    # Direct call to the widget's internal hover handler
                    if hasattr(widget, '_on_mouse_pos_internal'):
                        widget._on_mouse_pos_internal(self._last_pos)
                    still_alive.append(ref)
                except Exception as e:
                    # Logger.error(f"GlobalHoverManager: Error dispatching to {widget}: {e}")
                    pass

        self.widgets = still_alive

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
