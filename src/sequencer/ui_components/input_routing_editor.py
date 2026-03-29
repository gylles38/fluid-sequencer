from .floating_window import FloatingWindow
from kivy.lang import Builder
from kivy.uix.relativelayout import RelativeLayout
from kivy.properties import ObjectProperty, NumericProperty, StringProperty, BooleanProperty, ListProperty
from . import TooltipMDIconButton, Ruler
from .ui_utils import is_any_text_input_focused
from sequencer.models import AutomationTrack, MidiTrack, AutomationPoint
import copy
from kivy.core.window import Window
from kivy.clock import Clock
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.divider import MDDivider
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.boxlayout import BoxLayout
from .HoverBehavior import HoverableButton
from kivy.graphics import Color, Line, Rectangle, Mesh
from kivy.metrics import dp
import math
from collections import deque
from .SaveDiscardCancelPopup import SaveDiscardCancelPopup

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

class RoutingValueAxis(Widget):
    midi_tracks = ListProperty([]) # List of (absolute_index, track_name)
    active_index = NumericProperty(-1)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.redraw, size=self.redraw, midi_tracks=self.redraw, active_index=self.redraw)
        self.labels = []

    def redraw(self, *args):
        """Debounced redraw of the routing axis."""
        if not hasattr(self, '_redraw_pending'): self._redraw_pending = False
        if self._redraw_pending: return
        self._redraw_pending = True
        Clock.schedule_once(self._do_redraw, 0)

    def _do_redraw(self, dt):
        self._redraw_pending = False
        self.draw()

    def draw(self, *args):
        if not self.canvas: return
        self.canvas.clear()

        if not hasattr(self, '_label_widgets'): self._label_widgets = []

        with self.canvas:
            Color(0.2, 0.2, 0.2, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0.4, 0.4, 0.4, 1)
            Line(points=[self.right, self.y, self.right, self.top], width=1)

        if not self.midi_tracks:
            label = Label(text="No MIDI tracks", pos=self.pos, size=self.size, color=(1, 0, 0, 1))
            self.add_widget(label)
            return

        num_tracks = len(self.midi_tracks)

        # Synchronize label widget count
        while len(self._label_widgets) < num_tracks:
            lbl = Label(font_size='10sp', halign='right', valign='middle')
            self.add_widget(lbl)
            self._label_widgets.append(lbl)
        while len(self._label_widgets) > num_tracks:
            lbl = self._label_widgets.pop()
            self.remove_widget(lbl)

        for i, (abs_idx, name) in enumerate(self.midi_tracks):
            y_pos = self.y + (i / max(1, num_tracks - 1)) * (self.height - dp(20)) + dp(10)
            if num_tracks == 1:
                y_pos = self.y + self.height / 2

            is_active = (abs_idx == self.active_index)
            if is_active:
                with self.canvas:
                    Color(0.2, 0.3, 0.4, 1)
                    Rectangle(pos=(self.x, y_pos - dp(10)), size=(self.width, dp(20)))

            label = self._label_widgets[i]
            label.text = f"{abs_idx}: {name}"
            label.pos = (self.x, y_pos - dp(8))
            label.size = (self.width - dp(4), dp(16))
            label.color = (1, 1, 1, 1) if is_active else (0.8, 0.8, 0.8, 1)
            label.bold = is_active

