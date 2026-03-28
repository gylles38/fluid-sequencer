from .floating_window import FloatingWindow
from kivy.lang import Builder
from kivy.app import App
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.divider import MDDivider
from kivy.properties import ObjectProperty, NumericProperty, StringProperty, BooleanProperty, ListProperty
from . import TooltipMDIconButton, Ruler, PianoKeyboard, BoundedScrollView
from .ui_utils import is_any_text_input_focused
from sequencer.ui_components.PianoRoll import PianoRoll
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.relativelayout import RelativeLayout
from kivy.metrics import dp
from kivy.clock import Clock
from kivy.core.window import Window
import copy
from sequencer.models import Event, Note, MidiTrack
from .SaveDiscardCancelPopup import SaveDiscardCancelPopup
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle, PushMatrix, PopMatrix, Translate, InstructionGroup
from collections import deque
import copy
import mido
import bisect
from sequencer.ui_components.HoverBehavior import HoverableButton


class EditHistoryManager:
    """Manages undo/redo history using a single list and an index."""
    def __init__(self, max_history=31) -> None:  # 30 undo steps + initial state
        self.history = deque(maxlen=max_history)
        self.index = -1

    def record_state(self, state) -> None:
        """Records a new state and invalidates any future 'redo' states."""
        # If we undo and then make a new change, the old redo history is gone.
        if self.index < len(self.history) - 1:
            # Create a new deque from the truncated history
            self.history = deque(list(self.history)[:self.index + 1], maxlen=self.history.maxlen)

        # The state is now a pre-serialized snapshot, no deepcopy needed.
        self.history.append(state)
        self.index: int = len(self.history) - 1

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

    def can_undo(self) -> bool:
        return self.index > 0

    def can_redo(self) -> bool:
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
    _selection_border = None
    _selection_group = None
    _selection_initial_states = None
    def __init__(self, **kwargs) -> None:
        self.playback_line_x = 0
        self.playback_rect = None
        self._selection_group = None
        super().__init__(**kwargs)

    def draw(self, *args):
        # We don't clear canvas.after here anymore as we want the selection group to persist
        # through debounced redraws. We only update the playback line.

        super().draw(*args) # Clears before and canvas. Redraws notes.

        if hasattr(self, 'canvas'):
            # Playback line is managed separately to avoid clearing the whole after-canvas
            if not self.playback_rect:
                with self.canvas.after:
                    Color(1, 0, 0, 0.8)
                    self.playback_rect = Rectangle(pos=(self.playback_line_x, 0), size=(dp(2), self.height))
            else:
                self.playback_rect.pos = (self.playback_line_x, 0)
                self.playback_rect.size = (dp(2), self.height)

    def add_playback_line(self) -> None:
        # Now handled by direct canvas drawing in draw()
        self.draw()

    def set_playback_line_x(self, x):
        self.playback_line_x = x
        if self.playback_rect:
            self.playback_rect.pos = (x, 0)

    def on_touch_move(self, touch) -> None | bool:
        if touch.grab_current is not self:
            return super(EditableMidiGrid, self).on_touch_move(touch)

        # Robust coordinate transformation
        local_pos = self.to_local(*touch.pos)
        
        if self._drag_mode == 'select':
            if self._selection_rect:
                width = local_pos[0] - self._selection_start_pos[0]
                height = local_pos[1] - self._selection_start_pos[1]
                self._selection_rect.size = (width, height)
                if self._selection_border:
                    self._selection_border.rectangle = (self._selection_start_pos[0], self._selection_start_pos[1], width, height)

                # Performance Optimization: Calculate selected notes based on current viewport.
                # Redrawing the entire grid on every mouse move is expensive and causes freezes.
                # We update the selection list but use a debounced redraw.
                newly_selected = []
                x1, y1 = self._selection_start_pos
                x2, y2 = local_pos
                sel_x, sel_w = (min(x1, x2), abs(x1 - x2))
                sel_y, sel_h = (min(y1, y2), abs(y1 - y2))

                # Temporal boundaries for the selection box
                sel_start_beat = sel_x / self.pixels_per_beat
                sel_end_beat = (sel_x + sel_w) / self.pixels_per_beat

                # Performance Optimization: Use binary search to narrow down selection range
                search_idx = bisect.bisect_left(self.editor.track_copy.events, sel_start_beat - 32, key=lambda e: e.start_time)

                for i in range(search_idx, len(self.editor.track_copy.events)):
                    event = self.editor.track_copy.events[i]
                    if event.start_time > sel_end_beat:
                        break # Optimization: stopped searching after the selection box

                    for note in event.notes:
                        note_x = event.start_time * self.pixels_per_beat
                        note_y = note.pitch * self.note_height
                        note_w = note.duration * self.pixels_per_beat
                        note_h = self.note_height

                        if sel_x < (note_x + note_w) and (sel_x + sel_w) > note_x and \
                           sel_y < (note_y + note_h) and (sel_y + sel_h) > note_y:
                            newly_selected.append(note)

                if len(newly_selected) != len(self.editor.selected_notes) or newly_selected != self.editor.selected_notes:
                    self.editor.selected_notes = newly_selected
                    self.redraw() # Use debounced redraw
            return True

        if self._dragged_note:
            if self._drag_mode == 'move':
                new_x = local_pos[0] - self._drag_offset[0]
                new_y = local_pos[1] - self._drag_offset[1]

                new_beat = new_x / self.pixels_per_beat
                new_pitch = int(new_y / self.note_height)

                # Quantize for visual feedback and snap
                new_beat = round(new_beat * 4) / 4
                new_pitch = max(0, min(127, new_pitch))

                try:
                    master_data = next(d for d in self._multi_drag_data if d['note'] is self._dragged_note)
                except (StopIteration, AttributeError):
                    # Fallback for single note move if _multi_drag_data is missing
                    self._drag_event.start_time = new_beat
                    self._dragged_note.pitch = new_pitch
                else:
                    delta_beat = new_beat - master_data['original_start']
                    delta_pitch = new_pitch - master_data['original_pitch']

                    earliest_start = min(item['original_start'] for item in self._multi_drag_data)
                    if earliest_start + delta_beat < 0:
                        delta_beat = -earliest_start

                    for item in self._multi_drag_data:
                        target_new_beat = item['original_start'] + delta_beat
                        target_new_pitch = max(0, min(127, int(item['original_pitch'] + delta_pitch)))
                        new_parent = self._move_note_logic(item['note'], target_new_beat, target_new_pitch, item['parent_event'])
                        item['parent_event'] = new_parent
                        if item['note'] is self._dragged_note:
                            self._drag_event = new_parent

            elif self._drag_mode == 'resize_end':
                note_start_x = self._drag_event.start_time * self.pixels_per_beat
                new_width = local_pos[0] - note_start_x
                new_duration: float = max(0.1, round((new_width / self.pixels_per_beat) * 4) / 4)
                self._dragged_note.duration = new_duration

            elif self._drag_mode == 'resize_start':
                note_end_time = self._drag_event.start_time + self._dragged_note.duration
                new_start_x = local_pos[0]
                new_start_beat = round((new_start_x / self.pixels_per_beat) * 4) / 4

                if new_start_beat < note_end_time:
                    new_duration = note_end_time - new_start_beat
                    if new_duration >= 0.1:
                        note_to_move = self._dragged_note
                        self._drag_event.notes.remove(note_to_move)
                        if not self._drag_event.notes and not self._drag_event.cc_messages:
                            self.editor.track_copy.events.remove(self._drag_event)
                        note_to_move.duration = new_duration
                        target_event = self._find_event_at(self.editor.track_copy, new_start_beat)
                        if target_event:
                            if note_to_move not in target_event.notes:
                                target_event.notes.append(note_to_move)
                        else:
                            target_event = Event(start_time=new_start_beat, notes=[note_to_move])
                            self.editor.track_copy.add_event(target_event)
                        self._drag_event = target_event

            self.editor.is_dirty = True
            self.draw()
            return True

        # Regression Fix: Returning True if grabbed ensures the touch is consumed and not passed to ScrollView
        return True

    def _find_event_at(self, track, beat, tolerance=0.001):
        """Finds an event at a specific beat using binary search."""
        if not track.events:
            return None
        idx = bisect.bisect_left(track.events, beat - tolerance, key=lambda e: e.start_time)
        if idx < len(track.events) and abs(track.events[idx].start_time - beat) <= tolerance:
            return track.events[idx]
        return None

    def _move_note_logic(self, note, new_beat, new_pitch, source_event):
        track = self.editor.track_copy
        
        # 1. Retrait par identité stricte
        if source_event and note in source_event.notes:
            source_event.notes = [n for n in source_event.notes if n is not note]
            if not source_event.notes and not source_event.cc_messages:
                if source_event in track.events:
                    track.events.remove(source_event)

        # 2. Mise à jour des propriétés
        note.pitch = int(new_pitch)
        
        # 3. Placement et récupération du nouvel Event
        target_event = self._find_event_at(track, new_beat)
        
        if target_event:
            # On vérifie si CETTE instance n'y est pas déjà
            if not any(n is note for n in target_event.notes):
                target_event.notes.append(note)
            return target_event
        else:
            new_event = Event(start_time=new_beat, notes=[note])
            track.add_event(new_event) # add_event maintains sort
            return new_event

    def _store_selection_states_if_needed(self, dragged_note) -> None:
        """If multiple notes are selected, store their initial states for group operations."""
        # Performance Optimization: Avoid building full note_to_event_map via nested loops.
        # We only need the mapping for currently selected notes.
        if len(self.editor.selected_notes) > 1 and any(dragged_note is sn for sn in self.editor.selected_notes):
            self._selection_initial_states = {}

            selected_ids = {id(n) for n in self.editor.selected_notes}
            note_to_event_map = {}

            for event in self.editor.track_copy.events:
                for note in event.notes:
                    if id(note) in selected_ids:
                        note_to_event_map[id(note)] = event
                if len(note_to_event_map) == len(selected_ids):
                    break

            for note in self.editor.selected_notes:
                note_id: int = id(note)
                if note_id in note_to_event_map:
                    event = note_to_event_map[note_id]
                    self._selection_initial_states[note_id] = {
                        'note_obj': note,
                        'pitch': note.pitch,
                        'duration': note.duration,
                        'start_time': event.start_time,
                        'event': event
                    }

    def on_touch_down(self, touch) -> None | bool:
        # Use to_local for robust coordinate handling regardless of parent layout.
        local_pos = self.to_local(*touch.pos)

        if not (0 <= local_pos[0] <= self.width and 0 <= local_pos[1] <= self.height):
            return super(EditableMidiGrid, self).on_touch_down(touch)

        clicked_beat = local_pos[0] / self.pixels_per_beat
        clicked_pitch = int(local_pos[1] / self.note_height)

        edit_mode = self.editor.edit_mode
        track = self.editor.track_copy

        # --- Note Preview Logic ---
        note_to_preview = None
        if edit_mode in ('insert', 'move'):
            # Find if there's a note at the clicked position
            # Optimization: Use binary search for preview too
            idx = bisect.bisect_right(track.events, clicked_beat, key=lambda e: e.start_time) - 1
            for i in range(idx, -1, -1):
                event = track.events[i]
                if event.start_time < clicked_beat - 32:
                    break
                for note in reversed(event.notes):
                    note_x = event.start_time * self.pixels_per_beat
                    note_y = note.pitch * self.note_height
                    note_width = note.duration * self.pixels_per_beat

                    if note_x <= local_pos[0] <= note_x + note_width and \
                       note_y <= local_pos[1] <= note_y + self.note_height:
                        note_to_preview = note
                        break
                if note_to_preview:
                    break

            velocity = note_to_preview.velocity if note_to_preview else 100
            duration_in_seconds = (60.0 / self.editor.sequencer_layout.sequencer.song.tempo) * self.editor.note_duration
            self.editor._preview_note(clicked_pitch, velocity, duration_in_seconds)


        if edit_mode == 'move':
            # Rubber-band selection is only available in 'move' mode as requested.
            # We first check for note clicks to prioritize direct editing.
            # Performance Optimization: Use binary search to narrow down events near the click
            search_beat = local_pos[0] / self.pixels_per_beat
            # Find events starting at or before the click
            idx = bisect.bisect_right(track.events, search_beat, key=lambda e: e.start_time) - 1

            # Check a window of events around the click (accounting for duration)
            # We check up to 32 beats before the click to handle long notes
            for i in range(idx, -1, -1):
                event = track.events[i]
                if event.start_time < search_beat - 32:
                    break
                for note in reversed(event.notes):
                    note_x = event.start_time * self.pixels_per_beat
                    note_y = note.pitch * self.note_height
                    note_width = note.duration * self.pixels_per_beat
                    handle_width: float | int = min(dp(8), note_width / 4) if note_width > dp(16) else 0

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
                        is_already_selected: bool = any(note is sel_note for sel_note in self.editor.selected_notes)
                        if not is_already_selected:
                            self.editor.selected_notes = [note]
                            self.editor._record_state()

                        self._dragged_note = note
                        self._drag_event = event
                        self._drag_mode = 'move'
                        self._drag_offset = (local_pos[0] - note_x, local_pos[1] - note_y)

                        # --- AJOUT POUR LE MULTI-MOVE ---
                        # On stocke la position de départ de TOUTES les notes sélectionnées
                        # Optimization: Avoid full track scan. We can use the note_to_event_map
                        # built during selection or a specialized O(N) pass if needed.
                        self._multi_drag_data = []
                        selected_ids = {id(sn) for sn in self.editor.selected_notes}

                        # Use a single pass to find all events for selected notes
                        # Still O(N) but better than O(N*S). We can't use binary search
                        # because we don't know the beats of all selected notes.
                        for ev in track.events:
                            for n in ev.notes:
                                if id(n) in selected_ids:
                                    self._multi_drag_data.append({
                                        'note': n,
                                        'parent_event': ev,
                                        'original_start': ev.start_time,
                                        'original_pitch': n.pitch
                                    })
                            if len(self._multi_drag_data) == len(selected_ids):
                                break

                        self._store_selection_states_if_needed(note)
                        
                        self.editor.selected_event = event # Gardé pour compatibilité, mais moins utile en multi-select
                        self.draw()

                        touch.grab(self)
                        return True

            # If no note was clicked, it's a click on an empty space.
            # This action should clear any existing selection.
            if self.editor.selected_notes:
                self.editor.selected_notes = []
                self.editor._record_state()

            # Prepare for a potential rubber-band selection.
            self._drag_mode = 'select'
            self._selection_start_pos = local_pos
            self._selection_group = InstructionGroup()
            self._selection_group.add(Color(1, 1, 1, 0.3))
            self._selection_rect = Rectangle(pos=local_pos, size=(0, 0))
            self._selection_group.add(self._selection_rect)
            self._selection_group.add(Color(1, 1, 1, 0.8))
            self._selection_border = Line(rectangle=(local_pos[0], local_pos[1], 0, 0), width=dp(1))
            self._selection_group.add(self._selection_border)
            self.canvas.after.add(self._selection_group)

            touch.grab(self)
            self.draw()
            return True

        if edit_mode == 'insert':
            # Quantize to 16th notes, which is a common default for piano rolls
            quantized_beat = round(clicked_beat * 4) / 4
            new_note = Note(pitch=clicked_pitch, velocity=100, duration=self.editor.note_duration)
            target_event = self._find_event_at(track, quantized_beat)

            if target_event:
                if not any(n.pitch == new_note.pitch for n in target_event.notes): target_event.notes.append(new_note)
            else:
                track.add_event(Event(start_time=quantized_beat, notes=[new_note]))

            self.editor.is_dirty = True
            self.draw()
            # This was the missing call from the review
            self.editor._record_state()
            return True

        elif edit_mode == 'delete':
            # Optimization: Use binary search for delete mode too
            idx = bisect.bisect_right(track.events, clicked_beat, key=lambda e: e.start_time) - 1
            for i in range(idx, -1, -1):
                event = track.events[i]
                if event.start_time < clicked_beat - 32:
                    break
                for note in reversed(event.notes):
                    if note.pitch == clicked_pitch:
                        if event.start_time <= clicked_beat <= (event.start_time + note.duration):
                            event.notes.remove(note)
                            if not event.notes and not event.cc_messages:
                                track.events.remove(event)
                            self.editor.is_dirty = True
                            self.draw()
                            self.editor._record_state()
                            return True

        return super(EditableMidiGrid, self).on_touch_down(touch)

    def on_touch_up(self, touch) -> None | bool:
        if touch.grab_current is not self:
            return super(EditableMidiGrid, self).on_touch_up(touch)

        if self._drag_mode == 'select':
            if self._selection_group:
                try:
                    self.canvas.after.remove(self._selection_group)
                except ValueError:
                    pass
                self._selection_group = None
                self._selection_rect = None
            # Record the state after the selection is finalized.
            self.editor._record_state()

        if self._dragged_note:
            if hasattr(self, '_multi_drag_data'):
                self._multi_drag_data.clear()

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

    def _apply_multi_selection_changes(self) -> None:
        """Apply the final transformation to all selected notes based on the dragged note."""
        dragged_note_id: int = id(self._dragged_note)
        dragged_note_initial_state = self._selection_initial_states.get(dragged_note_id)
        if not dragged_note_initial_state:
            return

        # Find which event the dragged note is currently in (after move)
        # Optimization: Use binary search near the expected final beat
        final_beat_approx = self._dragged_note.pitch # This is pitch, not beat.
        # We actually don't have the final beat saved in the note object,
        # but we might have updated the note's parent event during move.
        # Let's do a more efficient scan if possible, but for multi-move
        # we usually have the _drag_event updated.

        dragged_note_final_event = self._drag_event
        if not dragged_note_final_event or self._dragged_note not in dragged_note_final_event.notes:
            # Fallback search
            for e in self.editor.track_copy.events:
                if self._dragged_note in e.notes:
                    dragged_note_final_event = e
                    break

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

    def _move_note_to_new_time(self, note, original_event, new_start_time) -> None:
        # Retirer la note de l'événement d'origine par identité
        if original_event and note in original_event.notes:
            original_event.notes = [n for n in original_event.notes if n is not note]
            if not original_event.notes and not original_event.cc_messages:
                if original_event in self.editor.track_copy.events:
                    self.editor.track_copy.events.remove(original_event)

        target_event = self._find_event_at(self.editor.track_copy, new_start_time)
        if target_event:
            # CORRECTION : Empêcher l'ajout si l'instance est déjà là
            if not any(n is note for n in target_event.notes):
                target_event.notes.append(note)
        else:
            new_event = Event(start_time=new_start_time, notes=[note])
            self.editor.track_copy.add_event(new_event)

