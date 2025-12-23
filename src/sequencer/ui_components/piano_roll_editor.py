from kivy.uix.modalview import ModalView
from kivy.lang import Builder
from kivy.app import App
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.divider import MDDivider
from kivy.properties import ObjectProperty, NumericProperty, StringProperty, BooleanProperty, ListProperty
from . import TooltipMDIconButton, Ruler, PianoKeyboard, BoundedScrollView
from sequencer.ui_components.PianoRoll import PianoRoll
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.floatlayout import FloatLayout
from kivy.metrics import dp
from kivy.clock import Clock
from kivy.core.window import Window
import copy
from sequencer.models import Event, Note, MidiTrack
from .SaveDiscardCancelPopup import SaveDiscardCancelPopup
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle
from collections import deque
import copy


class EditHistoryManager:
    """Manages undo/redo history using a single list and an index."""
    def __init__(self, max_history=31):  # 30 undo steps + initial state
        self.history = deque(maxlen=max_history)
        self.index = -1

    def record_state(self, state):
        """Records a new state and invalidates any future 'redo' states."""
        # If we undo and then make a new change, the old redo history is gone.
        if self.index < len(self.history) - 1:
            # Create a new deque from the truncated history
            self.history = deque(list(self.history)[:self.index + 1], maxlen=self.history.maxlen)

        # The state is now a pre-serialized snapshot, no deepcopy needed.
        self.history.append(state)
        self.index = len(self.history) - 1

    def undo(self):
        """Moves the index back and returns the state at that position."""
        if self.can_undo():
            self.index -= 1
            return self.history[self.index]
        return None

    def redo(self):
        """Moves the index forward and returns the state at that position."""
        if self.can_redo():
            self.index += 1
            return self.history[self.index]
        return None

    def can_undo(self):
        return self.index > 0

    def can_redo(self):
        return self.index < len(self.history) - 1


# --- New Editable Grid Components (based on PianoRoll.py) ---

