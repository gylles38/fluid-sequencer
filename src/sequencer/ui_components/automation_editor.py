from kivy.uix.modalview import ModalView
from kivy.lang import Builder
from kivy.properties import ObjectProperty, NumericProperty, StringProperty, BooleanProperty, ListProperty
from . import TooltipMDIconButton, Ruler, AutomationControls
from sequencer.models import AutomationTrack, MidiTrack
import copy
from kivy.core.window import Window
from kivy.clock import Clock
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.divider import MDDivider
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.boxlayout import BoxLayout
from .HoverBehavior import HoverableButton
from kivy.graphics import Color, Line, Rectangle, Mesh
from kivy.metrics import dp
import math
from collections import deque
from kivy.uix.dropdown import DropDown
from kivy.uix.button import Button
from .SaveDiscardCancelPopup import SaveDiscardCancelPopup


# --- Easing Functions (copied from AutomationCurve.py) ---
def _interp_linear(t):
    return t

def _interp_ease_in_quad(t):
    return t * t

def _interp_ease_out_quad(t):
    return t * (2 - t)

def _interp_ease_in_out_quad(t):
    t *= 2
    if t < 1:
        return 0.5 * t * t
    t -= 1
    return -0.5 * (t * (t - 2) - 1)

def _interp_sine(t):
    return 0.5 * (1 - math.cos(t * math.pi))

def _get_interp_func(curve_type):
    if curve_type == "linear": return _interp_linear
    elif curve_type == "ease-in": return _interp_ease_in_quad
    elif curve_type == "ease-out": return _interp_ease_out_quad
    elif curve_type == "ease-in-out": return _interp_ease_in_out_quad
    elif curve_type == "sine": return _interp_sine
    else: return None


class EditHistoryManager:
    """Manages undo/redo history using a single list and an index."""
    def __init__(self, max_history=31):
        self.history = deque(maxlen=max_history)
        self.index = -1

    def record_state(self, state):
        if self.index < len(self.history) - 1:
            self.history = deque(list(self.history)[:self.index + 1], maxlen=self.history.maxlen)
        self.history.append(state)
        self.index = len(self.history) - 1

    def undo(self):
        if self.can_undo():
            self.index -= 1
            return self.history[self.index]
        return None

    def redo(self):
        if self.can_redo():
            self.index += 1
            return self.history[self.index]
        return None

    def can_undo(self) -> bool:
        return self.index > 0

    def can_redo(self) -> bool:
        return self.index < len(self.history) - 1


class AutomationValueAxis(Widget):
    min_val = NumericProperty(0.0)
    max_val = NumericProperty(1.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.draw, size=self.draw, min_val=self.draw, max_val=self.draw)
        self.labels = []

    def draw(self, *args):
        self.canvas.clear()
        self.clear_widgets()
        self.labels.clear()

        with self.canvas:
            Color(0.2, 0.2, 0.2, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0.4, 0.4, 0.4, 1)
            Line(points=[self.right, self.y, self.right, self.top], width=1)

        # Draw labels based on the range
        v_range = self.max_val - self.min_val
        if v_range == 0: return

        def add_label(value, y_align, text=None):
            if text is None: text = f"{value:.1f}"
            y_pos = self.y + ((value - self.min_val) / v_range) * self.height

            if y_align == 'bottom':
                y_pos = self.y
            elif y_align == 'top':
                y_pos = self.top - dp(16)
            else: # Center
                y_pos -= dp(8)

            label = Label(
                text=text,
                font_size='10sp',
                pos=(self.x, y_pos),
                size=(self.width - dp(4), dp(16)),
                halign='right',
                valign='middle',
                color=(0.8, 0.8, 0.8, 1)
            )
            self.labels.append(label)
            self.add_widget(label)

        add_label(self.max_val, y_align='top')
        add_label(self.min_val, y_align='bottom')
        if self.min_val < 0 < self.max_val:
            add_label(0.0, y_align='center')