class EditablePianoRollViewer(BoundedScrollView):
    editor = ObjectProperty()
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    note_height = NumericProperty(round(dp(14)))

    def __init__(self, **kwargs) -> None:
        super(EditablePianoRollViewer, self).__init__(**kwargs)
        self.scroll_type: list[str] = ['bars'] # Disable content scrolling
        self.size_hint_x = None
        self.do_scroll_x = False
        self.do_scroll_y = True

        # Regression Fix: Wrapping the grid in a RelativeLayout container ensures
        # it maintains stable local coordinates for drawing and touch processing,
        # isolating it from ScrollView's internal coordinate shifts.
        self.container = RelativeLayout(size_hint=(None, None))
        self.add_widget(self.container)

        self.grid = EditableMidiGrid(editor=self.editor, track=self.track, total_beats=self.total_beats, pixels_per_beat=self.pixels_per_beat, note_height=self.note_height)
        self.grid.editor = self.editor
        self.container.add_widget(self.grid)

        # Bind container size to grid, and viewer width to container
        self.grid.bind(size=self.container.setter('size'))
        self.container.bind(width=self.setter('width'))

    def on_touch_move(self, touch) -> bool | None:
        # If the grid has grabbed the touch for a selection or move operation,
        # we must not process it for scrolling. We consume the event by returning True.
        if touch.grab_current is self.grid:
            return True
        return super(EditablePianoRollViewer, self).on_touch_move(touch)

    def on_touch_up(self, touch) -> bool | None:
        # Regression Fix: Prevent vertical jumps or momentum scrolling on release
        # when the interaction with the grid ends.
        if touch.grab_current is self.grid:
            return True
        return super(EditablePianoRollViewer, self).on_touch_up(touch)

    def on_editor(self, i, v) -> None: self.grid.editor = v
    def on_track(self, i, v) -> None: self.grid.track = v
    def on_total_beats(self, i, v) -> None: self.grid.total_beats = v
    def on_pixels_per_beat(self, i, v) -> None: self.grid.pixels_per_beat = v
    def on_note_height(self, i, v) -> None:
        self.grid.note_height = v
        # Regression Fix: Explicitly update the grid's height to match the full note range
        self.grid.height = 128 * v


