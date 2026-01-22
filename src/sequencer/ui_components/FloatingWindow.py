from kivy.uix.relativelayout import RelativeLayout
from kivymd.uix.card import MDCard
from kivymd.uix.boxlayout import MDBoxLayout
from kivy.properties import StringProperty, BooleanProperty, ObjectProperty
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.clock import Clock

Builder.load_string("""
<FloatingWindow>:
    size_hint: None, None
    size: dp(800), dp(600)

    # Main window body using standard BoxLayout to avoid MDCard touch issues
    BoxLayout:
        id: card
        _is_internal_widget: True
        orientation: 'vertical'
        pos: 0, 0
        size: root.size
        canvas.before:
            Color:
                rgba: 0.1, 0.1, 0.1, 1
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [dp(8),]
            # Simple border
            Color:
                rgba: 0.3, 0.3, 0.3, 1
            Line:
                width: 1
                rounded_rectangle: (self.x, self.y, self.width, self.height, dp(8))

        # Title Bar
        MDBoxLayout:
            id: title_bar
            size_hint_y: None
            height: dp(40)
            md_bg_color: 0.15, 0.15, 0.15, 1
            padding: [dp(10), 0]
            # Manual radius simulation for background
            canvas.before:
                Color:
                    rgba: 0.15, 0.15, 0.15, 1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [dp(8), dp(8), 0, 0]

            MDLabel:
                text: root.title
                theme_text_color: "Custom"
                text_color: 1, 1, 1, 1
                bold: True
                valign: 'middle'

            MDIconButton:
                icon: 'window-maximize' if not root.is_maximized else 'window-restore'
                theme_icon_color: "Custom"
                icon_color: 1, 1, 1, 1
                on_release: root.toggle_maximize()

            MDIconButton:
                icon: 'close'
                theme_icon_color: "Custom"
                icon_color: 1, 1, 1, 1
                on_release: root.dismiss()

        # Content area
        BoxLayout:
            id: window_content

    # Resize handle icon (moved last to be in front)
    MDIcon:
        id: resize_handle
        _is_internal_widget: True
        icon: 'resize-bottom-right'
        theme_text_color: "Custom"
        text_color: 1, 1, 1, 0.9
        size_hint: None, None
        size: dp(24), dp(24)
        # Force initial position in KV using root properties
        x: root.width - self.width
        y: 0
""")