class EditableAutomationGrid(Widget):
    editor = ObjectProperty()
    points = ListProperty([])
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128.0)
    min_val = NumericProperty(0.0)
    max_val = NumericProperty(1.0)
    beats_per_measure = NumericProperty(4)
    _dragged_point = ObjectProperty(None, allownone=True)
    _drag_offset = (0, 0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.draw, size=self.draw, points=self.draw,
                  pixels_per_beat=self.draw, total_beats=self.draw,
                  min_val=self.draw_curve_and_points, max_val=self.draw_curve_and_points)

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)

        local_pos = self.to_local(*touch.pos)
        clicked_beat = (local_pos[0] - self.x) / self.pixels_per_beat

        v_range = self.max_val - self.min_val
        if v_range == 0: v_range = 1
        clicked_value = self.min_val + ((local_pos[1] - self.y) / self.height) * v_range


        edit_mode = self.editor.edit_mode

        # Find clicked point
        clicked_point = None
        for point in self.points:
            point_x = self.x + point.start_time * self.pixels_per_beat
            point_y = self.y + ((point.value - self.min_val) / v_range) * self.height
            if abs(local_pos[0] - point_x) < dp(8) and abs(local_pos[1] - point_y) < dp(8):
                clicked_point = point
                break

        if touch.button == 'right':
            if clicked_point:
                self.editor.show_curve_type_popup(clicked_point, touch)
            return True

        if edit_mode == 'insert':
            quantized_beat = round(clicked_beat * 4) / 4 # Snap to 16th
            self.editor.add_point(quantized_beat, clicked_value)
            return True

        elif edit_mode == 'delete':
            if clicked_point:
                self.editor.delete_point(clicked_point)
            return True

        elif edit_mode == 'move':
            if clicked_point:
                self.editor.selected_point = clicked_point
                self._dragged_point = clicked_point
                self._drag_offset = (local_pos[0] - (self.x + clicked_point.start_time * self.pixels_per_beat)), \
                                    (local_pos[1] - (self.y + ((clicked_point.value - self.min_val) / v_range) * self.height))
                touch.grab(self)
            else:
                self.editor.selected_point = None
            return True

        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)

        if self._dragged_point:
            local_pos = self.to_local(*touch.pos)

            # --- Time (X-axis) Calculation ---
            new_x = local_pos[0] - self._drag_offset[0]
            new_beat = (new_x - self.x) / self.pixels_per_beat
            quantized_beat = round(new_beat * 4) / 4 # Snap to 16th
            self._dragged_point.start_time = max(0, quantized_beat)

            # --- Value (Y-axis) Calculation ---
            v_range = self.max_val - self.min_val
            if v_range == 0: v_range = 1
            new_y = local_pos[1] - self._drag_offset[1]
            new_value_normalized = (new_y - self.y) / self.height
            new_value = self.min_val + new_value_normalized * v_range
            self._dragged_point.value = max(self.min_val, min(self.max_val, new_value))

            self.editor.is_dirty = True
            self.draw_curve_and_points()
            return True

        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_up(touch)

        if self._dragged_point:
            self._dragged_point = None
            touch.ungrab(self)
            self.editor._record_state()
            return True

        return super().on_touch_up(touch)


    def draw(self, *args):
        self.canvas.before.clear()
        with self.canvas.before:
            # Background
            Color(0.1, 0.1, 0.1, 1)
            Rectangle(pos=self.pos, size=self.size)

            # --- Grid Lines ---
            # Vertical lines (beats)
            Color(0.2, 0.2, 0.2, 1)
            for i in range(int(self.total_beats) + 1):
                x = self.x + i * self.pixels_per_beat
                if x > self.right: break
                is_measure = i % self.beats_per_measure == 0
                Line(points=[x, self.y, x, self.top], width=1.5 if is_measure else 0.5)

            # Horizontal lines (values)
            num_h_lines = 10
            for i in range(num_h_lines + 1):
                y = self.y + (i / num_h_lines) * self.height
                Line(points=[self.x, y, self.right, y], width=0.5)

        self.draw_curve_and_points()

    def draw_curve_and_points(self, *args):
        self.canvas.clear()
        if not self.points: return

        v_range = self.max_val - self.min_val
        if v_range == 0: v_range = 1

        def normalize(val):
            return (val - self.min_val) / v_range

        sorted_points = sorted(self.points, key=lambda p: p.start_time)

        with self.canvas:
            # --- Draw Curve ---
            Color(0.5, 0.5, 0.8, 0.4)
            vertices, indices, v_index = [], [], 0

            first_p = sorted_points[0]
            first_x = self.x + first_p.start_time * self.pixels_per_beat
            if first_x > self.x:
                vertices.extend([self.x, self.y, 0, 0, self.x, self.y, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2
                vertices.extend([first_x, self.y, 0, 0, first_x, self.y, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2

            for i in range(len(sorted_points)):
                p1 = sorted_points[i]
                x1 = self.x + p1.start_time * self.pixels_per_beat
                y1 = self.y + (normalize(p1.value) * self.height)

                vertices.extend([x1, self.y, 0, 0, x1, y1, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2

                if i < len(sorted_points) - 1:
                    p2 = sorted_points[i+1]
                    x2 = self.x + p2.start_time * self.pixels_per_beat

                    if p1.curve != "none":
                        interp_func = _get_interp_func(p1.curve)
                        num_steps = max(2, min(100, int((x2 - x1) / 5)))
                        for step in range(1, num_steps):
                            t = step / num_steps
                            curr_x = x1 + t * (x2 - x1)
                            real_val = p1.value + interp_func(t) * (p2.value - p1.value)
                            curr_y = self.y + (normalize(real_val) * self.height)
                            vertices.extend([curr_x, self.y, 0, 0, curr_x, curr_y, 0, 0])
                            indices.extend([v_index, v_index + 1])
                            v_index += 2
                    else:
                        vertices.extend([x2, self.y, 0, 0, x2, y1, 0, 0])
                        indices.extend([v_index, v_index + 1])
                        v_index += 2

            last_p = sorted_points[-1]
            last_x = self.x + last_p.start_time * self.pixels_per_beat
            final_x = self.x + self.total_beats * self.pixels_per_beat
            if last_x < final_x:
                y_last = self.y + (normalize(last_p.value) * self.height)
                vertices.extend([final_x, self.y, 0, 0, final_x, y_last, 0, 0])
                indices.extend([v_index, v_index + 1])

            Mesh(vertices=vertices, indices=indices, mode='triangle_strip')


            # --- Draw Lines & Points ---
            Color(0.8, 0.8, 1, 0.9)
            point_radius = dp(4)
            for i in range(len(sorted_points)):
                p1 = sorted_points[i]
                x1 = self.x + p1.start_time * self.pixels_per_beat
                y1 = self.y + normalize(p1.value) * self.height

                # Draw point
                Rectangle(pos=(x1 - point_radius, y1 - point_radius), size=(point_radius * 2, point_radius * 2))

                # Draw line to next point
                if i < len(sorted_points) - 1:
                    p2 = sorted_points[i+1]
                    x2 = self.x + p2.start_time * self.pixels_per_beat
                    y2 = self.y + normalize(p2.value) * self.height
                    Line(points=[x1, y1, x2, y2], width=1.2)


Builder.load_string("""
<AutomationEditor>:
    size_hint: 0.9, 0.9
    auto_dismiss: False

    MDBoxLayout:
        orientation: 'vertical'

        # Top Toolbar
        MDBoxLayout:
            id: toolbar
            size_hint_y: None
            height: dp(56)
            padding: dp(8)
            spacing: dp(8)
            md_bg_color: 0.2, 0.2, 0.2, 1

            AutomationControls:
                id: automation_controls
                size_hint_x: None
                width: dp(200) # Adjust as needed
                pos_hint: {'center_y': 0.5}

            MDDivider:
                orientation: 'vertical'

            Label:
                text: "Modes:"
                size_hint_x: None
                width: self.texture_size[0]

            TooltipMDIconButton:
                id: insert_button
                icon: 'pencil'
                tooltip_text: "Insert Mode (Ctrl+I)"
                theme_bg_color: "Custom"
                on_press: root.set_edit_mode('insert', self)
            TooltipMDIconButton:
                id: move_button
                icon: 'cursor-move'
                tooltip_text: "Move Mode (Ctrl+M)"
                theme_bg_color: "Custom"
                on_press: root.set_edit_mode('move', self)
            TooltipMDIconButton:
                id: delete_button
                icon: 'eraser'
                tooltip_text: "Delete Mode (Ctrl+D)"
                theme_bg_color: "Custom"
                on_press: root.set_edit_mode('delete', self)

            MDDivider:
                orientation: 'vertical'

            TooltipMDIconButton:
                id: undo_button
                icon: 'undo'
                tooltip_text: "Undo (Ctrl+Z)"
                on_press: root.undo()
                disabled: True
            TooltipMDIconButton:
                id: redo_button
                icon: 'redo'
                tooltip_text: "Redo (Ctrl+Y)"
                on_press: root.redo()
                disabled: True

            Widget:
                size_hint_x: 1

            MDBoxLayout:
                adaptive_width: True
                spacing: dp(4)

                TooltipMDIconButton:
                    icon: "magnify-plus-outline"
                    tooltip_text: "Zoom In"
                    on_release: root.zoom_in()

                TooltipMDIconButton:
                    icon: "magnify-minus-outline"
                    tooltip_text: "Zoom Out"
                    on_release: root.zoom_out()

                TooltipMDIconButton:
                    icon: "magnify-close"
                    tooltip_text: "Reset Zoom"
                    on_release: root.zoom_reset()

            MDDivider:
                orientation: "vertical"

            TooltipMDIconButton:
                id: rewind_button
                icon: 'skip-backward'
                tooltip_text: "Rewind to Start"
                on_press: root.rewind_pressed()
            TooltipMDIconButton:
                id: play_button
                icon: 'play'
                tooltip_text: "Play / Pause"
                on_press: root.play_pressed()
            TooltipMDIconButton:
                id: stop_button
                icon: 'stop'
                tooltip_text: "Stop"
                on_press: root.stop_pressed()

            Widget:
                size_hint_x: 0.5

            Label:
                id: pos_label
                text: "Pos: 1:1"
                size_hint_x: None
                width: self.texture_size[0]


        # Ruler
        Ruler:
            id: ruler
            size_hint_y: None
            height: dp(30)
            sequencer_layout: root.sequencer_layout
            pixels_per_beat: root.pixels_per_beat
            total_beats: root.total_beats
            info_width: dp(60) # Match axis width
            controls_width: 0


        # Main Content Area (Grid + Value Axis)
        BoxLayout:
            id: main_content
            orientation: 'horizontal'

            AutomationValueAxis:
                id: value_axis
                size_hint_x: None
                width: dp(60)
                min_val: root.min_val
                max_val: root.max_val

            ScrollView:
                id: timeline_scroll
                do_scroll_y: False
                bar_width: dp(20)
                scroll_type: ['bars']

                EditableAutomationGrid:
                    id: grid
                    editor: root
                    size_hint: None, 1
                    width: root.total_beats * root.pixels_per_beat
                    points: root.visible_points
                    total_beats: root.total_beats
                    pixels_per_beat: root.pixels_per_beat
                    min_val: root.min_val
                    max_val: root.max_val
                    beats_per_measure: root.sequencer_layout.sequencer.song.time_signature_numerator


        # Bottom Toolbar
        MDBoxLayout:
            size_hint_y: None
            height: dp(48)
            padding: dp(8)
            spacing: dp(8)
            md_bg_color: 0.2, 0.2, 0.2, 1

            Label:
                id: status_label
                text: "Point: Time=1:1, Value=0.0"

            Widget:
                size_hint_x: 1

            HoverableButton:
                text: 'Save & Close'
                size_hint_x: None
                width: dp(120)
                on_press: root.dismiss('save_and_close')
            HoverableButton:
                text: 'Discard & Close'
                size_hint_x: None
                width: dp(140)
                on_press: root.dismiss('discard_and_close')
            HoverableButton:
                text: 'Cancel'
                size_hint_x: None
                width: dp(100)
                on_press: root.dismiss()
""")

class AutomationEditor(ModalView):
    sequencer_layout = ObjectProperty()
    track = ObjectProperty() # This will be the AutomationTrack
    original_track_index = NumericProperty(None)
    track_copy = ObjectProperty()

    pixels_per_beat = NumericProperty(100)
    total_beats = NumericProperty(128)

    edit_mode = StringProperty('insert')
    is_dirty = BooleanProperty(False)
    _is_scrolling = False

    # Properties for managing visible data
    visible_points = ListProperty([])
    selected_parameter = StringProperty('vol')
    selected_point = ObjectProperty(None, allownone=True)
    min_val = NumericProperty(0.0)
    max_val = NumericProperty(1.0)
    history = ObjectProperty(None)


    def __init__(self, **kwargs):
        self.history = EditHistoryManager()
        super(AutomationEditor, self).__init__(**kwargs)

        self.track_copy = AutomationTrack(
            name=self.track.name,
            target_track_index=self.track.target_track_index,
            points=copy.deepcopy(self.track.points)
        )
        self.total_beats = self.sequencer_layout.sequencer.get_song_length_in_beats()

        Clock.schedule_once(self._post_kv_init)
        Window.bind(on_key_down=self._on_key_down)

    def _post_kv_init(self, dt):
        """Final UI setup after the kv string is loaded."""
        target_track = self.sequencer_layout.sequencer.song.tracks[self.track.target_track_index]
        track_type = 'midi' if isinstance(target_track, MidiTrack) else 'audio'

        automation_controls = self.ids.automation_controls
        automation_controls.track_type = track_type
        automation_controls.bind(on_selection_change=self.on_automation_selection_change)

        # Manually trigger the first selection to initialize the view
        automation_controls.select_param('vol')

        self.mode_buttons = {
            'insert': self.ids.insert_button, 'move': self.ids.move_button, 'delete': self.ids.delete_button
        }
        self.set_edit_mode(self.edit_mode, self.mode_buttons[self.edit_mode])

        # Record initial state for undo
        self._record_state()
        self._update_undo_redo_buttons_state()

        # Scroll synchronization
        ruler_scroll = self.ids.ruler.scroll_view
        timeline_scroll = self.ids.timeline_scroll
        ruler_scroll.bind(scroll_x=self.sync_horizontal_scroll)
        timeline_scroll.bind(scroll_x=self.sync_horizontal_scroll)


    def on_automation_selection_change(self, instance, param):
        self.selected_parameter = param

        if param in ["prog", "vel"]:
            self.min_val, self.max_val = 0.0, 127.0
        elif param == "pan":
            self.min_val, self.max_val = -1.0, 1.0
        else: # vol, etc.
            self.min_val, self.max_val = 0.0, 1.0

        self.visible_points = [p for p in self.track_copy.points if p.parameter == param]
        self.ids.grid.points = self.visible_points


    def on_dismiss(self):
        """Clean up bindings when the editor is closed."""
        Window.unbind(on_key_down=self._on_key_down)
        pass

    def _on_key_down(self, instance, keyboard, keycode, text, modifiers):
        """Handle keyboard shortcuts for the editor."""
        if 'ctrl' in modifiers:
            if text == 'z':
                self.undo()
                return True
            elif text == 'y':
                self.redo()
                return True
            elif text == 'i':
                self.set_edit_mode('insert', self.mode_buttons['insert'])
                return True
            elif text == 'd':
                self.set_edit_mode('delete', self.mode_buttons['delete'])
                return True
            elif text == 'm':
                self.set_edit_mode('move', self.mode_buttons['move'])
                return True

        if text in ('+', '-'):
            if self.selected_point:
                if self.selected_parameter in ('prog', 'vel'):
                    delta = 10 if text == '+' else -10
                else:
                    delta = 1.0 if text == '+' else -1.0

                new_val = self.selected_point.value + delta
                self.selected_point.value = max(self.min_val, min(self.max_val, new_val))
                self.is_dirty = True
                self.ids.grid.draw_curve_and_points()
                self._record_state()

        return False

    # Placeholder methods for actions
    def set_edit_mode(self, mode, btn):
        self.edit_mode = mode
        self._update_button_states(self.mode_buttons, btn)
        print(f"Edit mode set to: {mode}")

    def _update_button_states(self, buttons, active_button):
        """Mise à jour visuelle des boutons de mode d'édition."""
        from kivy.app import App
        theme = App.get_running_app().theme_cls

        for button in buttons.values():
            if button == active_button:
                button.md_bg_color = theme.primary_color
                button.icon_color = [1, 1, 1, 1]
            else:
                button.md_bg_color = [0.2, 0.2, 0.2, 1]
                button.icon_color = [1, 1, 1, 0.8]

    def undo(self):
        previous_state = self.history.undo()
        if previous_state:
            self._apply_state(previous_state)

    def redo(self):
        next_state = self.history.redo()
        if next_state:
            self._apply_state(next_state)

    def _record_state(self):
        state = [{
            'start_time': p.start_time,
            'value': p.value,
            'curve': p.curve,
            'parameter': p.parameter
        } for p in self.track_copy.points]
        self.history.record_state(state)
        self._update_undo_redo_buttons_state()

    def _apply_state(self, state):
        from sequencer.models import AutomationPoint
        self.track_copy.points = [AutomationPoint(**data) for data in state]
        # Re-filter visible points based on the current parameter
        self.on_automation_selection_change(None, self.selected_parameter)
        self.is_dirty = True
        self._update_undo_redo_buttons_state()

    def _update_undo_redo_buttons_state(self):
        self.ids.undo_button.disabled = not self.history.can_undo()
        self.ids.redo_button.disabled = not self.history.can_redo()

    def add_point(self, beat, value):
        from sequencer.models import AutomationPoint
        new_point = AutomationPoint(
            start_time=beat,
            value=value,
            parameter=self.selected_parameter,
            curve='linear'
        )
        self.track_copy.points.append(new_point)
        self.on_automation_selection_change(None, self.selected_parameter) # Refresh view
        self._record_state()
        self.is_dirty = True

    def delete_point(self, point):
        if point in self.track_copy.points:
            self.track_copy.points.remove(point)
            self.on_automation_selection_change(None, self.selected_parameter)
            self._record_state()
            self.is_dirty = True

    def show_curve_type_popup(self, point, touch):
        if self.selected_parameter == 'prog': # Program change has no curve
            return

        dropdown = DropDown()
        curve_types = ['none', 'linear', 'ease-in', 'ease-out', 'ease-in-out', 'sine']
        dropdown.width = dp(150)

        for curve_type in curve_types:
            btn = Button(text=curve_type, size_hint_y=None, height=dp(44))
            btn.bind(on_release=lambda btn, t=curve_type: self.set_curve_type(point, t, dropdown))
            dropdown.add_widget(btn)

        proxy_widget = Widget(size_hint=(None, None), size=(1, 1), pos=touch.pos)
        self.add_widget(proxy_widget)
        dropdown.open(proxy_widget)
        dropdown.bind(on_dismiss=lambda instance: self.remove_widget(proxy_widget))

    def set_curve_type(self, point, curve_type, dropdown):
        point.curve = curve_type
        dropdown.dismiss()
        self.ids.grid.draw_curve_and_points()
        self._record_state()
        self.is_dirty = True

    def zoom_in(self):
        self.pixels_per_beat *= 1.25

    def zoom_out(self):
        self.pixels_per_beat /= 1.25

    def zoom_reset(self):
        self.pixels_per_beat = 100

    def sync_horizontal_scroll(self, instance, value):
        if self._is_scrolling: return
        self._is_scrolling = True
        ruler_scroll = self.ids.ruler.scroll_view
        timeline_scroll = self.ids.timeline_scroll
        if instance == ruler_scroll:
            timeline_scroll.scroll_x = value
        else:
            ruler_scroll.scroll_x = value
        self._is_scrolling = False

    def play_pressed(self, *args):
        self.sequencer_layout.sequencer.process_transport_command("play_pause")

    def stop_pressed(self, *args):
        self.sequencer_layout.sequencer.process_transport_command("stop")

    def rewind_pressed(self, *args):
        self.sequencer_layout.sequencer._resync_all_at_beat(0)

    def dismiss(self, action=None, *args):
        if action == 'save_and_close':
            self._save_changes()
            super().dismiss(*args)
            return
        if action == 'discard_and_close':
            super().dismiss(*args)
            return
        if self.is_dirty:
            SaveDiscardCancelPopup(prompt_text="You have unsaved changes.", callback=self._handle_save_dialog).open()
        else:
            super().dismiss(*args)

    def _handle_save_dialog(self, answer):
        if answer == 's':
            self._save_changes()
            super().dismiss()
        elif answer == 'd':
            super().dismiss()

    def _save_changes(self):
        self.track.points = copy.deepcopy(self.track_copy.points)
        self.is_dirty = False
        # Find the corresponding track widget and tell it to refresh
        for tw in self.sequencer_layout.track_widgets:
            if tw.track == self.track:
                # Pass both the instance and the param name to the event handler
                tw.update_automation_visibility(tw.automation_controls, tw.automation_controls.selected_param)
                break