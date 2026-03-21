from kivy.core.window import Window
from kivymd.uix.button import MDButton
from kivy.uix.button import Button

class HoverBehavior:
    """
    A Kivy mixin class that adds hover behavior to a widget. It changes the
    system cursor to a 'hand' when the mouse enters and back to an 'arrow'
    when it leaves.
    """
    def __init__(self, *args, **kwargs):
        self.register_event_type('on_enter')
        self.register_event_type('on_leave')
        self.hovered = False
        super().__init__(*args, **kwargs)
        self.bind(on_parent=self._on_hover_parent)

    def _on_hover_parent(self, instance, parent):
        Window.unbind(mouse_pos=self._on_mouse_pos)
        if parent is not None:
            Window.bind(mouse_pos=self._on_mouse_pos)
        else:
            if self.hovered:
                self.hovered = False
                self.dispatch('on_leave')

    def _on_mouse_pos(self, *args):
        # Basic visibility and tree presence check
        if not self.get_root_window() or getattr(self, 'disabled', False) or getattr(self, 'opacity', 1) < 0.01 or self.width <= 0 or self.height <= 0:
            if self.hovered:
                self.hovered = False
                self.dispatch('on_leave')
            return

        # Deep visibility check: Ensure all ancestors are enabled and visible
        p = self.parent
        while p:
            if getattr(p, 'disabled', False) or getattr(p, 'opacity', 1) < 0.01:
                if self.hovered:
                    self.hovered = False
                    self.dispatch('on_leave')
                return
            p = p.parent

        pos = args[1]
        # Use absolute window coordinates for robust collision detection
        wx, wy = self.to_window(0, 0)
        inside = (wx <= pos[0] <= wx + self.width) and \
                 (wy <= pos[1] <= wy + self.height)

        if inside and not self.hovered:
            self.hovered = True
            self.dispatch('on_enter')
        elif not inside and self.hovered:
            self.hovered = False
            self.dispatch('on_leave')

    def on_enter(self, *args):
        """Called when the mouse enters the widget area."""
        Window.set_system_cursor('hand')

        # Only report if we have a midi_command to learn
        if not hasattr(self, 'midi_command') or not self.midi_command:
            return

        from kivymd.app import MDApp
        app = MDApp.get_running_app()
        if not app: return

        # Try to get layout from app instance directly
        layout = getattr(app, 'sequencer_layout', None)
        if not layout and hasattr(app, 'sequencer_layout'):
            layout = app.sequencer_layout

        # Fallback: search in root
        if not layout and hasattr(app, 'root'):
            if app.root.__class__.__name__ == 'SequencerLayout':
                layout = app.root
            elif hasattr(app.root, 'sequencer_layout'):
                layout = app.root.sequencer_layout
            else:
                def find_layout(widget):
                    if widget.__class__.__name__ == 'SequencerLayout':
                        return widget
                    if hasattr(widget, 'children'):
                        for child in widget.children:
                            res = find_layout(child)
                            if res: return res
                    return None
                layout = find_layout(app.root)

        if layout:
            layout.report_hover(self, True)

    def on_leave(self, *args):
        """Called when the mouse leaves the widget area."""
        Window.set_system_cursor('arrow')

        # Only report if we have a midi_command to learn
        if not hasattr(self, 'midi_command') or not self.midi_command:
            return

        from kivymd.app import MDApp
        app = MDApp.get_running_app()
        if not app: return

        layout = getattr(app, 'sequencer_layout', None)
        if not layout and hasattr(app, 'sequencer_layout'):
            layout = app.sequencer_layout

        if not layout and hasattr(app, 'root'):
            if app.root.__class__.__name__ == 'SequencerLayout':
                layout = app.root
            elif hasattr(app.root, 'sequencer_layout'):
                layout = app.root.sequencer_layout
            else:
                def find_layout(widget):
                    if widget.__class__.__name__ == 'SequencerLayout':
                        return widget
                    if hasattr(widget, 'children'):
                        for child in widget.children:
                            res = find_layout(child)
                            if res: return res
                    return None
                layout = find_layout(app.root)

        if layout:
            layout.report_hover(self, False)

class HoverableMDButton(MDButton, HoverBehavior):
    pass

class HoverableButton(Button, HoverBehavior):
    pass