class EditableRoutingGrid(Widget):
    editor = ObjectProperty()
    points = ListProperty([])
    active_index = NumericProperty(-1)
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128.0)
    midi_tracks = ListProperty([]) # List of (absolute_index, track_name)
    beats_per_measure = NumericProperty(4)
    _dragged_point = ObjectProperty(None, allownone=True)
    _drag_offset = (0, 0)
    selected_point = ObjectProperty(None, allownone=True)

    # Virtual Dragging offsets
    drag_delta_beat = NumericProperty(0)
    drag_delta_value = NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.grid_widget = Widget(size_hint=(None, None))
        self.curve_widget = Widget(size_hint=(None, None))
        self.add_widget(self.grid_widget)
        self.add_widget(self.curve_widget)

        self.bind(pos=self._update_layout, size=self._update_layout, points=self.draw,
                  pixels_per_beat=self.draw, total_beats=self.draw,
                  midi_tracks=self.draw, active_index=self.draw,
                  drag_delta_beat=self.draw, drag_delta_value=self.draw)

    def _update_layout(self, *args):
        self.grid_widget.size = self.size
        self.grid_widget.pos = self.pos
        self.curve_widget.size = self.size
        self.curve_widget.pos = self.pos
        self.redraw()

    def redraw(self, *args):
        """Debounced redraw of the grid and curve."""
        if not hasattr(self, '_redraw_pending'): self._redraw_pending = False
        if self._redraw_pending: return
        self._redraw_pending = True
        Clock.schedule_once(self._do_redraw, 0)

    def _do_redraw(self, dt):
        self._redraw_pending = False
        self.draw()

    def _get_y_from_abs_idx(self, abs_idx):
        if not self.midi_tracks: return 0
        num_tracks = len(self.midi_tracks)
        # Find index in sorted midi_tracks
        try:
            i = [t[0] for t in self.midi_tracks].index(abs_idx)
        except ValueError:
            return 0

        if num_tracks == 1:
            return self.height / 2
        return (i / (num_tracks - 1)) * (self.height - dp(20)) + dp(10)

    def _get_abs_idx_from_y(self, y):
        if not self.midi_tracks: return 0
        num_tracks = len(self.midi_tracks)
        if num_tracks == 1:
            return self.midi_tracks[0][0]

        # Reverse the calculation
        normalized_y = (y - dp(10)) / (self.height - dp(20))
        i = round(normalized_y * (num_tracks - 1))
        i = max(0, min(num_tracks - 1, i))
        return self.midi_tracks[i][0]

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)

        # Subtract widget position from relative parent coordinates
        lx, ly = touch.x - self.x, touch.y - self.y
        local_pos = (lx, ly)

        clicked_beat = lx / self.pixels_per_beat
        clicked_abs_idx = self._get_abs_idx_from_y(ly)

        edit_mode = self.editor.edit_mode

        clicked_point = None
        click_threshold = dp(12)

        for point in self.points:
            point_x = point.start_time * self.pixels_per_beat
            point_y = self._get_y_from_abs_idx(point.value)
            if abs(local_pos[0] - point_x) < click_threshold and abs(local_pos[1] - point_y) < click_threshold:
                clicked_point = point
                break

        self.selected_point = clicked_point
        self.redraw()
        self.editor.update_status_bar(clicked_point)

        if edit_mode == 'insert':
            quantized_beat = round(clicked_beat * 4) / 4
            self.editor.add_point(quantized_beat, clicked_abs_idx)
            return True

        elif edit_mode == 'delete':
            if clicked_point:
                self.editor.delete_point(clicked_point)
            return True

        elif edit_mode == 'move':
            if clicked_point:
                self._dragged_point = clicked_point
                self._drag_offset = (local_pos[0] - (clicked_point.start_time * self.pixels_per_beat)), 0
                touch.grab(self)
                return True

        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)

        if self._dragged_point:
            lx, ly = touch.x - self.x, touch.y - self.y

            new_x = lx - self._drag_offset[0]
            new_beat = new_x / self.pixels_per_beat
            quantized_beat = round(new_beat * 4) / 4
            target_beat = max(0, quantized_beat)
            self.drag_delta_beat = target_beat - self._dragged_point.start_time

            new_abs_idx = self._get_abs_idx_from_y(ly)
            self.drag_delta_value = new_abs_idx - self._dragged_point.value

            # Update status bar live with virtual values.
            self.editor.update_status_bar(self._dragged_point)

            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_up(touch)
        if self._dragged_point:
            # Apply offsets to model
            self._dragged_point.start_time += self.drag_delta_beat
            self._dragged_point.value += self.drag_delta_value

            # Reset offsets
            self.drag_delta_beat = 0
            self.drag_delta_value = 0

            self._dragged_point = None
            touch.ungrab(self)
            self.editor.is_dirty = True
            self.editor._record_state()
            self.redraw()
            return True
        return super().on_touch_up(touch)

    def draw(self, *args):
        self.grid_widget.canvas.clear()
        with self.grid_widget.canvas:
            Color(0.1, 0.1, 0.1, 1)
            Rectangle(pos=self.pos, size=self.size)

            Color(0.2, 0.2, 0.2, 1)
            for i in range(int(self.total_beats) + 1):
                x = i * self.pixels_per_beat
                if x > self.width: break
                is_measure = i % self.beats_per_measure == 0
                Line(points=[self.x + x, self.y, self.x + x, self.y + self.height], width=1.5 if is_measure else 0.5)

            # Horizontal lines for each MIDI track
            for abs_idx, name in self.midi_tracks:
                y = self._get_y_from_abs_idx(abs_idx)
                if abs_idx == self.active_index:
                    Color(0.2, 0.3, 0.4, 0.5)
                    Rectangle(pos=(self.x, self.y + y - dp(10)), size=(self.width, dp(20)))
                    Color(0.2, 0.2, 0.2, 1)
                Line(points=[self.x, self.y + y, self.x + self.width, self.y + y], width=0.5)

        self.draw_curve_and_points()

    def draw_curve_and_points(self, *args):
        self.curve_widget.canvas.clear()
        if not self.points: return

        sorted_points = sorted(self.points, key=lambda p: p.start_time)

        with self.curve_widget.canvas:
            # Draw step-like routing line
            Color(0.8, 0.8, 1, 0.9)
            points_to_draw = []

            # Start from 0
            first_p = sorted_points[0]
            is_dragged = (first_p is self._dragged_point)
            v_off_start_y = self.drag_delta_value if is_dragged else 0
            points_to_draw.extend([self.x, self.y + self._get_y_from_abs_idx(first_p.value + v_off_start_y)])

            for i in range(len(sorted_points)):
                p = sorted_points[i]
                is_dragged = (p is self._dragged_point)
                v_off_x = self.drag_delta_beat if is_dragged else 0
                v_off_y = self.drag_delta_value if is_dragged else 0

                x = (p.start_time + v_off_x) * self.pixels_per_beat
                y = self._get_y_from_abs_idx(p.value + v_off_y)

                if i > 0:
                    # Vertical step from previous value
                    prev_p = sorted_points[i-1]
                    is_dragged_prev = (prev_p is self._dragged_point)
                    v_off_prev_y = self.drag_delta_value if is_dragged_prev else 0

                    prev_y = self._get_y_from_abs_idx(prev_p.value + v_off_prev_y)
                    points_to_draw.extend([self.x + x, self.y + prev_y])

                points_to_draw.extend([self.x + x, self.y + y])

            # End line
            final_x = self.total_beats * self.pixels_per_beat
            last_p = sorted_points[-1]
            is_dragged_last = (last_p is self._dragged_point)
            v_off_last_y = self.drag_delta_value if is_dragged_last else 0
            points_to_draw.extend([self.x + final_x, self.y + self._get_y_from_abs_idx(last_p.value + v_off_last_y)])

            if len(points_to_draw) >= 4:
                Line(points=points_to_draw, width=1.5)

            # Draw points
            point_radius = dp(4)
            selected_radius = dp(7)
            for p in sorted_points:
                is_dragged = (p is self._dragged_point)
                v_off_x = self.drag_delta_beat if is_dragged else 0
                v_off_y = self.drag_delta_value if is_dragged else 0

                x = (p.start_time + v_off_x) * self.pixels_per_beat
                y = self._get_y_from_abs_idx(p.value + v_off_y)

                if p == self.selected_point:
                    Color(1, 0.6, 0, 1)
                    Rectangle(pos=(self.x + x - selected_radius, self.y + y - selected_radius), size=(selected_radius * 2, selected_radius * 2))
                else:
                    Color(0.8, 0.8, 1, 0.9)
                    Rectangle(pos=(self.x + x - point_radius, self.y + y - point_radius), size=(point_radius * 2, point_radius * 2))