class FloatingWindow(RelativeLayout):
    title = StringProperty("Window")
    is_maximized = BooleanProperty(False)
    _prev_state = ObjectProperty(None, allownone=True)
    _touch_lock = BooleanProperty(True)

    def __init__(self, **kwargs):
        self.register_event_type('on_open')
        self.register_event_type('on_dismiss')
        super().__init__(**kwargs)
        self._drag_mode = None
        # Unlock touch after a short delay to prevent Accidental triggers on open (like double-click second tap)
        Clock.schedule_once(lambda dt: setattr(self, '_touch_lock', False), 0.3)
        # Force handle repositioning after first layout and on size change
        self.bind(size=self._ensure_handle_pos)
        Clock.schedule_once(self._ensure_handle_pos, 0.1)

    def _ensure_handle_pos(self, *args):
        if hasattr(self, 'ids'):
            if 'resize_handle' in self.ids:
                # Use absolute positioning relative to self
                self.ids.resize_handle.x = self.width - self.ids.resize_handle.width
                self.ids.resize_handle.y = 0
            # Ensure card follows root size exactly
            if 'card' in self.ids:
                self.ids.card.size = self.size
                self.ids.card.pos = (0, 0)

    def add_widget(self, widget, index=0, canvas=None):
        # redirection logic: internal widgets go to self, others go to window_content
        if hasattr(widget, '_is_internal_widget'):
            super().add_widget(widget, index, canvas)
            return

        if hasattr(self, 'ids') and 'window_content' in self.ids:
            self.ids.window_content.add_widget(widget, index, canvas)
        else:
            super().add_widget(widget, index, canvas)

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False

        if self._touch_lock:
            return True

        local_pos = self.to_local(*touch.pos)

        # 1. Check resize handle first (highest priority)
        if not self.is_maximized and self.ids.resize_handle.collide_point(*local_pos):
            touch.grab(self)
            self._drag_mode = 'resize'
            Clock.schedule_once(lambda dt: self.bring_to_front(), 0)
            return True

        # 2. Special handling for title bar to ensure buttons work and dragging is robust
        if local_pos[1] >= self.height - dp(40):
            # Try to dispatch to buttons manually as it proved more robust
            # title_bar is at (0, height-40) in FloatingWindow space
            original_pos = touch.pos
            touch.pos = local_pos
            handled = self.ids.title_bar.on_touch_down(touch)
            touch.pos = original_pos

            if handled:
                Clock.schedule_once(lambda dt: self.bring_to_front(), 0)
                return True

            # If no button handled it, it's a drag
            if not self.is_maximized:
                touch.grab(self)
                self._drag_mode = 'drag'
                Clock.schedule_once(lambda dt: self.bring_to_front(), 0)
                return True
            else:
                Clock.schedule_once(lambda dt: self.bring_to_front(), 0)
                return True

        # 3. Normal content interaction
        if super().on_touch_down(touch):
            Clock.schedule_once(lambda dt: self.bring_to_front(), 0)
            return True

        # Background click also brings to front and consumes touch
        Clock.schedule_once(lambda dt: self.bring_to_front(), 0)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)

        if self._drag_mode == 'drag':
            if self.is_maximized:
                # dragging while maximized restores it
                self.toggle_maximize()
                # Center it around mouse if possible, or just restore
                # For now, simple restore is safer.

            self.x += touch.dx
            self.y += touch.dy
        elif self._drag_mode == 'resize':
            # Relative to parent (assuming parent starts at 0,0 like window_manager)
            target_width = max(dp(400), touch.x - self.x)
            target_height = max(dp(300), self.top - touch.y)

            # To keep top edge fixed while pulling bottom-right handle
            delta_h = target_height - self.height
            self.height = target_height
            self.y -= delta_h

            self.width = target_width
        return True

    def toggle_maximize(self):
        if not self.parent:
            return

        if not self.is_maximized:
            # Store current state
            self._prev_state = (self.pos[:], self.size[:])
            # Maximize
            self.pos = (0, 0)
            self.size = self.parent.size
            self.is_maximized = True
            # Hide resize handle when maximized
            self.ids.resize_handle.opacity = 0
        else:
            # Restore
            if self._prev_state:
                self.pos, self.size = self._prev_state
            self.is_maximized = False
            # Show resize handle
            self.ids.resize_handle.opacity = 1

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._drag_mode = None
            return True
        return super().on_touch_up(touch)

    def bring_to_front(self):
        if self.parent and self.parent.children[0] is not self:
            parent = self.parent
            parent.remove_widget(self)
            parent.add_widget(self)

    def open(self, *args):
        from kivy.app import App
        app = App.get_running_app()

        # Try to find window_manager in the root layout
        target = None
        if hasattr(app.root, 'window_manager'):
            target = app.root.window_manager

        if target:
            if self not in target.children:
                target.add_widget(self)
                # Center it if not positioned, with a small offset for each new window
                if self.pos == [0, 0]:
                    # Find how many windows are already open to offset the new one
                    num_windows = len([c for c in target.children if isinstance(c, FloatingWindow)])
                    offset = (num_windows - 1) * dp(30)
                    self.center = target.center
                    self.x += offset
                    self.y -= offset
                self.dispatch('on_open')
        else:
            # Fallback to adding to Window
            if self not in Window.children:
                Window.add_widget(self)
                if self.pos == [0, 0]:
                    self.center = Window.center
                self.dispatch('on_open')

    def dismiss(self, *args):
        self.dispatch('on_dismiss')
        if self.parent:
            self.parent.remove_widget(self)

    def on_open(self):
        pass

    def on_dismiss(self):
        pass