# --- Builder String ---
Builder.load_string("""
<PianoRollEditor>:
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
                theme_icon_color: "Custom"
                on_press: root.set_note_duration(4.0, self)
            TooltipMDIconButton:
                id: half_note_button
                icon: 'music-note-half'
                tooltip_text: "Half Note (2 beats)"
                theme_icon_color: "Custom"
                on_press: root.set_note_duration(2.0, self)
            TooltipMDIconButton:
                id: quarter_note_button
                icon: 'music-note-quarter'
                tooltip_text: "Quarter Note (1 beat)"
                theme_icon_color: "Custom"
                on_press: root.set_note_duration(1.0, self)
            TooltipMDIconButton:
                id: eighth_note_button
                icon: 'music-note-eighth'
                tooltip_text: "Eighth Note (0.5 beats)"
                theme_icon_color: "Custom"
                on_press: root.set_note_duration(0.5, self)
            TooltipMDIconButton:
                id: sixteenth_note_button
                icon: 'music-note-sixteenth'
                tooltip_text: "Sixteenth Note (0.25 beats)"
                theme_icon_color: "Custom"
                on_press: root.set_note_duration(0.25, self)
            TooltipMDIconButton:
                id: thirty_second_note_button
                icon: 'music-note-thirty-second'
                tooltip_text: "Thirty-second Note (0.125 beats)"
                theme_icon_color: "Custom"
                on_press: root.set_note_duration(0.125, self)

            MDDivider:
                orientation: 'vertical'

            TooltipMDIconButton:
                id: dotted_button
                icon: 'circle-small'
                tooltip_text: "Dotted Note (Toggle)"
                theme_icon_color: "Custom"
                on_press: root.toggle_dotted_mode()

            MDDivider:
                orientation: "vertical"
                adaptive_height: False
                height: dp(30)
                pos_hint: {"center_y": .5}
                
            # --- Boutons de Zoom (À insérer après duration_1_16) ---
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
                adaptive_height: False
                height: dp(30)
                pos_hint: {"center_y": .5}
            
            # --- Boutons de Transport (Existant) ---
            MDIconButton:
                id: play_pause_btn

            Widget:
                size_hint_x: 1

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
            TooltipMDIconButton:
                id: record_button
                icon: 'record-circle-outline'
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
            end_pos_str: root.end_pos_str
            size_hint_y: None
            height: dp(30)
            info_width: 0
            controls_width: 0
            spacing: 0
            keyboard_width: dp(60)
            padding: [0, 0, 0, 0]
            label_padding_x: 0

        BoxLayout:
            id: main_content
            orientation: 'horizontal'
            spacing: 0

            # --- Left Column: Keyboard ---
            BoxLayout:
                orientation: 'vertical'
                size_hint_x: None
                width: dp(60)

                BoundedScrollView:
                    id: keyboard_sv
                    size_hint: (1, 1)
                    do_scroll_x: False
                    bar_width: 0
                    scroll_type: ['content']

                    PianoKeyboard:
                        id: piano_keyboard
                        size_hint: (None, None)
                        width: self.parent.width
                        note_height: root.note_height

                # Exact spacer to match the grid side's horizontal scrollbar and bottom widget
                Widget:
                    size_hint_y: None
                    height: dp(15) + dp(18)

            # --- Right Column: Horizontal Timeline ---
            BoundedScrollView:
                id: timeline_scroll
                do_scroll_y: False
                do_scroll_x: True
                bar_width: dp(15)
                scroll_type: ['bars', 'content']
                bar_pos_x: 'bottom'
                bar_margin: dp(0)

                BoxLayout:
                    orientation: 'vertical'
                    size_hint_x: None
                    width: grid_viewer.width
                    # Usable height is timeline_scroll.height - bar_width.
                    # We want grid_viewer to have the same height as keyboard_sv.

                    EditablePianoRollViewer:
                        id: grid_viewer
                        editor: root
                        track: root.track_copy
                        total_beats: root.total_beats
                        pixels_per_beat: root.pixels_per_beat
                        note_height: root.note_height
                        bar_width: 0
                        scroll_type: ['bars']
                        size_hint_y: 1

                    Widget:
                        size_hint_y: None
                        height: dp(15) + dp(18)

        MDBoxLayout:
            size_hint_y: None
            height: dp(48)
            padding: dp(8)
            spacing: dp(8)
            md_bg_color: 0.2, 0.2, 0.2, 1

            Label:
                id: status_label
                text: "Note: C4"
                size_hint_x: None
                width: self.texture_size[0]
                color: 0.8, 0.8, 0.8, 1

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

class PianoRollEditor(FloatingWindow):
    min_width = NumericProperty(dp(750))
    sequencer_layout = ObjectProperty()
    track = ObjectProperty()
    original_track_index = NumericProperty(None)
    track_copy = ObjectProperty()
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128)
    note_height = NumericProperty(round(dp(14)))
    edit_mode = StringProperty('insert')
    note_duration = NumericProperty(1.0) # Default to quarter note
    base_note_duration = NumericProperty(1.0)
    dotted_mode = BooleanProperty(False)
    is_dirty = BooleanProperty(False)
    _is_scrolling = False
    _update_event = None
    end_pos_str = StringProperty('')
    selected_note = ObjectProperty(None, allownone=True) # Will be deprecated in favor of selected_notes
    selected_notes = ListProperty([])
    selected_event = ObjectProperty(None, allownone=True)
    history = ObjectProperty(None)
    hovered_note = ObjectProperty(None, allownone=True)
    display_beat = NumericProperty(0.0)
    saved_scroll_x = NumericProperty(0.0)
    last_playback_state = StringProperty("stopped")

    def __init__(self, **kwargs) -> None:
        self.history = EditHistoryManager()
        super(PianoRollEditor, self).__init__(**kwargs)
        self.source_track = self.track
        self.title = f"Piano Roll: {self.track.name}"
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

        # Bind the editor's end_pos_str to the main sequencer's property
        self.end_pos_str = self.sequencer_layout.sequencer.ui_end_pos_str
        self.sequencer_layout.sequencer.bind(ui_end_pos_str=self.setter('end_pos_str'))

        Clock.schedule_once(self._post_kv_init)
        self._update_event = Clock.schedule_interval(self.update_playhead, 1/30.0)
        self.clipboard_data = []

    def _post_kv_init(self, dt) -> None:
        keyboard_sv = self.ids.keyboard_sv
        grid_viewer = self.ids.grid_viewer
        ruler_scroll = self.ids.ruler.scroll_view
        timeline_scroll = self.ids.timeline_scroll

        keyboard_sv.bind(scroll_y=lambda i, v: setattr(grid_viewer, 'scroll_y', v))
        grid_viewer.bind(scroll_y=lambda i, v: setattr(keyboard_sv, 'scroll_y', v))

        self.ids.piano_keyboard.height = self.ids.grid_viewer.grid.height
        self.ids.grid_viewer.grid.bind(height=self.ids.piano_keyboard.setter('height'))

        # --- ALIGNMENT SYNC ---
        # Ensure Ruler's alignment properties match the editor's layout
        self.ids.ruler.keyboard_width = self.ids.keyboard_sv.width
        self.ids.ruler.info_width = 0
        self.ids.ruler.controls_width = 0
        self.ids.ruler.spacing = 0

        # Ensure ruler content width matches the grid
        self.ids.ruler.ruler_content.width = self.ids.grid_viewer.grid.width
        self.ids.grid_viewer.grid.bind(width=lambda i, v: setattr(self.ids.ruler.ruler_content, 'width', v))

        # Add the playback line here to ensure it's drawn on top
        self.ids.grid_viewer.grid.add_playback_line()

        ruler_scroll.bind(scroll_x=self.sync_horizontal_scroll)
        timeline_scroll.bind(scroll_x=self.sync_horizontal_scroll)

        self._center_view_on_c4()
        current_beat = self.sequencer_layout.sequencer.current_beat
        if current_beat > 0 and self.total_beats > 0:
            def sync_at_start(dt):
                scroll_pos = (current_beat * self.pixels_per_beat)
                max_scroll = self.ids.grid_viewer.grid.width - timeline_scroll.width
                if max_scroll > 0:
                    target_scroll_x = min(1.0, scroll_pos / max_scroll)
                    timeline_scroll.scroll_x = target_scroll_x
                    ruler_scroll.scroll_x = target_scroll_x
            # We schedule it to ensure widths are correctly computed
            Clock.schedule_once(sync_at_start)

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

        # Mouse cursor logic
        Window.bind(mouse_pos=self._on_mouse_pos)

    def _on_key_down(self, instance, keyboard, keycode, text, modifiers):
        """Handle keyboard shortcuts for the editor."""
        # --- Sécurité : Désactiver les raccourcis si un champ texte a le focus ---
        if is_any_text_input_focused():
            return False

        # --- Gestion de la Vélocité (+/-) ---
        # 43 = + (numpad), 45 = - (clavier/numpad), 61 = + (clavier principal)
        if text in ('+', '-') or keyboard in (43, 45, 61, 269, 270):
            if self.hovered_note:
                # Calcul du changement (pas de 5 ou 10 selon votre préférence)
                delta: int = 5 if text == '+' or keyboard in (43, 61, 270) else -5
                
                # Application et bridage entre 0 et 127
                new_vel: int = max(0, min(127, self.hovered_note.velocity + delta))
                
                if new_vel != self.hovered_note.velocity:
                    self.hovered_note.velocity = new_vel
                    self.is_dirty = True
                    
                    # Mise à jour immédiate de l'affichage
                    note_name: str = self._pitch_to_note_name(self.hovered_note.pitch)
                    self.ids.status_label.text = f"Note: {note_name}, Velocity: {self.hovered_note.velocity}"
                    
                    # Optionnel : redessiner la grille si la couleur dépend de la vélocité
                    self.ids.grid_viewer.grid.draw()
                    
                    # Enregistrement pour le Undo/Redo
                    self._record_state()
                return True
  
        if keyboard == 278:  # Code de la touche 'Home' dans Kivy
            self.go_to_start()

        if keyboard == 279:  # Code de la touche 'End' dans Kivy
            self.go_to_last_measure_start()
            return True
      
        # Sécurité : Si un champ de texte a le focus, on laisse l'événement passer
        # pour permettre l'écriture normalement.
        #if any(isinstance(w, FocusBehavior) and w.focus for w in window.children):
        #    return False
           
        # --- Barre d'espace (Play/Pause) ---
        if keyboard == 32:  # 32 est le code pour l'espace
            self.toggle_playback()
            return True # On indique que l'événement est consommé
                    
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
            
            key_name = keycode[1] if isinstance(keycode, tuple) else text

            if key_name == 'c':
                self._copy_selection(is_cut=False)
                return True
            
            elif key_name == 'x':
                self._copy_selection(is_cut=True)
                return True

            elif key_name == 'v':
                self._paste_selection()
                return True            

            elif key_name == 'a':
                self._select_all_notes()
                return True 
            
        # --- Non-Modifier Shortcuts ---
        # Note: 'keyboard' argument is the integer keycode from Kivy

        # Duration Shortcuts (Numpad)
        duration_map: dict[int, float] = {
            256: 4.0,  # Numpad 0 -> Whole
            257: 2.0,  # Numpad 1 -> Half
            258: 1.0,  # Numpad 2 -> Quarter
            259: 0.5,  # Numpad 3 -> Eighth
            260: 0.25, # Numpad 4 -> Sixteenth
        }
        if keyboard in duration_map:
            duration: float = duration_map[keyboard]
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
                beats_per_measure: copy.Any | int = getattr(self.sequencer_layout.sequencer.song, 'time_signature_numerator', 4)
                measure_width_pixels = beats_per_measure * self.pixels_per_beat
                max_scroll_pixels = grid.width - timeline_scroll.width
                if max_scroll_pixels > 0:
                    current_scroll_pixels = timeline_scroll.scroll_x * max_scroll_pixels
                    direction: int = 1 if keyboard == 275 else -1 # Right is +, Left is -
                    new_scroll_pixels = current_scroll_pixels + (measure_width_pixels * direction)
                    new_scroll_pixels: int = max(0, min(new_scroll_pixels, max_scroll_pixels))
                    timeline_scroll.scroll_x = new_scroll_pixels / max_scroll_pixels
                return True

            if keyboard in (273, 274): # Up, Down
                grid_viewer = self.ids.grid_viewer
                grid = self.ids.grid_viewer.grid
                octave_height_pixels = 12 * self.note_height
                max_scroll_pixels = grid.height - grid_viewer.height
                if max_scroll_pixels > 0:
                    current_scroll_pixels = grid_viewer.scroll_y * max_scroll_pixels
                    direction: int = 1 if keyboard == 273 else -1 # Up is +, Down is -
                    new_scroll_pixels = current_scroll_pixels + (octave_height_pixels * direction)
                    new_scroll_pixels: int = max(0, min(new_scroll_pixels, max_scroll_pixels))
                    grid_viewer.scroll_y = new_scroll_pixels / max_scroll_pixels
                return True

        # On vérifie aussi 'backspace' (8) qui est souvent utilisé pour supprimer
        if keyboard in (127, 8):
            if self.selected_notes:
                self._delete_selected_notes()
                # On enregistre l'état pour le Undo
                self._record_state()
                # On redessine la grille
                #if hasattr(self.ids.ruler, 'redraw'):
                #    self.ids.ruler.redraw()
                self.ids.grid_viewer.grid.draw()                
                return True # Indique que l'événement a été géré

        return False

    def undo(self) -> None:
        """Restores the previous state from the history manager."""
        previous_state = self.history.undo()
        if previous_state is not None:
            self._apply_state(previous_state)

    def redo(self) -> None:
        """Restores the next state from the history manager."""
        next_state = self.history.redo()
        if next_state is not None:
            self._apply_state(next_state)

    def move_to_beat(self, beat) -> None:
        """
        Méthode centralisée pour déplacer la tête de lecture, 
        synchroniser le séquenceur/JACK et ajuster le scroll.
        """
        # 1. Mise à jour de l'état du séquenceur et de l'UI
        sequencer = self.sequencer_layout.sequencer
        new_pos_str = sequencer._format_beats_to_position(beat)
        
        sequencer.ui_start_pos_str = new_pos_str
        self.sequencer_layout.start_pos_input.text = new_pos_str        
        
        # 2. Synchronisation moteur et JACK
        sequencer._resync_all_at_beat(beat)

        # 3. Ajustement du défilement de la grille
        self.scroll_to_beat(beat)

    def go_to_start(self) -> None:
        """Déplace au tout début (Mesure 1, Temps 1)"""
        self.move_to_beat(0)

    def go_to_last_measure_start(self) -> None:
        """Définit la fin du morceau au début de la dernière mesure."""
        total_beats = self.total_beats
        # On récupère le numérateur de la signature temporelle (défaut 4)
        beats_per_measure: copy.Any | int = getattr(self.sequencer_layout.sequencer.song, 'time_signature_numerator', 4)
        
        if total_beats <= 0:
            target_beat = 0
        else:
            # Calcul du premier temps de la dernière mesure entamée
            last_measure_index = (total_beats - 1) // beats_per_measure
            target_beat = last_measure_index * beats_per_measure

        new_pos_str = self.sequencer_layout.sequencer._format_beats_to_position(target_beat)

        # --- FIX: Update End field instead of Start field ---
        self.sequencer_layout.sequencer.ui_end_pos_str = new_pos_str
        self.sequencer_layout.end_pos_input.text = new_pos_str
        self.sequencer_layout.end_pos_manual_override = True

        # Visual feedback: seek playhead
        self.sequencer_layout.sequencer._resync_all_at_beat(target_beat)
        self.scroll_to_beat(target_beat)

    def scroll_to_beat(self, beat) -> None:
        # ... (votre fonction actuelle reste inchangée) ...
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

    def toggle_playback(self):
        sequencer = self.sequencer_layout.sequencer
        # On vérifie l'état actuel (si c'est 'playing', on met en pause et vice versa)
        if sequencer.playback_state == 'playing':
            sequencer.pause() # Ou la méthode qui met playback_state à 'stopped'
        else:
            # 1. On force le seek d'abord
            sequencer._resync_all_at_beat(sequencer.current_beat)
            # 2. On laisse un micro-délai pour que les buffers se remplissent
            # avant de lancer réellement le moteur JACK
            Clock.schedule_once(lambda dt: sequencer.play(), 0.05)
                        
            # Avant de lire, on s'assure d'être au bon beat
#            sequencer._resync_all_at_beat(sequencer.current_beat)
 #           sequencer.play() # Ou la méthode qui met playback_state à 'playing'

    def _apply_state(self, state) -> None:
        """Applies a given state (events and selection) to the editor."""
        # Reconstruct the events and notes from the snapshot.
        new_events = []
        for event_data in state['events']:
            new_notes: list[Note] = [Note(**note_data) for note_data in event_data['notes']]
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

    def _copy_selection(self, is_cut=False) -> None:
        """Copie les notes sélectionnées en calculant leur position relative."""
        if not self.selected_notes:
            return

        self.clipboard_data = []
        
        # 1. On associe chaque note sélectionnée à son temps de départ
        selected_with_times = []
        for event in self.track_copy.events:
            for note in event.notes:
                # On utilise 'is' pour comparer l'instance exacte de la note
                if any(note is sn for sn in self.selected_notes):
                    selected_with_times.append((event.start_time, note))
        
        if not selected_with_times:
            return

        # 2. On trouve le temps de départ le plus tôt pour calculer les offsets
        earliest_start = min(t for t, n in selected_with_times)

        # 3. On remplit le presse-papier avec des données sérialisables
        for start_time, note in selected_with_times:
            self.clipboard_data.append({
                'offset': start_time - earliest_start,
                'pitch': note.pitch,
                'velocity': note.velocity,
                'duration': note.duration
            })

        if is_cut:
            self._delete_selected_notes()
            self._record_state()
            self.ids.grid_viewer.grid.draw()

    def _paste_selection(self) -> None:
        """Colle les notes à la position de la tête de lecture sans doublons."""
        if not self.clipboard_data:
            return

        # Position cible : la tête de lecture (playhead)
        target_beat = self.sequencer_layout.sequencer.current_beat
        
        new_selection = []
        grid = self.ids.grid_viewer.grid
        for item in self.clipboard_data:
            paste_time = target_beat + item['offset']
            new_pitch = item['pitch']
            
            # 1. Trouver ou créer l'événement à ce temps
            event = grid._find_event_at(self.track_copy, paste_time)
            
            if event:
                # VERIFICATION : Si une note de même pitch existe déjà ici, 
                # on ne la colle pas (ou on peut choisir de la remplacer)
                if any(n.pitch == new_pitch for n in event.notes):
                    continue  # Saute cette note pour éviter le doublon
                
                new_note = Note(
                    pitch=new_pitch, 
                    velocity=item['velocity'], 
                    duration=item['duration']
                )
                event.notes.append(new_note)
            else:
                new_note = Note(
                    pitch=new_pitch, 
                    velocity=item['velocity'], 
                    duration=item['duration']
                )
                new_event = Event(start_time=paste_time, notes=[new_note])
                self.track_copy.add_event(new_event)
            
            new_selection.append(new_note)

        # 2. Mettre à jour la sélection
        if new_selection:
            self.selected_notes = new_selection
            self.is_dirty = True
            self._record_state()
            self.ids.grid_viewer.grid.draw()

    def _select_all_notes(self) -> None:
        """Sélectionne toutes les notes présentes dans la piste actuelle."""
        all_notes = []
        for event in self.track_copy.events:
            all_notes.extend(event.notes)
        
        if all_notes:
            self.selected_notes = all_notes
            self.ids.grid_viewer.grid.draw()
     
    def _delete_selected_notes(self) -> None:
        """Supprime proprement toutes les notes sélectionnées."""
        if not self.selected_notes:
            return
            
        selected_ids = {id(n) for n in self.selected_notes}
        for event in list(self.track_copy.events):
            event.notes = [n for n in event.notes if id(n) not in selected_ids]
            
            if not event.notes and not event.cc_messages:
                self.track_copy.events.remove(event)
        
        self.selected_notes = []
        self.is_dirty = True
        
    def _update_undo_redo_buttons_state(self) -> None:
        """Enables/disables the undo/redo buttons based on history."""
        self.ids.undo_button.disabled = not self.history.can_undo()
        self.ids.redo_button.disabled = not self.history.can_redo()

    def _record_state(self) -> None:
        """Records the current state of the track (events and selection) for undo/redo."""
        # Performance Optimization: Build events_snapshot and selection_ids in a single pass
        events_snapshot = []
        selection_ids = []
        selected_ids = {id(n) for n in self.selected_notes}

        for event_idx, event in enumerate(self.track_copy.events):
            notes_snapshot = []
            for note_idx, note in enumerate(event.notes):
                notes_snapshot.append({
                    'pitch': note.pitch,
                    'velocity': note.velocity,
                    'duration': note.duration
                })
                if id(note) in selected_ids:
                    selection_ids.append((event_idx, note_idx))

            events_snapshot.append({
                'start_time': event.start_time,
                'notes': notes_snapshot
            })

        state = {
            'events': events_snapshot,
            'selection': selection_ids
        }
        self.history.record_state(state)
        self._update_undo_redo_buttons_state()

    def _update_legacy_selection(self, *args) -> None:
        if self.selected_notes:
            self.selected_note = self.selected_notes[0]
        else:
            self.selected_note = None
            self.selected_event = None

    def _pitch_to_note_name(self, pitch) -> str:
        """Converts a MIDI pitch number to its note name (e.g., 60 -> C4)."""
        if not (0 <= pitch <= 127):
            return ""
        note_names: list[str] = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        note = note_names[pitch % 12]
        octave = (pitch // 12) - 1
        return f"{note}{octave}"

    def _on_mouse_pos(self, instance, pos) -> None:
        if not self.ids:
            return
        grid_viewer = self.ids.get('grid_viewer')
        piano_keyboard = self.ids.get('piano_keyboard')
        status_label = self.ids.get('status_label')

        if not all([grid_viewer, piano_keyboard, status_label]) or not grid_viewer.parent:
            return

        # --- Performance Optimization ---
        # Disable heavy hover calculations during playback
        if self.sequencer_layout.sequencer.playback_state in ('playing', 'recording'):
            # Reset to a clean state and exit
            Window.set_system_cursor('arrow')
            piano_keyboard.highlighted_note = -1
            status_label.text = ""
            return

        # 1. On récupère la position relative au contenu de la grille
        # grid_viewer.grid est le PianoRoll qui contient les notes
        grid_content = grid_viewer.grid
        
        # Transformation des coordonnées Fenêtre -> Widget interne
        # to_widget(pos) sur le contenu du scrollview est la méthode la plus fiable
        lx, ly = grid_content.to_widget(*pos)

        # 2. On vérifie si la souris est dans la zone visible du ScrollView
        # On transforme les coordonnées fenêtre en coordonnées locales au parent du ScrollView
        if grid_viewer.collide_point(*grid_viewer.parent.to_widget(*pos)):
            
            # CALCULS (Pitch et Temps)
            # Note: on utilise int(ly / self.note_height)
            pitch = int(ly / self.note_height)
            current_beat = lx / self.pixels_per_beat
            
            if 0 <= pitch <= 127:
                # Allume la touche sur le clavier à gauche
                piano_keyboard.highlighted_note = pitch
                
                # Nom de la note (C4, D#2, etc.)
                note_name: str = self._pitch_to_note_name(pitch)
                
                # --- RECHERCHE DE LA NOTE SOUS LE CURSEUR ---
                # Performance Optimization: Use binary search to find relevant events
                found_note = None
                if self.track_copy.events:
                    # Search for events starting at or before current_beat
                    idx = bisect.bisect_right(self.track_copy.events, current_beat, key=lambda e: e.start_time) - 1

                    # Check events back to handle overlapping notes/events (up to 32 beats)
                    for i in range(idx, -1, -1):
                        event = self.track_copy.events[i]
                        if event.start_time < current_beat - 32:
                            break

                        for note in event.notes:
                            if note.pitch == pitch:
                                if event.start_time <= current_beat <= (event.start_time + note.duration):
                                    found_note = note
                                    break
                        if found_note:
                            break

                # On mémorise l'objet note pour les raccourcis clavier (+/-)
                self.hovered_note = found_note 
                
                # --- MISE À JOUR DU TEXTE ---
                if found_note:
                    status_label.text = f"Note: {note_name} | Velocity: {found_note.velocity}"
                else:
                    status_label.text = f"Note: {note_name}"
                
                # Curseur
                self._set_editor_cursor()
            else:
                piano_keyboard.highlighted_note = -1
                status_label.text = ""
        else:
            # Hors de la grille
            Window.set_system_cursor('arrow')
            piano_keyboard.highlighted_note = -1
            status_label.text = ""

    def _set_editor_cursor(self) -> None:
        """Gère l'apparence du curseur selon le mode d'édition"""
        mode: copy.Any | str = getattr(self, 'edit_mode', 'select')
        if mode == 'insert': Window.set_system_cursor('crosshair')
        elif mode == 'delete': Window.set_system_cursor('no')
        elif mode == 'move': Window.set_system_cursor('hand')
        else: Window.set_system_cursor('arrow')

    def on_dismiss(self) -> None:
        # --- Cleanup ---
        # Unbind all global window events to prevent memory leaks
        Window.unbind(on_key_down=self._on_key_down)
        Window.unbind(mouse_pos=self._on_mouse_pos)

        # Reset the cursor to default one last time to be safe
        Window.set_system_cursor('arrow')

        self.sequencer_layout.sequencer.unbind(playback_state=self.on_playback_state_change)
        if self._update_event:
            self._update_event.cancel()
        # Ensure the override is removed when the editor is closed
        if self.original_track_index in self.sequencer_layout.sequencer.track_overrides:
            del self.sequencer_layout.sequencer.track_overrides[self.original_track_index]


    def dismiss(self, action=None, *args) -> None:
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

    def _handle_save_dialog(self, answer) -> None:
        if answer == 's':
            self._save_changes()
            super(PianoRollEditor, self).dismiss()
        elif answer == 'd':
            super(PianoRollEditor, self).dismiss()

    def _save_changes(self) -> None:
            # 1. Appliquer les changements (On utilise deepcopy pour éviter les références partagées)
            import copy
            self.track.events = copy.deepcopy(self.track_copy.events)
            
            self.is_dirty = False
            
            # 2. Rafraîchissement VISUEL de la fenêtre principale
            # On cherche le widget de la piste dans la liste des widgets du séquenceur
            if self.sequencer_layout and hasattr(self.sequencer_layout, 'track_widgets'):
                for tw in self.sequencer_layout.track_widgets:
                    if tw.track == self.track:
                        # On parcourt les enfants du TrackWidget pour trouver le PianoRoll
                        # Dans votre structure, il est dans timeline_container
                        for child in tw.walk():
                            if child.__class__.__name__ == 'PianoRoll':
                                # On appelle la méthode de dessin du PianoRoll de la fenêtre principale
                                child.draw()
                        break

            # 3. Rafraîchissement de la LECTURE (Moteur MIDI)
            # On force le séquenceur à recharger les événements de cette piste
            if self.sequencer_layout.sequencer:
                seq = self.sequencer_layout.sequencer
                
                # Si vous avez une méthode dédiée dans votre séquenceur :
                if hasattr(seq, 'update_track_events'):
                    seq.update_track_events(self.track_index)
                else:
                    # Sinon, on déclenche souvent une reconstruction de la timeline 
                    # en réassignant ou en appelant la méthode qui prépare les messages
                    # Exemple si vous utilisez un moteur basé sur des messages pré-calculés :
                    if hasattr(seq, '_prepare_track_messages'):
                        seq._prepare_track_messages(self.track_index)
                
                # Note: Si la lecture est en cours, certains moteurs demandent 
                # un stop/start pour prendre en compte les gros changements.

    def update_playhead(self, dt) -> None:
        current_state = self.sequencer_layout.sequencer.playback_state

        # --- 1. POSITION JACK & SMOOTHING ---
        jack_beat = self.sequencer_layout.sequencer.current_beat
        if current_state in ("playing", "recording"):
            safe_dt = min(dt, 1/15.0)
            beats_per_second = self.sequencer_layout.sequencer.song.tempo / 60.0
            if beats_per_second > 0:
                self.display_beat += (beats_per_second * safe_dt)
            error = jack_beat - self.display_beat
            correction_speed = 5.0
            if abs(error) > 0.5 or dt > 0.1: self.display_beat = jack_beat
            else: self.display_beat += (error * correction_speed * dt)

            # Auto-scroll uniquement en lecture
            self._scroll_to_logic(self.display_beat)
        else:
            self.display_beat = jack_beat

        # --- 2. MISE À JOUR VISUELLE ---
        self.set_playback_position(self.display_beat)
        pos_str = self.sequencer_layout.sequencer._format_beats_to_position(self.display_beat)
        self.ids.pos_label.text = f"Pos: {pos_str}"

        self.last_playback_state = current_state

    def _scroll_to_logic(self, current_beat):
        scroll_view = self.ids.timeline_scroll
        grid = self.ids.grid_viewer.grid
        ppb = self.pixels_per_beat
        total_width = grid.width
        viewport_width = scroll_view.width

        max_scroll_dist = total_width - viewport_width
        if max_scroll_dist <= 0:
            return

        playhead_pixel_x = current_beat * ppb
        trigger_point = viewport_width * 0.5

        if playhead_pixel_x > trigger_point:
            target_view_start = playhead_pixel_x - trigger_point
            new_scroll_x = target_view_start / max_scroll_dist
            scroll_view.scroll_x = max(0, min(1, new_scroll_x))

    def set_playback_position(self, current_beat: float) -> None:
        grid = self.ids.grid_viewer.grid
        x_pos = current_beat * self.pixels_per_beat
        grid.set_playback_line_x(x_pos)

    def play_pressed(self, *args) -> None: self.sequencer_layout.sequencer.process_transport_command("play")
    def pause_pressed(self, *args) -> None: self.sequencer_layout.sequencer.process_transport_command("pause")
    def stop_pressed(self, *args) -> None: self.sequencer_layout.sequencer.process_transport_command("stop")
    def record_pressed(self, *args) -> None: self.sequencer_layout.sequencer.process_transport_command("record")
    def rewind_pressed(self, *args) -> None: self.sequencer_layout.sequencer._resync_all_at_beat(0)

    def on_playback_state_change(self, instance, state) -> None:
        play_btn = self.ids.play_button
        pause_btn = self.ids.pause_button
        record_btn = self.ids.record_button

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

        record_btn.icon_color = [1, 0.2, 0.2, 1] if state == 'recording' else [0.8, 0.8, 0.8, 1]
        record_btn.md_bg_color = [0.5, 0.1, 0.1, 1] if state == 'recording' else [1, 1, 1, 0.05]

        # --- Live Preview Logic ---
        sequencer = self.sequencer_layout.sequencer
        if state in ('playing', 'recording'):
            # When playback starts, apply the edited track as an override
            sequencer.track_overrides[self.original_track_index] = self.track_copy
        else:
            # When playback stops, remove the override
            if self.original_track_index in sequencer.track_overrides:
                del sequencer.track_overrides[self.original_track_index]

    def set_edit_mode(self, mode, btn) -> None:
        self.edit_mode = mode
        self._update_button_states(self.mode_buttons, btn)

        # Trigger a cursor update in case the mouse is already over the grid
        self._on_mouse_pos(None, Window.mouse_pos)

        # If switching away from the selection-enabled mode, clear selection
        if mode != 'move':
            if self.selected_notes:
                self.selected_notes = []
                self.ids.grid_viewer.grid.draw()

    def set_note_duration(self, dur, btn) -> None:
        self.base_note_duration = dur
        self._update_note_duration()
        self._update_button_states(self.duration_buttons, btn)

    def toggle_dotted_mode(self) -> None:
        self.dotted_mode: bool = not self.dotted_mode
        self._update_note_duration()

        # Update button appearance
        dotted_button = self.ids.dotted_button
        if self.dotted_mode:
            dotted_button.md_bg_color = [0.9, 0.7, 0, 1] # Active color
            dotted_button.icon_color = [0.1, 0.1, 0.1, 1]
        else:
            dotted_button.md_bg_color = [0.2, 0.2, 0.2, 1] # Inactive color
            dotted_button.icon_color = [0.8, 0.8, 0.8, 1]

    def _update_note_duration(self) -> None:
        """Calculates the final note duration and applies it to all selected notes."""
        multiplier: float = 1.5 if self.dotted_mode else 1.0
        new_duration = self.base_note_duration * multiplier
        self.note_duration = new_duration

        if self.selected_notes:
            for note in self.selected_notes:
                note.duration = new_duration
            self.is_dirty = True
            self.ids.grid_viewer.grid.draw()

    def _update_button_states(self, group, active_btn) -> None:
        """Met à jour l'apparence des boutons d'outils selon l'outil sélectionné."""
        orange_vif = [1, 0.6, 0, 1]
        blanc_semi = [1, 1, 1, 0.8]

        for btn in group.values():
            if btn == active_btn:
                # On force la couleur orange
                btn.icon_color = orange_vif
                # Optionnel : On peut aussi augmenter l'opacité pour plus de peps
                btn.opacity = 1.0
            else:
                # On remet en blanc semi-transparent
                btn.icon_color = blanc_semi
                btn.opacity = 0.8
                
            btn.canvas.ask_update()                

    def sync_horizontal_scroll(self, source_scroll_view, scroll_x_value) -> None:
        if self._is_scrolling: return
        self._is_scrolling = True

        try:
            # Calculate absolute pixel offset from source
            content_width_source = source_scroll_view.children[0].width
            viewport_width_source = source_scroll_view.width
            max_scroll_source = max(0, content_width_source - viewport_width_source)
            pixel_offset = scroll_x_value * max_scroll_source if max_scroll_source > 0 else 0

            ruler_scroll = self.ids.ruler.scroll_view
            timeline_scroll = self.ids.timeline_scroll

            targets = [ruler_scroll, timeline_scroll]
            for sv in targets:
                if sv is not source_scroll_view:
                    try:
                        content_width = sv.children[0].width
                        viewport_width = sv.width
                        max_scroll = max(0, content_width - viewport_width)
                        if max_scroll > 0:
                            sv.scroll_x = max(0.0, min(1.0, pixel_offset / max_scroll))
                        else:
                            sv.scroll_x = 0
                    except (IndexError, AttributeError):
                        continue
        except (IndexError, AttributeError):
            pass

        self._is_scrolling = False

    def _center_view_on_c4(self) -> None:
        grid_viewer = self.ids.grid_viewer
        max_scroll = (128 * self.note_height) - grid_viewer.height
        if max_scroll > 0:
            grid_viewer.scroll_y = max(0.0, min(1.0, ((60 * self.note_height) - (self.height / 2)) / max_scroll))

    def zoom_in(self) -> None:
        self._apply_zoom(self.pixels_per_beat * 1.25)

    def zoom_out(self) -> None:
        # On limite le dézoom pour ne pas avoir une grille minuscule
        new_zoom: float = max(dp(20), self.pixels_per_beat / 1.25)
        self._apply_zoom(new_zoom)

    def zoom_reset(self) -> None:
        self._apply_zoom(dp(100))

    def _apply_zoom(self, new_pixels_per_beat) -> None:
        """Applique le zoom en tentant de conserver le centre de la vue."""
        scroll_view = self.ids.timeline_scroll
        
        # 1. Calculer le beat qui est actuellement au centre de l'écran
        # Largeur totale actuelle
        old_total_width = self.total_beats * self.pixels_per_beat
        viewport_width = scroll_view.width
        
        # Position du centre en pixels
        center_pixel = (scroll_view.scroll_x * (old_total_width - viewport_width)) + (viewport_width / 2)
        center_beat = center_pixel / self.pixels_per_beat

        # 2. Appliquer le nouveau zoom
        self.pixels_per_beat = new_pixels_per_beat
        
        # 3. Recalculer le scroll_x pour que le center_beat reste au centre
        # On doit attendre que Kivy mette à jour le layout au prochain frame
        Clock.schedule_once(lambda dt: self._update_scroll_after_zoom(center_beat), 0)

    def _update_scroll_after_zoom(self, target_beat) -> None:
        scroll_view = self.ids.timeline_scroll
        new_total_width = self.total_beats * self.pixels_per_beat
        viewport_width = scroll_view.width
        
        if new_total_width <= viewport_width:
            scroll_view.scroll_x = 0
            return

        # Nouvelle position du beat cible en pixels
        new_center_pixel = target_beat * self.pixels_per_beat
        new_scroll_pixels = new_center_pixel - (viewport_width / 2)
        
        # Normalisation du scroll_x (entre 0 et 1)
        max_scroll = new_total_width - viewport_width
        self.ids.timeline_scroll.scroll_x = max(0, min(1, new_scroll_pixels / max_scroll))
        
        # Forcer le redessin de la règle et de la grille
        if hasattr(self.ids.ruler, 'redraw'):
            self.ids.ruler.redraw()
        
        self.ids.grid_viewer.grid.draw()

    def _preview_note(self, pitch, velocity, duration) -> None:
        """Plays a single note through the sequencer's MIDI output for preview."""
        sequencer = self.sequencer_layout.sequencer
        if not sequencer or not sequencer.jack_manager.is_running:
            return

        port_name = self.track_copy.output_port_name
        if not port_name or port_name not in sequencer.jack_manager.open_ports:
            return

        port = sequencer.jack_manager.open_ports[port_name]
        channel = self.track_copy.channel

        try:
            note_on_msg = mido.Message('note_on', channel=channel, note=pitch, velocity=velocity)
            note_off_msg = mido.Message('note_off', channel=channel, note=pitch, velocity=velocity)

            sequencer.jack_manager._send_midi(port_name, note_on_msg)
            Clock.schedule_once(lambda dt: sequencer.jack_manager._send_midi(port_name, note_off_msg), duration)
        except Exception as e:
            print(f"Error sending preview note: {e}")