Builder.load_string("""
<InputRoutingEditor>:
    size_hint: 0.9, 0.9

    MDBoxLayout:
        orientation: 'vertical'

        MDBoxLayout:
            id: toolbar
            size_hint_y: None
            height: dp(56)
            padding: dp(8)
            spacing: dp(8)
            md_bg_color: 0.2, 0.2, 0.2, 1

            Label:
                text: "MIDI Input Routing"
                bold: True
                size_hint_x: None
                width: self.texture_size[0]

            MDDivider:
                orientation: 'vertical'

            Label:
                text: "Modes:"
                size_hint_x: None
                width: self.texture_size[0]

            TooltipMDIconButton:
                id: insert_button
                icon: 'pencil'
                tooltip_text: "Insert Mode"
                theme_icon_color: "Custom"
                on_press: root.set_edit_mode('insert', self)
            TooltipMDIconButton:
                id: move_button
                icon: 'cursor-move'
                tooltip_text: "Move Mode"
                theme_icon_color: "Custom"
                on_press: root.set_edit_mode('move', self)
            TooltipMDIconButton:
                id: delete_button
                icon: 'eraser'
                tooltip_text: "Delete Mode"
                theme_icon_color: "Custom"
                on_press: root.set_edit_mode('delete', self)

            MDDivider:
                orientation: 'vertical'

            TooltipMDIconButton:
                id: undo_button
                icon: 'undo'
                tooltip_text: "Undo"
                on_press: root.undo()
                disabled: True
            TooltipMDIconButton:
                id: redo_button
                icon: 'redo'
                tooltip_text: "Redo"
                on_press: root.redo()
                disabled: True

            Widget:
                size_hint_x: 1

            MDBoxLayout:
                adaptive_width: True
                spacing: dp(4)
                TooltipMDIconButton:
                    icon: "magnify-plus-outline"
                    on_release: root.zoom_in()
                TooltipMDIconButton:
                    icon: "magnify-minus-outline"
                    on_release: root.zoom_out()
                TooltipMDIconButton:
                    icon: "magnify-close"
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
                tooltip_text: "Play"
                on_press: root.play_pressed()
            TooltipMDIconButton:
                id: pause_button
                icon: 'pause'
                tooltip_text: "Pause / Resume"
                on_press: root.pause_pressed()
            TooltipMDIconButton:
                id: stop_button
                icon: 'stop'
                tooltip_text: "Stop"
                on_press: root.stop_pressed()

            Widget:
                size_hint_x: 0.5

            Label:
                text: "Pos:"
                size_hint_x: None
                width: self.texture_size[0]

            TextInput:
                id: pos_label
                text: "1:1"
                size_hint_x: None
                size_hint_y: None
                height: dp(30)
                pos_hint: {"center_y": .5}
                width: dp(70)
                multiline: False
                on_text_validate: root.seek_from_input(self.text)

        Ruler:
            id: ruler
            size_hint: 1, None
            height: dp(30)
            sequencer_layout: root.sequencer_layout
            pixels_per_beat: root.pixels_per_beat
            total_beats: root.total_beats
            end_pos_str: root.end_pos_str
            beats_per_measure: root.sequencer_layout.sequencer.song.time_signature_numerator
            info_width: dp(120)
            controls_width: 0
            keyboard_width: 0
            spacing: 0
            padding: [0, 0, 0, 0]

        BoxLayout:
            id: main_content
            orientation: 'horizontal'

            RoutingValueAxis:
                id: value_axis
                size_hint_x: None
                width: dp(120)
                midi_tracks: root.midi_tracks
                active_index: root.current_routing_index

            BoundedScrollView:
                id: timeline_scroll
                do_scroll_x: True
                do_scroll_y: False
                bar_width: dp(15)
                scroll_type: ['bars']
                bar_pos_x: 'bottom'
                bar_margin: dp(2)

                # Utiliser un RelativeLayout pour superposer le contenu et la playhead
                RelativeLayout:
                    id: scroll_content
                    size_hint: None, 1
                    width: grid.width + dp(15)

                    BoxLayout:
                        orientation: 'vertical'
                        size_hint: (1, 1)
                        padding: [0, 0, 0, dp(15)]

                        # Grille d'automation (Routing)
                        EditableRoutingGrid:
                            id: grid
                            editor: root
                            size_hint: None, 1
                            width: root.total_beats * root.pixels_per_beat
                            points: root.track_copy.points
                            total_beats: root.total_beats
                            pixels_per_beat: root.pixels_per_beat
                            midi_tracks: root.midi_tracks
                            beats_per_measure: root.sequencer_layout.sequencer.song.time_signature_numerator
                            active_index: root.current_routing_index

                        Widget:
                            size_hint_y: None
                            height: dp(18)

                    # Playhead
                    Widget:
                        id: playhead
                        size_hint: None, 1
                        width: dp(2)
                        x: 0
                        canvas:
                            Color:
                                rgba: 1, 0, 0, 0.8
                            Rectangle:
                                pos: self.pos
                                size: self.size

        MDBoxLayout:
            size_hint_y: None
            height: dp(48)
            padding: dp(8)
            spacing: dp(8)
            md_bg_color: 0.2, 0.2, 0.2, 1

            MDBoxLayout:
                id: edit_zone
                adaptive_width: True
                spacing: dp(10)
                opacity: 0

                MDLabel:
                    text: "Beat:"
                    adaptive_width: True
                TextInput:
                    id: input_beat
                    size_hint: None, None
                    size: dp(60), dp(30)
                    multiline: False
                    on_text_validate: root.apply_manual_edit()

                MDLabel:
                    text: "Track Idx:"
                    adaptive_width: True
                TextInput:
                    id: input_value
                    size_hint: None, None
                    size: dp(60), dp(30)
                    multiline: False
                    on_text_validate: root.apply_manual_edit()

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

class InputRoutingEditor(FloatingWindow):
    min_width = NumericProperty(dp(750))
    sequencer_layout = ObjectProperty()
    track = ObjectProperty() # The 'input_routing' AutomationTrack
    track_copy = ObjectProperty()
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128)
    current_routing_index = NumericProperty(-1)
    end_pos_str = StringProperty('')
    midi_tracks = ListProperty([])
    edit_mode = StringProperty('insert')
    is_dirty = BooleanProperty(False)
    _is_scrolling = False
    history = ObjectProperty(None)
    selected_point = ObjectProperty(None, allownone=True)

    def __init__(self, **kwargs):
        self.history = EditHistoryManager()
        self.track = kwargs.get('track')
        if self.track:
            self.track_copy = AutomationTrack(
                name=self.track.name,
                target_track_index=-1, # Global
                points=copy.deepcopy(self.track.points)
            )
        super(InputRoutingEditor, self).__init__(**kwargs)
        self.title = "MIDI Input Routing Editor"
        self.total_beats = self.sequencer_layout.sequencer.get_song_length_in_beats()
        self.end_pos_str = self.sequencer_layout.sequencer.ui_end_pos_str
        self._seq_binding_end_pos = lambda inst, val: setattr(self, 'end_pos_str', val)
        self.sequencer_layout.sequencer.bind(ui_end_pos_str=self._seq_binding_end_pos)

        self.update_midi_tracks()

        self._seq_binding_routing = lambda inst, val: setattr(self, 'current_routing_index', val)
        self.sequencer_layout.sequencer.bind(current_routing_index=self._seq_binding_routing)
        self.current_routing_index = self.sequencer_layout.sequencer.current_routing_index
        self.sequencer_layout.sequencer.bind(playback_state=self.on_playback_state_change)

        Clock.schedule_once(self._post_kv_init)
        Clock.schedule_interval(self.update_playhead, 1/60)

    def _post_kv_init(self, dt):
        self.mode_buttons = {
            'insert': self.ids.insert_button, 'move': self.ids.move_button, 'delete': self.ids.delete_button
        }
        self.set_edit_mode(self.edit_mode, self.mode_buttons[self.edit_mode])
        self.ids.ruler.scroll_view.bind(scroll_x=self.sync_horizontal_scroll)
        self.ids.timeline_scroll.bind(scroll_x=self.sync_horizontal_scroll)
        self._record_state()
        Window.bind(on_key_down=self._on_key_down)

    def update_midi_tracks(self):
        self.midi_tracks = [
            (i, t.name) for i, t in enumerate(self.sequencer_layout.sequencer.song.tracks)
            if isinstance(t, MidiTrack)
        ]

    def scroll_to_beat(self, beat):
        """Défile la timeline pour afficher le beat spécifié."""
        scroll_view = self.ids.timeline_scroll
        grid_width = self.total_beats * self.pixels_per_beat
        viewport_width = scroll_view.width

        if grid_width <= viewport_width:
            scroll_view.scroll_x = 0
            return

        target_pixel = beat * self.pixels_per_beat
        max_scroll = grid_width - viewport_width
        new_scroll_x = target_pixel / max_scroll

        scroll_view.scroll_x = max(0, min(1, new_scroll_x))

    def update_playhead(self, dt):
        if 'playhead' not in self.ids: return
        sequencer = self.sequencer_layout.sequencer
        current_beat = sequencer.current_beat

        # Déplacement de la barre rouge
        self.ids.playhead.x = current_beat * self.pixels_per_beat

        # Mise à jour du texte M:B
        if not self.ids.pos_label.focus:
            self.ids.pos_label.text = sequencer._format_beats_to_position(current_beat)

        # Auto-scroll uniquement en lecture
        if sequencer.playback_state == 'playing':
            self._scroll_to_logic(current_beat)

    def _scroll_to_logic(self, current_beat):
        scroll_view = self.ids.timeline_scroll
        total_width = self.ids.grid.width
        viewport_width = scroll_view.width

        max_scroll_dist = total_width - viewport_width
        if max_scroll_dist <= 0:
            return

        playhead_pixel_x = current_beat * self.pixels_per_beat
        trigger_point = viewport_width * 0.5

        if playhead_pixel_x > trigger_point:
            target_view_start = playhead_pixel_x - trigger_point
            new_scroll_x = target_view_start / max_scroll_dist
            scroll_view.scroll_x = max(0, min(1, new_scroll_x))

    def set_edit_mode(self, mode, btn):
        self.edit_mode = mode
        for b in self.mode_buttons.values():
            b.icon_color = [1, 1, 1, 0.8]
        btn.icon_color = [1, 0.6, 0, 1]

    def add_point(self, beat, value):
        new_point = AutomationPoint(start_time=beat, value=value, parameter='input_routing', curve='none')
        self.track_copy.points.append(new_point)
        self.track_copy.points.sort(key=lambda p: p.start_time)
        self.ids.grid.points = list(self.track_copy.points)
        self.ids.grid.draw()
        self._record_state()
        self.is_dirty = True

    def delete_point(self, point):
        if point in self.track_copy.points:
            self.track_copy.points.remove(point)
            if self.selected_point == point:
                self.selected_point = None
            if self.ids.grid.selected_point == point:
                self.ids.grid.selected_point = None
            self.update_status_bar(None)
            self.ids.grid.points = list(self.track_copy.points)
            self.ids.grid.draw()
            self._record_state()
            self.is_dirty = True

    def undo(self):
        state = self.history.undo()
        if state: self._apply_state(state)

    def redo(self):
        state = self.history.redo()
        if state: self._apply_state(state)

    def _record_state(self):
        state = [{'start_time': p.start_time, 'value': p.value, 'curve': p.curve, 'parameter': p.parameter} for p in self.track_copy.points]
        self.history.record_state(state)
        self.ids.undo_button.disabled = not self.history.can_undo()
        self.ids.redo_button.disabled = not self.history.can_redo()

    def _apply_state(self, state):
        self.track_copy.points = [AutomationPoint(**d) for d in state]
        self.ids.grid.points = list(self.track_copy.points)
        self.ids.grid.draw()
        self.is_dirty = True

    def zoom_in(self): self._apply_zoom(self.pixels_per_beat * 1.25)
    def zoom_out(self): self._apply_zoom(max(dp(20), self.pixels_per_beat / 1.25))
    def zoom_reset(self): self._apply_zoom(dp(100))

    def _apply_zoom(self, new_pixels_per_beat):
        """Applique le zoom en tentant de conserver le centre de la vue."""
        scroll_view = self.ids.timeline_scroll

        # 1. Calculer le beat qui est actuellement au centre de l'écran
        old_total_width = self.total_beats * self.pixels_per_beat
        viewport_width = scroll_view.width

        if old_total_width > viewport_width:
            center_pixel = (scroll_view.scroll_x * (old_total_width - viewport_width)) + (viewport_width / 2)
        else:
            center_pixel = viewport_width / 2
        center_beat = center_pixel / self.pixels_per_beat

        # 2. Appliquer le nouveau zoom
        self.pixels_per_beat = new_pixels_per_beat

        # 3. Recalculer le scroll_x pour que le center_beat reste au centre
        Clock.schedule_once(lambda dt: self._update_scroll_after_zoom(center_beat), 0)

    def _update_scroll_after_zoom(self, target_beat):
        scroll_view = self.ids.timeline_scroll
        new_total_width = self.total_beats * self.pixels_per_beat
        viewport_width = scroll_view.width

        self.ids.grid.width = new_total_width

        if new_total_width <= viewport_width:
            scroll_view.scroll_x = 0
        else:
            new_center_pixel = target_beat * self.pixels_per_beat
            new_scroll_pixels = new_center_pixel - (viewport_width / 2)
            max_scroll = new_total_width - viewport_width
            scroll_view.scroll_x = max(0, min(1, new_scroll_pixels / max_scroll))

        self.ids.ruler.redraw()
        self.ids.grid.draw_curve_and_points()

    def sync_horizontal_scroll(self, instance, value):
        if self._is_scrolling: return
        self._is_scrolling = True

        # Simple and direct synchronization for identical widths
        if instance is self.ids.ruler.scroll_view:
            self.ids.timeline_scroll.scroll_x = value
        else:
            self.ids.ruler.scroll_view.scroll_x = value

        self._is_scrolling = False

    def play_pressed(self): self.sequencer_layout.sequencer.process_transport_command("play")
    def pause_pressed(self): self.sequencer_layout.sequencer.process_transport_command("pause")
    def stop_pressed(self): self.sequencer_layout.sequencer.process_transport_command("stop")
    def rewind_pressed(self): self.sequencer_layout.sequencer._resync_all_at_beat(0)

    def seek_from_input(self, text):
        try:
            seq = self.sequencer_layout.sequencer
            target_beat = seq.parse_position_to_beats(text)
            if target_beat is not None:
                target_beat = max(0, min(self.total_beats, target_beat))
                seq.current_beat = target_beat
                seq._resync_all_at_beat(target_beat)
                self.ids.playhead.x = target_beat * self.pixels_per_beat
                self._scroll_to_logic(target_beat)
            self.ids.pos_label.focus = False
        except:
            self.ids.pos_label.focus = False

    def update_status_bar(self, point):
        if point:
            self.ids.edit_zone.opacity = 1

            # Account for Virtual Dragging deltas
            v_beat_off = 0
            v_val_off = 0
            if hasattr(self.ids.grid, '_dragged_point') and self.ids.grid._dragged_point is point:
                v_beat_off = self.ids.grid.drag_delta_beat
                v_val_off = self.ids.grid.drag_delta_value

            display_beat = point.start_time + v_beat_off
            display_value = point.value + v_val_off

            self.ids.input_beat.text = f"{display_beat:.2f}"
            self.ids.input_value.text = f"{int(display_value)}"
        else:
            self.ids.edit_zone.opacity = 0

    def apply_manual_edit(self):
        if not self.ids.grid.selected_point: return
        point = self.ids.grid.selected_point
        try:
            new_beat = float(self.ids.input_beat.text)
            new_val = int(float(self.ids.input_value.text))

            # Validation: Check if the track index exists and is a MIDI track
            valid_indices = [t[0] for t in self.midi_tracks]
            if new_val not in valid_indices:
                # Reverting to the previous value if invalid
                self.update_status_bar(point)
                self.ids.input_beat.focus = False
                self.ids.input_value.focus = False
                return

            point.start_time = max(0, min(self.total_beats, new_beat))
            point.value = new_val
            self.ids.grid.draw()
            self.update_status_bar(point)
            self.is_dirty = True
            self._record_state()
            self.ids.input_beat.focus = False
            self.ids.input_value.focus = False
        except ValueError:
            self.update_status_bar(point)

    def _on_key_down(self, instance, keyboard, keycode, text, modifiers):
        if is_any_text_input_focused(): return False
        if keyboard == 32: # Space
            self.play_pressed()
            return True
        if keyboard == 278: # Home
            self.rewind_pressed()
            return True

        if keyboard == 279: # End
            ts_num = getattr(self.sequencer_layout.sequencer.song, 'time_signature_numerator', 4)
            target_beat = max(0, self.total_beats - ts_num)
            pos_str = self.sequencer_layout.sequencer._format_beats_to_position(target_beat)

            # Update End field
            self.sequencer_layout.sequencer.ui_end_pos_str = pos_str
            self.sequencer_layout.end_pos_input.text = pos_str
            self.sequencer_layout.end_pos_manual_override = True

            # Sync
            self.sequencer_layout.sequencer._resync_all_at_beat(target_beat)
            self.scroll_to_beat(target_beat)
            return True

        return False

    def on_playback_state_change(self, instance, state):
        play_btn = self.ids.play_button
        pause_btn = self.ids.pause_button

        if state in ('playing', 'recording'):
            play_btn.icon = 'play-circle-outline'
            play_btn.icon_color = [0, 0.7, 0.3, 1]
            pause_btn.icon = 'pause'
            pause_btn.md_bg_color = [0.1, 0.1, 0.1, 1]
        elif state == 'paused':
            play_btn.icon = 'play'
            play_btn.icon_color = [1, 1, 1, 0.8]
            pause_btn.icon = 'pause-circle-outline'
            pause_btn.md_bg_color = [0.9, 0.7, 0, 1]
        else: # stopped
            play_btn.icon = 'play'
            play_btn.icon_color = [1, 1, 1, 0.8]
            pause_btn.icon = 'pause'
            pause_btn.md_bg_color = [0.1, 0.1, 0.1, 1]

    def on_open(self):
        self.total_beats = self.sequencer_layout.sequencer.get_song_length_in_beats()
        self.ids.ruler.total_beats = self.total_beats
        self.ids.ruler.redraw()
        self.ids.grid.draw()

    def on_dismiss(self):
        from kivy.logger import Logger
        Logger.info(f"InputRoutingEditor: cleaning up {id(self)}")
        try:
            self.sequencer_layout.sequencer.unbind(playback_state=self.on_playback_state_change)
            if hasattr(self, '_seq_binding_routing'):
                self.sequencer_layout.sequencer.unbind(current_routing_index=self._seq_binding_routing)
            if hasattr(self, '_seq_binding_end_pos'):
                self.sequencer_layout.sequencer.unbind(ui_end_pos_str=self._seq_binding_end_pos)
        except Exception as e:
            Logger.error(f"InputRoutingEditor: Error unbinding sequencer: {e}")

        try:
            Window.unbind(on_key_down=self._on_key_down)
        except Exception as e:
            Logger.error(f"InputRoutingEditor: Error unbinding keyboard: {e}")

        if hasattr(self, '_playhead_event') and self._playhead_event:
            self._playhead_event.cancel()
            self._playhead_event = None

        super(InputRoutingEditor, self).on_dismiss()

    def dismiss(self, action=None, *args):
        if action == 'save_and_close':
            self.track.points = copy.deepcopy(self.track_copy.points)
            self.is_dirty = False
            #if self.sequencer_layout.sequencer.jack_manager.is_running:
            #    self.sequencer_layout.sequencer.jack_manager.refresh_automation()
            self.sequencer_layout.sequencer.song_structure_changed += 1
            super(InputRoutingEditor, self).dismiss(*args)
        elif action == 'discard_and_close':
            super(InputRoutingEditor, self).dismiss(*args)
        elif self.is_dirty:
            SaveDiscardCancelPopup(prompt_text="Unsaved changes.", callback=self._handle_save_dialog).open()
        else:
            super(InputRoutingEditor, self).dismiss(*args)

    def _handle_save_dialog(self, answer):
        if answer == 's':
            self.track.points = copy.deepcopy(self.track_copy.points)
            #if self.sequencer_layout.sequencer.jack_manager.is_running:
            #    self.sequencer_layout.sequencer.jack_manager.refresh_automation()
            self.sequencer_layout.sequencer.song_structure_changed += 1
            super(InputRoutingEditor, self).dismiss()
        elif answer == 'd':
            super(InputRoutingEditor, self).dismiss()
