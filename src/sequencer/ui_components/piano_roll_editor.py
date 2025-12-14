from kivy.uix.modalview import ModalView
from kivy.lang import Builder
from kivymd.uix.boxlayout import MDBoxLayout
from kivy.properties import ObjectProperty, NumericProperty, StringProperty, BooleanProperty
from . import TooltipMDIconButton, Ruler, PianoKeyboard, BoundedScrollView
from sequencer.ui_components.PianoRoll import PianoRoll
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.floatlayout import FloatLayout
from kivy.metrics import dp
from kivy.clock import Clock
import copy
from sequencer.models import Event, Note, MidiTrack
from .SaveDiscardCancelPopup import SaveDiscardCancelPopup
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle


# --- New Editable Grid Components (based on PianoRoll.py) ---

class EditableMidiGrid(PianoRoll):
    editor = ObjectProperty()
    _dragged_note = ObjectProperty(None, allownone=True)
    _drag_event = ObjectProperty(None, allownone=True)
    _drag_mode = StringProperty(None, allownone=True) # 'move' or 'resize'
    _drag_offset = (0, 0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.playback_line = None

    def add_playback_line(self):
        self.playback_line = Widget(size_hint_x=None, width=dp(2))
        with self.canvas.after:
            Color(1, 0, 0, 0.8)
            self.playback_rect = Rectangle(pos=self.playback_line.pos, size=self.playback_line.size)
        self.playback_line.bind(pos=self.update_playback_rect, size=self.update_playback_rect)
        self.add_widget(self.playback_line)
        self.playback_line.size_hint_y = None
        self.playback_line.height = self.height

    def update_playback_rect(self, *args):
        if hasattr(self, 'playback_rect'):
            self.playback_rect.pos = self.playback_line.pos
            self.playback_rect.size = self.playback_line.size

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super(EditableMidiGrid, self).on_touch_down(touch)

        local_pos = self.to_local(*touch.pos)
        clicked_beat = local_pos[0] / self.pixels_per_beat
        clicked_pitch = int(local_pos[1] / self.note_height)

        edit_mode = self.editor.edit_mode
        track = self.editor.track_copy

        if edit_mode == 'move':
            for event in reversed(track.events):
                for note in reversed(event.notes):
                    note_x = event.start_time * self.pixels_per_beat
                    note_y = note.pitch * self.note_height
                    note_width = note.duration * self.pixels_per_beat
                    resize_handle_width = min(dp(20), note_width / 2)

                    if note_x + note_width - resize_handle_width <= local_pos[0] <= note_x + note_width and \
                       note_y <= local_pos[1] <= note_y + self.note_height:
                        self._dragged_note = note
                        self._drag_event = event
                        self._drag_mode = 'resize'
                        self._drag_offset = (local_pos[0] - note_x, local_pos[1] - note_y)
                        touch.grab(self)
                        return True

                    elif note_x <= local_pos[0] <= note_x + note_width and \
                         note_y <= local_pos[1] <= note_y + self.note_height:
                        self._dragged_note = note
                        self._drag_event = event
                        self._drag_mode = 'move'
                        self._drag_offset = (local_pos[0] - note_x, local_pos[1] - note_y)
                        touch.grab(self)
                        return True

        quantized_beat = round(clicked_beat)

        if edit_mode == 'insert':
            new_note = Note(pitch=clicked_pitch, velocity=100, duration=self.editor.note_duration)
            target_event = next((e for e in track.events if abs(e.start_time - quantized_beat) < 0.001), None)

            if target_event:
                if not any(n.pitch == new_note.pitch for n in target_event.notes): target_event.notes.append(new_note)
            else:
                track.events.append(Event(start_time=quantized_beat, notes=[new_note]))
                track.events.sort(key=lambda e: e.start_time)

            self.editor.is_dirty = True
            self.draw()
            return True

        elif edit_mode == 'delete':
            for event in reversed(track.events):
                max_duration = max((n.duration for n in event.notes), default=0)
                if event.start_time <= clicked_beat < event.start_time + max_duration:
                    for note in reversed(event.notes):
                        if note.pitch == clicked_pitch:
                            event.notes.remove(note)
                            if not event.notes: track.events.remove(event)
                            self.editor.is_dirty = True
                            self.draw()
                            return True

        return super(EditableMidiGrid, self).on_touch_down(touch)

    def on_touch_move(self, touch):
        if self._dragged_note and touch.grab_current is self:
            local_pos = self.to_local(*touch.pos)

            if self._drag_mode == 'resize':
                note_start_x = self._drag_event.start_time * self.pixels_per_beat
                new_width = local_pos[0] - note_start_x
                new_duration = max(0.1, round((new_width / self.pixels_per_beat) * 4) / 4) # Quantize to 16th notes
                self._dragged_note.duration = new_duration

            elif self._drag_mode == 'move':
                new_x = local_pos[0] - self._drag_offset[0]
                new_y = local_pos[1] - self._drag_offset[1]

                new_beat = round(new_x / self.pixels_per_beat)
                new_pitch = max(0, min(127, int(new_y / self.note_height)))

                self._drag_event.start_time = new_beat
                self._dragged_note.pitch = new_pitch

            self.editor.is_dirty = True
            self.draw()
            return True
        return super(EditableMidiGrid, self).on_touch_move(touch)

    def on_touch_up(self, touch):
        if self._dragged_note and touch.grab_current is self:
            if self._drag_mode == 'move':
                self.editor.track_copy.events.sort(key=lambda e: e.start_time)

            self._dragged_note = None
            self._drag_event = None
            self._drag_mode = None
            touch.ungrab(self)
            return True
        return super(EditableMidiGrid, self).on_touch_up(touch)


class EditablePianoRollViewer(ScrollView):
    editor = ObjectProperty()
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    note_height = NumericProperty(dp(12))

    def __init__(self, **kwargs):
        super(EditablePianoRollViewer, self).__init__(**kwargs)
        self.size_hint_x = None
        self.do_scroll_x = False
        self.do_scroll_y = True
        self.grid = EditableMidiGrid(editor=self.editor, track=self.track, total_beats=self.total_beats, pixels_per_beat=self.pixels_per_beat, note_height=self.note_height)
        self.add_widget(self.grid)
        self.grid.bind(width=self.setter('width'))

    def on_touch_move(self, touch):
        # If the grid has grabbed the touch for a note drag/resize operation,
        # we must not process it for scrolling. We consume the event by returning True.
        if touch.grab_current is self.grid:
            return True
        return super(EditablePianoRollViewer, self).on_touch_move(touch)

    def on_editor(self, i, v): self.grid.editor = v
    def on_track(self, i, v): self.grid.track = v
    def on_total_beats(self, i, v): self.grid.total_beats = v
    def on_pixels_per_beat(self, i, v): self.grid.pixels_per_beat = v
    def on_note_height(self, i, v): self.grid.note_height = v


# --- Builder String ---
Builder.load_string("""
<PianoRollEditor>:
    size_hint: 0.9, 0.9
    auto_dismiss: False

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
                text: "Modes:"
                size_hint_x: None
                width: self.texture_size[0]

            TooltipMDIconButton:
                id: insert_button
                icon: 'plus-box'
                tooltip_text: "Insert Mode"
                on_press: root.set_edit_mode('insert', self)
            TooltipMDIconButton:
                id: move_button
                icon: 'drag-variant'
                tooltip_text: "Move Mode"
                on_press: root.set_edit_mode('move', self)
            TooltipMDIconButton:
                id: delete_button
                icon: 'minus-box'
                tooltip_text: "Delete Mode"
                on_press: root.set_edit_mode('delete', self)

            Widget:
                size_hint_x: 1

            Label:
                text: "Duration:"
                size_hint_x: None
                width: self.texture_size[0]

            TooltipMDIconButton:
                id: whole_note_button
                icon: 'music-note-whole'
                tooltip_text: "Whole Note (4 beats)"
                on_press: root.set_note_duration(4.0, self)
            TooltipMDIconButton:
                id: half_note_button
                icon: 'music-note-half'
                tooltip_text: "Half Note (2 beats)"
                on_press: root.set_note_duration(2.0, self)
            TooltipMDIconButton:
                id: quarter_note_button
                icon: 'music-note-quarter'
                tooltip_text: "Quarter Note (1 beat)"
                on_press: root.set_note_duration(1.0, self)
            TooltipMDIconButton:
                id: eighth_note_button
                icon: 'music-note-eighth'
                tooltip_text: "Eighth Note (0.5 beats)"
                on_press: root.set_note_duration(0.5, self)

            Widget:
                size_hint_x: 1

            TooltipMDIconButton:
                id: rewind_button
                icon: 'rewind'
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
            TooltipMDIconButton:
                id: record_button
                icon: 'record'
                tooltip_text: "Record"
                on_press: root.record_pressed()

            Widget:
                size_hint_x: 0.5

            Label:
                id: pos_label
                text: "Pos: 1:1"
                size_hint_x: None
                width: self.texture_size[0]

        Ruler:
            id: ruler
            sequencer_layout: root.sequencer_layout
            pixels_per_beat: root.pixels_per_beat
            total_beats: root.total_beats
            beats_per_measure: root.sequencer_layout.sequencer.song.time_signature_numerator
            size_hint_y: None
            height: dp(30)
            info_width: 0
            controls_width: 0
            keyboard_width: keyboard_sv.width
            spacing: 0
            padding: [0, dp(6), 0, dp(6)]

        BoxLayout:
            id: main_content
            orientation: 'horizontal'
            spacing: 0

            BoundedScrollView:
                id: keyboard_sv
                size_hint_x: None
                width: dp(60)
                do_scroll_x: False

                PianoKeyboard:
                    id: piano_keyboard
                    size_hint: (None, None)
                    width: self.parent.width
                    note_height: root.note_height

            BoundedScrollView:
                id: timeline_scroll
                do_scroll_y: False

                EditablePianoRollViewer:
                    id: grid_viewer
                    editor: root
                    track: root.track_copy
                    total_beats: root.total_beats
                    pixels_per_beat: root.pixels_per_beat
                    note_height: root.note_height

        MDBoxLayout:
            size_hint_y: None
            height: dp(48)
            padding: dp(8)
            spacing: dp(8)
            md_bg_color: 0.2, 0.2, 0.2, 1

            Widget:
                size_hint_x: 1
            Button:
                text: 'Save & Close'
                size_hint_x: None
                width: dp(120)
                on_press: root.dismiss('save_and_close')
            Button:
                text: 'Discard & Close'
                size_hint_x: None
                width: dp(140)
                on_press: root.dismiss('discard_and_close')
            Button:
                text: 'Cancel'
                size_hint_x: None
                width: dp(100)
                on_press: root.dismiss()
""")

class PianoRollEditor(ModalView):
    sequencer_layout = ObjectProperty()
    track = ObjectProperty()
    track_copy = ObjectProperty()
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128)
    note_height = NumericProperty(dp(14))
    edit_mode = StringProperty('insert')
    note_duration = NumericProperty(2.0)
    is_dirty = BooleanProperty(False)
    _is_scrolling = False
    _update_event = None

    def __init__(self, **kwargs):
        super(PianoRollEditor, self).__init__(**kwargs)
        self.track_copy = MidiTrack(
            name=self.track.name,
            channel=self.track.channel,
            instrument=self.track.instrument,
            is_muted=self.track.is_muted,
            is_solo=self.track.is_solo,
            volume=self.track.volume,
            pan=self.track.pan,
            events=copy.deepcopy(self.track.events)
        )
        self.total_beats = self.sequencer_layout.sequencer.get_song_length_in_beats()
        self.sequencer_layout.sequencer.bind(playback_state=self.on_playback_state_change)
        Clock.schedule_once(self._post_kv_init)
        self._update_event = Clock.schedule_interval(self.update_playhead, 1/30.0)

    def _post_kv_init(self, dt):
        keyboard_sv = self.ids.keyboard_sv
        grid_viewer = self.ids.grid_viewer
        ruler_scroll = self.ids.ruler.scroll_view
        timeline_scroll = self.ids.timeline_scroll

        keyboard_sv.bind(scroll_y=lambda i, v: setattr(grid_viewer, 'scroll_y', v))
        grid_viewer.bind(scroll_y=lambda i, v: setattr(keyboard_sv, 'scroll_y', v))

        self.ids.piano_keyboard.height = self.ids.grid_viewer.grid.height
        self.ids.grid_viewer.grid.bind(height=self.ids.piano_keyboard.setter('height'))

        # Add the playback line here to ensure it's drawn on top
        self.ids.grid_viewer.grid.add_playback_line()

        ruler_scroll.bind(scroll_x=self.sync_horizontal_scroll)
        timeline_scroll.bind(scroll_x=self.sync_horizontal_scroll)

        self._center_view_on_c4()
        current_beat = self.sequencer_layout.sequencer.current_beat
        if current_beat > 0 and self.total_beats > 0:
            scroll_pos = (current_beat * self.pixels_per_beat)
            max_scroll = self.ids.grid_viewer.grid.width - timeline_scroll.width
            if max_scroll > 0:
                timeline_scroll.scroll_x = min(1.0, scroll_pos / max_scroll)

        self.mode_buttons = {
            'insert': self.ids.insert_button, 'move': self.ids.move_button, 'delete': self.ids.delete_button
        }
        self.duration_buttons = {
            4.0: self.ids.whole_note_button, 2.0: self.ids.half_note_button,
            1.0: self.ids.quarter_note_button, 0.5: self.ids.eighth_note_button
        }
        self.set_edit_mode(self.edit_mode, self.mode_buttons[self.edit_mode])
        self.set_note_duration(self.note_duration, self.duration_buttons[self.note_duration])
        self.on_playback_state_change(None, self.sequencer_layout.sequencer.playback_state)
        self.ids.ruler.redraw()

    def on_dismiss(self):
        self.sequencer_layout.sequencer.unbind(playback_state=self.on_playback_state_change)
        if self._update_event:
            self._update_event.cancel()

    def dismiss(self, action=None, *args):
        if action == 'save_and_close':
            self._save_changes()
            super(PianoRollEditor, self).dismiss(*args)
            return
        if action == 'discard_and_close':
            super(PianoRollEditor, self).dismiss(*args)
            return
        if self.is_dirty:
            SaveDiscardCancelPopup(prompt_text="You have unsaved changes.", callback=self._handle_save_dialog).open()
        else:
            super(PianoRollEditor, self).dismiss(*args)

    def _handle_save_dialog(self, answer):
        if answer == 's':
            self._save_changes()
            super(PianoRollEditor, self).dismiss()
        elif answer == 'd':
            super(PianoRollEditor, self).dismiss()

    def _save_changes(self):
        self.track.events = self.track_copy.events
        self.is_dirty = False
        for tw in self.sequencer_layout.track_widgets:
            if tw.track == self.track and hasattr(tw, 'piano_roll_viewer'):
                tw.piano_roll_viewer.grid.draw()
                break

    def update_playhead(self, dt):
        current_beat = self.sequencer_layout.sequencer.current_beat
        self.set_playback_position(current_beat)

        # Update position label
        pos_str = self.sequencer_layout.sequencer._format_beats_to_position(current_beat)
        self.ids.pos_label.text = f"Pos: {pos_str}"

    def set_playback_position(self, current_beat: float):
        grid = self.ids.grid_viewer.grid
        pixels_per_beat = self.pixels_per_beat
        x_pos = current_beat * pixels_per_beat

        if grid.playback_line:
            grid.playback_line.x = x_pos

        if self.sequencer_layout.sequencer.playback_state in ['playing', 'recording']:
            scroll_view = self.ids.timeline_scroll
            timeline_width = grid.width
            viewport_width = scroll_view.width
            if timeline_width <= viewport_width: return

            margin_x = viewport_width * 0.3
            max_displacement = timeline_width - viewport_width
            current_scroll_x_pixels = scroll_view.scroll_x * max_displacement
            new_scroll_x_pixels = -1

            if x_pos > current_scroll_x_pixels + viewport_width - margin_x:
                new_scroll_x_pixels = x_pos - (viewport_width - margin_x)
            elif x_pos < current_scroll_x_pixels + margin_x and current_scroll_x_pixels > 1:
                new_scroll_x_pixels = x_pos - margin_x

            if new_scroll_x_pixels != -1:
                new_scroll_x_pixels = max(0, min(new_scroll_x_pixels, max_displacement))
                scroll_view.scroll_x = new_scroll_x_pixels / max_displacement

    def play_pressed(self, *args): self.sequencer_layout.sequencer.process_transport_command("play_pause")
    def stop_pressed(self, *args): self.sequencer_layout.sequencer.process_transport_command("stop")
    def record_pressed(self, *args): self.sequencer_layout.sequencer.process_transport_command("record")
    def rewind_pressed(self, *args): self.sequencer_layout.sequencer._resync_all_at_beat(0)

    def on_playback_state_change(self, instance, state):
        play_button, record_button = self.ids.play_button, self.ids.record_button
        play_button.icon = 'pause' if state in ('playing', 'recording') else 'play'
        play_button.tooltip_text = "Pause" if state in ('playing', 'recording') else "Play"
        record_button.icon_color = [1, 0.2, 0.2, 1] if state == 'recording' else [0.8, 0.8, 0.8, 1]
        record_button.md_bg_color = [0.5, 0.1, 0.1, 1] if state == 'recording' else [1, 1, 1, 0.05]

    def set_edit_mode(self, mode, btn): self.edit_mode = mode; self._update_button_states(self.mode_buttons, btn)
    def set_note_duration(self, dur, btn): self.note_duration = dur; self._update_button_states(self.duration_buttons, btn)

    def _update_button_states(self, group, active_btn):
        for btn in group.values():
            btn.md_bg_color = [0.4, 0.4, 0.8, 1] if btn == active_btn else [1, 1, 1, 0.05]
            btn.icon_color = [1, 1, 1, 1] if btn == active_btn else [0.8, 0.8, 0.8, 1]

    def sync_horizontal_scroll(self, instance, value):
        if self._is_scrolling: return
        self._is_scrolling = True
        ruler_scroll, timeline_scroll = self.ids.ruler.scroll_view, self.ids.timeline_scroll
        if instance == ruler_scroll: timeline_scroll.scroll_x = value
        else: ruler_scroll.scroll_x = value
        self._is_scrolling = False

    def _center_view_on_c4(self):
        grid_viewer = self.ids.grid_viewer
        max_scroll = (128 * self.note_height) - grid_viewer.height
        if max_scroll > 0:
            grid_viewer.scroll_y = max(0.0, min(1.0, ((60 * self.note_height) - (self.height / 2)) / max_scroll))