class EditableMidiGrid(PianoRoll):
    editor = ObjectProperty()
    _dragged_note = ObjectProperty(None, allownone=True)
    _drag_event = ObjectProperty(None, allownone=True)
    _drag_mode = StringProperty(None, allownone=True) # 'move', 'resize', or 'select'
    _drag_offset = (0, 0)
    _selection_start_pos = (0, 0)
    _selection_rect = None
    _selection_initial_states = None

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


    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super(EditableMidiGrid, self).on_touch_move(touch)

        local_pos = self.to_local(*touch.pos)

        if self._drag_mode == 'select':
            if self._selection_rect:
                self._selection_rect.size = (local_pos[0] - self._selection_start_pos[0], local_pos[1] - self._selection_start_pos[1])

                # Update selected notes based on the rectangle
                newly_selected = []
                x1, y1 = self._selection_start_pos
                x2, y2 = local_pos
                sel_x, sel_w = (min(x1, x2), abs(x1 - x2))
                sel_y, sel_h = (min(y1, y2), abs(y1 - y2))

                for event in self.editor.track_copy.events:
                    for note in event.notes:
                        note_x = event.start_time * self.pixels_per_beat
                        note_y = note.pitch * self.note_height
                        note_w = note.duration * self.pixels_per_beat
                        note_h = self.note_height

                        if sel_x < (note_x + note_w) and (sel_x + sel_w) > note_x and \
                           sel_y < (note_y + note_h) and (sel_y + sel_h) > note_y:
                            newly_selected.append(note)

                self.editor.selected_notes = newly_selected
                self.draw()
            return True


        if self._dragged_note:
            if self._drag_mode == 'resize_end':
                note_start_x = self._drag_event.start_time * self.pixels_per_beat
                new_width = local_pos[0] - note_start_x
                new_duration = max(0.1, round((new_width / self.pixels_per_beat) * 4) / 4) # Quantize to 16th notes
                self._dragged_note.duration = new_duration

            elif self._drag_mode == 'resize_start':
                note_end_time = self._drag_event.start_time + self._dragged_note.duration
                new_start_x = local_pos[0]
                new_start_beat = round((new_start_x / self.pixels_per_beat) * 4) / 4

                if new_start_beat < note_end_time:
                    new_duration = note_end_time - new_start_beat
                    if new_duration >= 0.1:
                        # --- Isolate the note from its original event ---
                        note_to_move = self._dragged_note
                        self._drag_event.notes.remove(note_to_move)

                        # If the original event is now empty, remove it
                        if not self._drag_event.notes and not self._drag_event.cc_messages:
                            self.editor.track_copy.events.remove(self._drag_event)

                        # Update the note's properties
                        note_to_move.duration = new_duration

                        # Find or create a new event at the target beat
                        target_event = next((e for e in self.editor.track_copy.events if abs(e.start_time - new_start_beat) < 0.001), None)
                        if target_event:
                            if note_to_move not in target_event.notes:
                                target_event.notes.append(note_to_move)
                        else:
                            target_event = Event(start_time=new_start_beat, notes=[note_to_move])
                            self.editor.track_copy.add_event(target_event)

                        # Update the drag reference to the new event
                        self._drag_event = target_event

            elif self._drag_mode == 'move':
                new_x = local_pos[0] - self._drag_offset[0]
                new_y = local_pos[1] - self._drag_offset[1]

                # Quantize to 16th notes (4 positions per beat), same as resizing
                new_beat = round((new_x / self.pixels_per_beat) * 4) / 4
                new_pitch = max(0, min(127, int(new_y / self.note_height)))

                self._drag_event.start_time = new_beat
                self._dragged_note.pitch = new_pitch

            self.editor.is_dirty = True
            self.draw()
            return True
        return super(EditableMidiGrid, self).on_touch_move(touch)

    def _store_selection_states_if_needed(self, dragged_note):
        """If multiple notes are selected, store their initial states for group operations."""
        if len(self.editor.selected_notes) > 1 and dragged_note in self.editor.selected_notes:
            self._selection_initial_states = {}
            # Use the note's id() as the key, since Note objects are not hashable
            note_to_event_map = {id(note): event for event in self.editor.track_copy.events for note in event.notes}

            for note in self.editor.selected_notes:
                note_id = id(note)
                if note_id in note_to_event_map:
                    event = note_to_event_map[note_id]
                    self._selection_initial_states[note_id] = {
                        'note_obj': note,
                        'pitch': note.pitch,
                        'duration': note.duration,
                        'start_time': event.start_time,
                        'event': event
                    }

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
                    handle_width = min(dp(8), note_width / 4) if note_width > dp(16) else 0

                    # Check for right handle resize
                    if note_x + note_width - handle_width <= local_pos[0] <= note_x + note_width and \
                       note_y <= local_pos[1] <= note_y + self.note_height:
                        self._dragged_note = note
                        self._drag_event = event
                        self._drag_mode = 'resize_end'
                        self._store_selection_states_if_needed(note)
                        Window.set_system_cursor('size_we')
                        touch.grab(self)
                        return True

                    # Check for left handle resize
                    elif note_x <= local_pos[0] <= note_x + handle_width and \
                            note_y <= local_pos[1] <= note_y + self.note_height:
                        self._dragged_note = note
                        self._drag_event = event
                        self._drag_mode = 'resize_start'
                        self._store_selection_states_if_needed(note)
                        Window.set_system_cursor('size_we')
                        touch.grab(self)
                        return True

                    # Check for note move
                    elif note_x <= local_pos[0] <= note_x + note_width and \
                         note_y <= local_pos[1] <= note_y + self.note_height:
                        # --- CORRECTED SELECTION LOGIC ---
                        # Use an identity check (`is`) to see if the *exact* note instance is already selected.
                        # The `in` operator uses equality (`==`), which fails for identical but distinct notes.
                        is_already_selected = any(note is sel_note for sel_note in self.editor.selected_notes)
                        if not is_already_selected:
                            self.editor.selected_notes = [note]
                            self.editor._record_state()

                        self._dragged_note = note
                        self._drag_event = event
                        self._drag_mode = 'move'
                        self._drag_offset = (local_pos[0] - note_x, local_pos[1] - note_y)

                        self._store_selection_states_if_needed(note)
                        
                        self.editor.selected_event = event # Gardé pour compatibilité, mais moins utile en multi-select
                        self.draw()

                        touch.grab(self)
                        return True

            # If no note was clicked, it's a click on an empty space.
            # This action should clear any existing selection.
            if self.editor.selected_notes:
                self.editor.selected_notes.clear()

            # After clearing selection (if any), prepare for a potential rubber-band selection.
            self._drag_mode = 'select'
            self._selection_start_pos = local_pos
            with self.canvas.after:
                Color(1, 1, 1, 0.3)
                self._selection_rect = Rectangle(pos=local_pos, size=(0, 0))
            touch.grab(self)
            self.draw()
            return True

        if edit_mode == 'insert':
            # Quantize to 16th notes, which is a common default for piano rolls
            quantized_beat = round(clicked_beat * 4) / 4
            new_note = Note(pitch=clicked_pitch, velocity=100, duration=self.editor.note_duration)
            target_event = next((e for e in track.events if abs(e.start_time - quantized_beat) < 0.001), None)

            if target_event:
                if not any(n.pitch == new_note.pitch for n in target_event.notes): target_event.notes.append(new_note)
            else:
                track.events.append(Event(start_time=quantized_beat, notes=[new_note]))
                track.events.sort(key=lambda e: e.start_time)

            self.editor.is_dirty = True
            self.draw()
            # This was the missing call from the review
            self.editor._record_state()
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
                            self.editor._record_state()
                            return True

        return super(EditableMidiGrid, self).on_touch_down(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return super(EditableMidiGrid, self).on_touch_up(touch)

        if self._drag_mode == 'select':
            if self._selection_rect:
                self.canvas.after.remove(self._selection_rect)
                self._selection_rect = None
            # Record the state after the selection is finalized.
            self.editor._record_state()

        if self._dragged_note:
            if self._selection_initial_states:
                self._apply_multi_selection_changes()
                self._selection_initial_states = None

            if self._drag_mode in ('resize_start', 'resize_end', 'move'):
                Window.set_system_cursor('arrow')
            if self._drag_mode == 'move':
                # Tri final pour s'assurer que les événements déplacés sont dans le bon ordre
                self.editor.track_copy.events.sort(key=lambda e: e.start_time)
            self._dragged_note = None
            self._drag_event = None

            self.editor._record_state()

        self._drag_mode = None
        touch.ungrab(self)
        self.draw() # Redessine la grille pour afficher l'état final
        return True

    def _apply_multi_selection_changes(self):
        """Apply the final transformation to all selected notes based on the dragged note."""
        dragged_note_id = id(self._dragged_note)
        dragged_note_initial_state = self._selection_initial_states.get(dragged_note_id)
        if not dragged_note_initial_state:
            return

        dragged_note_final_event = next((e for e in self.editor.track_copy.events if self._dragged_note in e.notes), None)
        if not dragged_note_final_event:
            return

        # --- Calculer les deltas ---
        pitch_delta = self._dragged_note.pitch - dragged_note_initial_state['pitch']
        time_delta = dragged_note_final_event.start_time - dragged_note_initial_state['start_time']
        new_duration = self._dragged_note.duration

        # --- Appliquer les transformations aux autres notes ---
        for note_id, initial_state in self._selection_initial_states.items():
            if note_id == dragged_note_id:
                continue # Déjà modifié par l'interaction directe

            note = initial_state['note_obj']

            # Appliquer les deltas
            new_pitch = initial_state['pitch'] + pitch_delta
            new_start_time = initial_state['start_time'] + time_delta

            if self._drag_mode == 'move':
                note.pitch = max(0, min(127, new_pitch))
                # Déplacer la note vers un nouvel événement
                self._move_note_to_new_time(note, initial_state['event'], new_start_time)
            elif self._drag_mode == 'resize_end':
                note.duration = new_duration
            elif self._drag_mode == 'resize_start':
                # Pour un redimensionnement par le début, la durée et la position changent.
                note.duration = new_duration
                self._move_note_to_new_time(note, initial_state['event'], new_start_time)

        self.editor.is_dirty = True

    def _move_note_to_new_time(self, note, original_event, new_start_time):
        """Helper to move a note from its original event to an event at the new start time."""
        # Retirer la note de l'événement d'origine
        if note in original_event.notes:
            original_event.notes.remove(note)
            # Si l'événement d'origine est vide, le supprimer
            if not original_event.notes and not original_event.cc_messages:
                if original_event in self.editor.track_copy.events:
                    self.editor.track_copy.events.remove(original_event)

        # Trouver ou créer un événement à la nouvelle position
        target_event = next((e for e in self.editor.track_copy.events if abs(e.start_time - new_start_time) < 0.001), None)
        if target_event:
            if note not in target_event.notes:
                target_event.notes.append(note)
        else:
            new_event = Event(start_time=new_start_time, notes=[note])
            self.editor.track_copy.add_event(new_event)


class EditablePianoRollViewer(ScrollView):
    editor = ObjectProperty()
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    note_height = NumericProperty(dp(12))

    def __init__(self, **kwargs):
        super(EditablePianoRollViewer, self).__init__(**kwargs)
        self.scroll_type = ['bars'] # Disable content scrolling
        self.size_hint_x = None
        self.do_scroll_x = False
        self.do_scroll_y = True
        self.grid = EditableMidiGrid(editor=self.editor, track=self.track, total_beats=self.total_beats, pixels_per_beat=self.pixels_per_beat, note_height=self.note_height)
        self.grid.editor = self.editor # Pass the editor instance to the grid
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
                theme_bg_color: "Custom"
                on_press: root.set_edit_mode('insert', self)
            TooltipMDIconButton:
                id: move_button
                icon: 'drag-variant'
                tooltip_text: "Move Mode"
                theme_bg_color: "Custom"
                on_press: root.set_edit_mode('move', self)
            TooltipMDIconButton:
                id: delete_button
                icon: 'minus-box'
                tooltip_text: "Delete Mode"
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

            Label:
                text: "Duration:"
                size_hint_x: None
                width: self.texture_size[0]

            TooltipMDIconButton:
                id: whole_note_button
                icon: 'music-note-whole'
                tooltip_text: "Whole Note (4 beats)"
                theme_bg_color: "Custom"
                on_press: root.set_note_duration(4.0, self)
            TooltipMDIconButton:
                id: half_note_button
                icon: 'music-note-half'
                tooltip_text: "Half Note (2 beats)"
                theme_bg_color: "Custom"
                on_press: root.set_note_duration(2.0, self)
            TooltipMDIconButton:
                id: quarter_note_button
                icon: 'music-note-quarter'
                tooltip_text: "Quarter Note (1 beat)"
                theme_bg_color: "Custom"
                on_press: root.set_note_duration(1.0, self)
            TooltipMDIconButton:
                id: eighth_note_button
                icon: 'music-note-eighth'
                tooltip_text: "Eighth Note (0.5 beats)"
                theme_bg_color: "Custom"
                on_press: root.set_note_duration(0.5, self)
            TooltipMDIconButton:
                id: sixteenth_note_button
                icon: 'music-note-sixteenth'
                tooltip_text: "Sixteenth Note (0.25 beats)"
                theme_bg_color: "Custom"
                on_press: root.set_note_duration(0.25, self)
            TooltipMDIconButton:
                id: thirty_second_note_button
                icon: 'music-note-thirty-second'
                tooltip_text: "Thirty-second Note (0.125 beats)"
                theme_bg_color: "Custom"
                on_press: root.set_note_duration(0.125, self)

            MDDivider:
                orientation: 'vertical'

            TooltipMDIconButton:
                id: dotted_button
                icon: 'circle-small'
                tooltip_text: "Dotted Note (Toggle)"
                theme_bg_color: "Custom"
                on_press: root.toggle_dotted_mode()

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
            keyboard_width: -dp(138)
            spacing: 0
            padding: [0, dp(6), 0, dp(6)]
            label_padding_x: 0

        BoxLayout:
            id: main_content
            orientation: 'horizontal'

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
                bar_width: dp(20)
                scroll_type: ['bars']
                padding: [0, 0, 0, dp(20)]

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
    original_track_index = NumericProperty(None)
    track_copy = ObjectProperty()
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128)
    note_height = NumericProperty(dp(14))
    edit_mode = StringProperty('insert')
    note_duration = NumericProperty(1.0) # Default to quarter note
    base_note_duration = NumericProperty(1.0)
    dotted_mode = BooleanProperty(False)
    is_dirty = BooleanProperty(False)
    _is_scrolling = False
    _update_event = None
    selected_note = ObjectProperty(None, allownone=True) # Will be deprecated in favor of selected_notes
    selected_notes = ListProperty([])
    selected_event = ObjectProperty(None, allownone=True)
    history = ObjectProperty(None)

    def __init__(self, **kwargs):
        self.history = EditHistoryManager()
        super(PianoRollEditor, self).__init__(**kwargs)
        self.original_track_index = self.sequencer_layout.sequencer.song.tracks.index(self.track)
        self.track_copy = MidiTrack(
            name=self.track.name,
            channel=self.track.channel,
            instrument=self.track.instrument,
            is_muted=self.track.is_muted,
            is_solo=self.track.is_solo,
            volume=self.track.volume,
            pan=self.track.pan,
            velocity=self.track.velocity,
            events=copy.deepcopy(self.track.events),
            bank_msb=self.track.bank_msb,
            bank_lsb=self.track.bank_lsb,
            output_port_name=self.track.output_port_name,
            record_mode=self.track.record_mode,
            is_metronome=self.track.is_metronome
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
            1.0: self.ids.quarter_note_button, 0.5: self.ids.eighth_note_button,
            0.25: self.ids.sixteenth_note_button, 0.125: self.ids.thirty_second_note_button
        }
        self.set_edit_mode(self.edit_mode, self.mode_buttons[self.edit_mode])
        self.set_note_duration(self.base_note_duration, self.duration_buttons[self.base_note_duration])
        self.on_playback_state_change(None, self.sequencer_layout.sequencer.playback_state)
        self.ids.ruler.redraw()

        # Bind selected_notes properties
        self.bind(selected_notes=self.ids.grid_viewer.grid.setter('selected_notes'))
        self.bind(selected_notes=self._update_legacy_selection)

        # Record the initial state
        self._record_state()
        # Set initial button state
        self._update_undo_redo_buttons_state()

        # Keyboard shortcuts
        Window.bind(on_key_down=self._on_key_down)

    def _on_key_down(self, instance, keyboard, keycode, text, modifiers):
        """Handle keyboard shortcuts for the editor."""
        # --- Modifier Shortcuts (Ctrl) ---
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

        # --- Non-Modifier Shortcuts ---
        # Note: 'keyboard' argument is the integer keycode from Kivy

        # Duration Shortcuts (Numpad)
        duration_map = {
            256: 4.0,  # Numpad 0 -> Whole
            257: 2.0,  # Numpad 1 -> Half
            258: 1.0,  # Numpad 2 -> Quarter
            259: 0.5,  # Numpad 3 -> Eighth
            260: 0.25, # Numpad 4 -> Sixteenth
        }
        if keyboard in duration_map:
            duration = duration_map[keyboard]
            button = self.duration_buttons.get(duration)
            if button:
                self.set_note_duration(duration, button)
                if self.selected_notes:
                    self._record_state()
                return True

        # Dotted Note Shortcut (Numpad Decimal)
        if keyboard == 266:
            self.toggle_dotted_mode()
            if self.selected_notes:
                self._record_state()
            return True

        # Grid Navigation (Arrow Keys)
        if keyboard in (273, 274, 275, 276): # Up, Down, Right, Left
            if keyboard in (276, 275): # Left, Right
                timeline_scroll = self.ids.timeline_scroll
                grid = self.ids.grid_viewer.grid
                beats_per_measure = getattr(self.sequencer_layout.sequencer.song, 'time_signature_numerator', 4)
                measure_width_pixels = beats_per_measure * self.pixels_per_beat
                max_scroll_pixels = grid.width - timeline_scroll.width
                if max_scroll_pixels > 0:
                    current_scroll_pixels = timeline_scroll.scroll_x * max_scroll_pixels
                    direction = 1 if keyboard == 275 else -1 # Right is +, Left is -
                    new_scroll_pixels = current_scroll_pixels + (measure_width_pixels * direction)
                    new_scroll_pixels = max(0, min(new_scroll_pixels, max_scroll_pixels))
                    timeline_scroll.scroll_x = new_scroll_pixels / max_scroll_pixels
                return True

            if keyboard in (273, 274): # Up, Down
                grid_viewer = self.ids.grid_viewer
                grid = self.ids.grid_viewer.grid
                octave_height_pixels = 12 * self.note_height
                max_scroll_pixels = grid.height - grid_viewer.height
                if max_scroll_pixels > 0:
                    current_scroll_pixels = grid_viewer.scroll_y * max_scroll_pixels
                    direction = 1 if keyboard == 273 else -1 # Up is +, Down is -
                    new_scroll_pixels = current_scroll_pixels + (octave_height_pixels * direction)
                    new_scroll_pixels = max(0, min(new_scroll_pixels, max_scroll_pixels))
                    grid_viewer.scroll_y = new_scroll_pixels / max_scroll_pixels
                return True

        return False

    def undo(self):
        """Restores the previous state from the history manager."""
        previous_state = self.history.undo()
        if previous_state is not None:
            self._apply_state(previous_state)

    def redo(self):
        """Restores the next state from the history manager."""
        next_state = self.history.redo()
        if next_state is not None:
            self._apply_state(next_state)

    def _apply_state(self, state):
        """Applies a given state (events and selection) to the editor."""
        # Reconstruct the events and notes from the snapshot.
        new_events = []
        for event_data in state['events']:
            new_notes = [Note(**note_data) for note_data in event_data['notes']]
            new_events.append(Event(start_time=event_data['start_time'], notes=new_notes))

        self.track_copy.events = new_events

        # Restore selection using the newly created note objects.
        new_selection = []
        selection_ids = state.get('selection', [])
        for event_index, note_index in selection_ids:
            if event_index < len(self.track_copy.events):
                event = self.track_copy.events[event_index]
                if note_index < len(event.notes):
                    new_selection.append(event.notes[note_index])

        self.selected_notes = new_selection
        # Explicitly update the grid's property to ensure the visual update.
        self.ids.grid_viewer.grid.selected_notes = self.selected_notes
        self.ids.grid_viewer.grid.draw()
        self._update_undo_redo_buttons_state()
        self.is_dirty = True

    def _update_undo_redo_buttons_state(self):
        """Enables/disables the undo/redo buttons based on history."""
        self.ids.undo_button.disabled = not self.history.can_undo()
        self.ids.redo_button.disabled = not self.history.can_redo()

    def _record_state(self):
        """Records the current state of the track (events and selection) for undo/redo."""
        # Create a serializable snapshot of the events to avoid deepcopy issues with Kivy objects.
        events_snapshot = [
            {
                'start_time': event.start_time,
                'notes': [
                    {'pitch': note.pitch, 'velocity': note.velocity, 'duration': note.duration}
                    for note in event.notes
                ]
            }
            for event in self.track_copy.events
        ]

        # Create a list of stable identifiers for the selected notes.
        note_to_event_map = {id(note): event for event in self.track_copy.events for note in event.notes}
        selection_ids = []
        for note in self.selected_notes:
            event = note_to_event_map.get(id(note))
            if event:
                try:
                    # Find the index of the event *in the original list*
                    event_index = self.track_copy.events.index(event)
                    note_index = event.notes.index(note)
                    selection_ids.append((event_index, note_index))
                except ValueError:
                    pass  # Should not happen in a consistent state

        state = {
            'events': events_snapshot,
            'selection': selection_ids
        }
        self.history.record_state(state)
        self._update_undo_redo_buttons_state()

    def _update_legacy_selection(self, *args):
        if self.selected_notes:
            self.selected_note = self.selected_notes[0]
        else:
            self.selected_note = None
            self.selected_event = None

    def on_dismiss(self):
        Window.unbind(on_key_down=self._on_key_down)
        self.sequencer_layout.sequencer.unbind(playback_state=self.on_playback_state_change)
        if self._update_event:
            self._update_event.cancel()
        # Ensure the override is removed when the editor is closed
        if self.original_track_index in self.sequencer_layout.sequencer.track_overrides:
            del self.sequencer_layout.sequencer.track_overrides[self.original_track_index]


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

        # --- Live Preview Logic ---
        sequencer = self.sequencer_layout.sequencer
        if state in ('playing', 'recording'):
            # When playback starts, apply the edited track as an override
            sequencer.track_overrides[self.original_track_index] = self.track_copy
        else:
            # When playback stops, remove the override
            if self.original_track_index in sequencer.track_overrides:
                del sequencer.track_overrides[self.original_track_index]

    def set_edit_mode(self, mode, btn):
        self.edit_mode = mode
        self._update_button_states(self.mode_buttons, btn)
        # If switching away from the selection-enabled mode, clear selection
        if mode != 'move':
            if self.selected_notes:
                self.selected_notes.clear()
                self.ids.grid_viewer.grid.draw()

    def set_note_duration(self, dur, btn):
        self.base_note_duration = dur
        self._update_note_duration()
        self._update_button_states(self.duration_buttons, btn)

    def toggle_dotted_mode(self):
        self.dotted_mode = not self.dotted_mode
        self._update_note_duration()

        # Update button appearance
        dotted_button = self.ids.dotted_button
        if self.dotted_mode:
            dotted_button.md_bg_color = [0.9, 0.7, 0, 1] # Active color
            dotted_button.icon_color = [0.1, 0.1, 0.1, 1]
        else:
            dotted_button.md_bg_color = [0.2, 0.2, 0.2, 1] # Inactive color
            dotted_button.icon_color = [0.8, 0.8, 0.8, 1]

    def _update_note_duration(self):
        """Calculates the final note duration and applies it to all selected notes."""
        multiplier = 1.5 if self.dotted_mode else 1.0
        new_duration = self.base_note_duration * multiplier
        self.note_duration = new_duration

        if self.selected_notes:
            for note in self.selected_notes:
                note.duration = new_duration
            self.is_dirty = True
            self.ids.grid_viewer.grid.draw()

    def _update_button_states(self, group, active_btn):
        """
        Updates the visual state of a group of buttons by changing their background color
        to a fixed, high-contrast color to ensure visibility regardless of theme.
        """
        # A bright yellow, similar to the main pause button, for high visibility.
        active_bg_color = [0.9, 0.7, 0, 1]
        # A neutral dark color for inactive buttons.
        inactive_bg_color = [0.2, 0.2, 0.2, 1]
        # A dark icon for good contrast on the yellow background.
        active_icon_color = [0.1, 0.1, 0.1, 1]
         # A light grey icon for the inactive state.
        inactive_icon_color = [0.8, 0.8, 0.8, 1]

        for btn in group.values():
            is_active = btn == active_btn
            btn.md_bg_color = active_bg_color if is_active else inactive_bg_color
            btn.icon_color = active_icon_color if is_active else inactive_icon_color

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
