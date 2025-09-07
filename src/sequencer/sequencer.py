from .midi_export import export_to_midi
from .midi_import import import_song
from .models import AnyTrack, AudioTrack, Event, MidiTrack, Note, Song
from copy import deepcopy
from dataclasses import dataclass, asdict, is_dataclass, fields
import json
import math
import mido
from mido import get_input_names, get_output_names, open_output # type: ignore
from pydub import AudioSegment
import os
import signal
import sys
import subprocess
import tempfile
import socket
import threading
import time
from typing import List, Optional


@dataclass
class ActiveAudioProcess:
    process: subprocess.Popen
    socket_path: str
    track_index: int
    temp_filepath: Optional[str] = None # No longer mixing to a temp file


class CustomSongEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (Song, MidiTrack, AudioTrack, Event, Note)):
            d = {f.name: getattr(o, f.name) for f in fields(o)}
            d['__type__'] = o.__class__.__name__
            return d
        return super().default(o)

def song_decoder(d):
    if '__type__' in d:
        type_name = d.pop('__type__')
        # Map the type name to the actual class.
        # The values in 'd' have already been decoded into objects by the hook.
        if type_name == 'Song':
            return Song(**d)
        elif type_name == 'MidiTrack':
            return MidiTrack(**d)
        elif type_name == 'AudioTrack':
            return AudioTrack(**d)
        elif type_name == 'Event':
            return Event(**d)
        elif type_name == 'Note':
            return Note(**d)
    return d


class Sequencer:
    DEFAULT_AUDIO_PLAYER_COMMAND = "mpv --really-quiet --no-video --idle"

    def __init__(self, tempo: int = 120):
        self.song = Song(name="New Song", tempo=tempo)
        self.playback_state = "stopped"
        self.playback_thread = None
        self.metronome_thread = None # Nouveau thread pour le métronome
        self.open_ports = {}
        self.virtual_ports = []
        self.temporary_ports = []
        self._stop_event = threading.Event()
        self._run_event = threading.Event()
        self._run_event.set()
        self.audio_threads: List[threading.Thread] = []
        self.active_audio_processes: List[ActiveAudioProcess] = []
        self.process_lock = threading.Lock()
        self.audio_player_command: str = self.DEFAULT_AUDIO_PLAYER_COMMAND

        self.total_paused_time = 0.0
        self.pause_start_time = 0.0
        self.last_start_beat = 0.0
        self.recording_thread = None
        self.is_recording = False

        # Metronome settings
        self.metronome_channel = 9  # Channel 10 (0-indexed)
        self.metronome_pitch_downbeat = 76  # High Wood Block
        self.metronome_pitch_beat = 77  # Low Wood Block

        self.last_record_settings = None

    def _all_notes_off(self):
        for port in self.open_ports.values():
            if port and not port.closed:
                for channel in range(16):
                    port.send(mido.Message('control_change', channel=channel, control=123, value=0))
        print("Sent all notes off to all open ports.")

    def parse_position_to_beats(self, position_str: str, default: str = "1:1") -> Optional[float]:
        """Parses a 'measure:beat' string into a float representing the absolute beat count."""
        if not position_str:
            position_str = default

        try:
            parts = position_str.split(':')
            if len(parts) > 2:
                print("Error: Invalid format. Please use 'measure:beat' or 'measure'.")
                return None

            measure = int(parts[0])
            beat = int(parts[1]) if len(parts) == 2 else 1

            beats_per_measure = self.song.time_signature_numerator

            if not 1 <= beat <= beats_per_measure:
                print(f"Error: Beat number {beat} is out of range for the current time signature ({beats_per_measure}/...). It must be between 1 and {beats_per_measure}.")
                return None

            if measure < 1:
                print("Error: Measure number must be 1 or greater.")
                return None

            # Return total beats from the start (0-indexed)
            return (measure - 1) * beats_per_measure + (beat - 1)

        except (ValueError, IndexError):
            print("Error: Invalid format. Please enter numbers in 'measure:beat' format.")
            return None

    def _format_beats_to_position(self, beats: float) -> str:
        """Converts an absolute beat count into a 'measure:beat' string."""
        if beats is None:
            return ""
        beats_per_measure = self.song.time_signature_numerator
        if beats_per_measure == 0:
            return "1:1"  # Avoid division by zero, return a sensible default

        measure = int(beats / beats_per_measure) + 1
        beat = int(beats % beats_per_measure) + 1
        return f"{measure}:{beat}"

    def set_tempo(self, tempo: int):
        if tempo <= 0:
            raise ValueError("Tempo must be positive.")
        self.song.tempo = tempo
        print(f"Tempo set to {self.song.tempo} BPM.")

    def set_time_signature(self, numerator: int, denominator: int):
        # Basic validation
        if not (numerator > 0 and denominator > 0 and (denominator & (denominator - 1) == 0)):
            print("Error: Invalid time signature. Denominator must be a power of 2.")
            return
        self.song.time_signature_numerator = numerator
        self.song.time_signature_denominator = denominator
        print(f"Time signature set to {numerator}/{denominator}.")

    def add_track(self, name: str, track_type: str = 'midi', instrument: int = 0, filepath: Optional[str] = None):
        """Adds a new track to the song."""
        if track_type == 'midi':
            track = MidiTrack(name=name, instrument=instrument)
            print(f"MIDI track '{name}' added.")
        elif track_type == 'audio':
            if not filepath:
                print("Error: Filepath is required for audio tracks.")
                return
            try:
                # Pre-load the audio file to check for errors early
                AudioSegment.from_file(filepath)
            except FileNotFoundError:
                print(f"Error: Audio file not found at '{filepath}'")
                return
            except Exception as e:
                print(f"Error opening audio file: {e}")
                return
            track = AudioTrack(name=name, filepath=filepath)
            print(f"Audio track '{name}' added with file '{filepath}'.")
        else:
            print(f"Error: Unknown track type '{track_type}'. Must be 'midi' or 'audio'.")
            return

        self.song.add_track(track)

    def delete_track(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return False
        track_name = self.song.tracks[track_index].name
        self.song.tracks.pop(track_index)
        print(f"Track '{track_name}' deleted.")
        return True

    def erase_track(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            print("Error: Erasing events is only supported for MIDI tracks.")
            return

        # --- Get range from user ---
        try:
            start_pos_str = input(f"Erase from position on track '{track.name}' (measure:beat) [default: 1:1]: ").strip()
            start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if start_beat is None: return

            end_pos_str = input(f"Erase up to position on track '{track.name}' (measure:beat) [default: end of track]: ").strip()
            if end_pos_str == "":
                end_beat = float('inf')
            else:
                end_beat = self.parse_position_to_beats(end_pos_str)
                if end_beat is None: return

            if end_beat <= start_beat:
                print("Error: End position must be after the start position.")
                return

        except ValueError:
            print("Error: Invalid number format.")
            return

        # --- Confirmation ---
        end_str_display = f"up to {end_pos_str}" if end_pos_str else "to the end of the track"
        confirm_message = f"Erase events from {start_pos_str} {end_str_display} on track '{track.name}'? [y/N] "
        if input(confirm_message).lower() != 'y':
            print("Erase cancelled.")
            return

        # --- Partition events ---
        events_to_keep = []
        events_to_shift = []
        removed_count = 0

        for event in track.events:
            if event.start_time < start_beat:
                events_to_keep.append(event)
            elif event.start_time >= end_beat:
                events_to_shift.append(event)
            else: # event.start_time >= start_beat and event.start_time < end_beat
                removed_count += 1

        # --- Handle shifting ---
        final_events = events_to_keep
        shift_confirmed = False
        if events_to_shift:
            shift_choice = input(f"Shift subsequent {len(events_to_shift)} event(s) to start after the erased section? [y/N]: ").lower()
            if shift_choice == 'y':
                # This offset calculation correctly "closes the gap" by moving the subsequent events back.
                shift_offset = end_beat - start_beat
                for event in events_to_shift:
                    event.start_time -= shift_offset
                shift_confirmed = True

        final_events.extend(events_to_shift)

        # --- Execution ---
        track.events = final_events
        track.events.sort(key=lambda e: e.start_time) # Keep it sorted

        # --- Report results ---
        report = [f"Erased {removed_count} event(s)"]
        if shift_confirmed:
            report.append(f"shifted {len(events_to_shift)} event(s)")

        if removed_count > 0 or shift_confirmed:
             print(f"Operation complete: {', '.join(report)} from track '{track.name}'.")
        else:
             print("No events were erased or shifted.")

    def rename_track(self, track_index: int, new_name: str):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        old_name = self.song.tracks[track_index].name
        self.song.tracks[track_index].name = new_name
        print(f"Track '{old_name}' renamed to '{new_name}'.")

    def move_track_section(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid source track index.")
            return
        source_track = self.song.tracks[track_index]
        if not isinstance(source_track, MidiTrack):
            print("Error: Moving events is only supported for MIDI tracks.")
            return

        try:
            # Get source range
            start_pos_str = input(f"Move from position on track '{source_track.name}' (measure:beat) [default: 1:1]: ").strip()
            source_start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if source_start_beat is None: return

            end_pos_str = input(f"Move up to position on track '{source_track.name}' (measure:beat): ").strip()
            source_end_beat = self.parse_position_to_beats(end_pos_str)
            if source_end_beat is None: return

            if source_end_beat <= source_start_beat:
                print("Error: End position must be after the start position.")
                return

            # Get destination
            dest_track_idx_str = input(f"Move to destination track index (default: {track_index}, '{source_track.name}'): ").strip()
            dest_track_idx = track_index if dest_track_idx_str == "" else int(dest_track_idx_str)

            if not 0 <= dest_track_idx < len(self.song.tracks):
                print("Error: Invalid destination track index.")
                return

            dest_track = self.song.tracks[dest_track_idx]
            if not isinstance(dest_track, MidiTrack):
                print("Error: Destination track must be a MIDI track.")
                return

            dest_pos_str = input(f"Move to destination position on track '{dest_track.name}' (measure:beat) [default: 1:1]: ").strip()
            destination_start_beat = self.parse_position_to_beats(dest_pos_str, default="1:1")
            if destination_start_beat is None: return

        except ValueError:
            print("Error: Invalid number in track index.")
            return

        # --- Calculations ---
        range_duration_beats = source_end_beat - source_start_beat
        destination_end_beat = destination_start_beat + range_duration_beats
        offset_beats = destination_start_beat - source_start_beat

        # Confirmation
        confirm_message = (
            f"Move events from {start_pos_str} to {end_pos_str} on track '{source_track.name}' "
            f"to start at {dest_pos_str} on track '{dest_track.name}'. Are you sure? [y/N] "
        )
        if input(confirm_message).lower() != 'y':
            print("Move cancelled.")
            return

        # --- Check for notes at destination ---
        events_at_destination = [
            event for event in dest_track.events if destination_start_beat <= event.start_time < destination_end_beat
        ]
        if source_track == dest_track:
            events_at_destination = [e for e in events_at_destination if not (source_start_beat <= e.start_time < source_end_beat)]

        overwrite_mode = "add"
        if events_at_destination:
            print("There are existing notes at the destination.")
            while True:
                choice = input("Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
                if choice in ['r', 'replace', 'a', 'add']:
                    overwrite_mode = choice[0]
                    break
                else:
                    print("Invalid choice. Please enter 'r' or 'a'.")

        # --- Partition and process events ---
        events_to_move = []
        remaining_source_events = []
        for event in source_track.events:
            if source_start_beat <= event.start_time < source_end_beat:
                events_to_move.append(event)
            else:
                remaining_source_events.append(event)

        deleted_event_count = 0
        if overwrite_mode == 'r':
            # This list will hold the events that are NOT in the destination range
            final_dest_events = []
            # When moving within the same track, we must not remove the events that are being moved.
            # So, we iterate over the original list of events of the destination track.
            for event in dest_track.events:
                # If the event is not in the destination range, we keep it.
                if not (destination_start_beat <= event.start_time < destination_end_beat):
                    final_dest_events.append(event)
                else:
                    # If the event IS in the destination range, we must check if it's also in the source range
                    # (only relevant if source_track == dest_track)
                    if source_track == dest_track and source_start_beat <= event.start_time < source_end_beat:
                        # This event is being moved, so we keep it for now.
                        # It will be processed and moved later.
                        final_dest_events.append(event)
                    else:
                        # This event is in the destination and is NOT being moved, so it gets deleted.
                        deleted_event_count += 1
            dest_track.events = final_dest_events


        # Update source track events list only if the move is to a different track
        if source_track != dest_track:
            source_track.events = remaining_source_events

        # Move the selected events
        moved_event_count = 0
        for event in events_to_move:
            event.start_time += offset_beats
            if event.start_time < 0:
                print(f"Warning: Moving event would result in a negative start time ({event.start_time:.2f} beats). Skipping and keeping original.")
                source_track.add_event(event) # Add it back to source if it's an invalid move
                continue

            # If moving to a different track, add the event object to the new track
            if source_track != dest_track:
                dest_track.add_event(event)

            moved_event_count += 1

        # Sort the events for both tracks to ensure correct playback order
        source_track.events.sort(key=lambda e: e.start_time)
        dest_track.events.sort(key=lambda e: e.start_time)

        # --- Report results ---
        report = []
        if moved_event_count > 0:
            report.append(f"Moved {moved_event_count} event(s) from '{source_track.name}' to '{dest_track.name}'")
        if deleted_event_count > 0:
            report.append(f"deleted {deleted_event_count} event(s) at destination")

        if not report:
            print("No notes were found in the source range to move.")
        else:
            print(f"Operation complete: {', '.join(report)}.")

    def copy_track_section(self):
        if not self.song.tracks:
            print("No tracks to copy from.")
            return

        try:
            # Get source track
            source_track_idx = int(input("Copy from track index: ").strip())
            if not 0 <= source_track_idx < len(self.song.tracks):
                print("Error: Invalid source track index.")
                return
            source_track = self.song.tracks[source_track_idx]
            if not isinstance(source_track, MidiTrack):
                print("Error: Copying events is only supported for MIDI tracks.")
                return

            # Get source range
            start_pos_str = input(f"Copy from position on track '{source_track.name}' (measure:beat) [default: 1:1]: ").strip()
            source_start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if source_start_beat is None: return

            end_pos_str = input(f"Copy up to position on track '{source_track.name}' (measure:beat): ").strip()
            source_end_beat = self.parse_position_to_beats(end_pos_str)
            if source_end_beat is None: return

            if source_end_beat <= source_start_beat:
                print("Error: End position must be after the start position.")
                return

            # Get destination
            dest_track_idx = int(input(f"Copy to destination track index (default: {source_track_idx}): ").strip() or str(source_track_idx))
            if not 0 <= dest_track_idx < len(self.song.tracks):
                print("Error: Invalid destination track index.")
                return
            dest_track = self.song.tracks[dest_track_idx]
            if not isinstance(dest_track, MidiTrack):
                print("Error: Destination track must be a MIDI track.")
                return

            dest_pos_str = input(f"Copy to destination position on track '{dest_track.name}' (measure:beat) [default: 1:1]: ").strip()
            destination_start_beat = self.parse_position_to_beats(dest_pos_str, default="1:1")
            if destination_start_beat is None: return

        except ValueError:
            print("Error: Invalid number.")
            return

        # --- Calculations ---
        range_duration_beats = source_end_beat - source_start_beat
        destination_end_beat = destination_start_beat + range_duration_beats
        offset_beats = destination_start_beat - source_start_beat

        # Confirmation
        confirm_message = (
            f"Copy events from {start_pos_str} to {end_pos_str} on track '{source_track.name}' "
            f"to start at {dest_pos_str} on track '{dest_track.name}'. Are you sure? [y/N] "
        )
        if input(confirm_message).lower() != 'y':
            print("Copy cancelled.")
            return

        # --- Check for notes at destination ---
        events_at_destination = [
            event for event in dest_track.events
            if destination_start_beat <= event.start_time < destination_end_beat
        ]

        overwrite_mode = "add"
        if events_at_destination:
            print("There are existing notes at the destination.")
            while True:
                choice = input("Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
                if choice in ['r', 'replace']:
                    overwrite_mode = "replace"
                    break
                elif choice in ['a', 'add']:
                    overwrite_mode = "add"
                    break
                else:
                    print("Invalid choice. Please enter 'r' or 'a'.")

        # --- Partition and process events ---
        copied_event_count = 0
        deleted_event_count = 0

        # Find events to copy
        source_events_to_copy = [
            event for event in source_track.events
            if source_start_beat <= event.start_time < source_end_beat
        ]

        # If replacing, remove existing events at destination
        if overwrite_mode == "replace":
            initial_dest_event_count = len(dest_track.events)
            dest_track.events = [
                event for event in dest_track.events
                if not (destination_start_beat <= event.start_time < destination_end_beat)
            ]
            deleted_event_count = initial_dest_event_count - len(dest_track.events)

        # Create copies of the source events and add them
        for event in source_events_to_copy:
            new_event = deepcopy(event)
            new_event.start_time += offset_beats
            if new_event.start_time < 0:
                print(f"Warning: Copying event would result in a negative start time ({new_event.start_time:.2f} beats). Skipping event.")
                continue
            dest_track.add_event(new_event)
            copied_event_count += 1

        dest_track.events.sort(key=lambda e: e.start_time)

        # --- Report results ---
        report = []
        if copied_event_count > 0:
            report.append(f"Copied {copied_event_count} event(s)")
        if deleted_event_count > 0:
            report.append(f"deleted {deleted_event_count} event(s) at destination")

        if not report:
            print("No notes were found in the source range to copy.")
        else:
            print(f"Operation complete: {', '.join(report)}.")

    def transpose_track_section(self):
        if not self.song.tracks:
            print("No tracks to transpose.")
            return

        try:
            track_idx = int(input("Transpose track index: ").strip())
            if not 0 <= track_idx < len(self.song.tracks):
                print("Error: Invalid track index.")
                return
            track = self.song.tracks[track_idx]
            if not isinstance(track, MidiTrack):
                print("Error: Transposing is only supported for MIDI tracks.")
                return

            start_pos_str = input(f"Transpose from position on track '{track.name}' (measure:beat) [default: 1:1]: ").strip()
            start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if start_beat is None: return

            end_pos_str = input(f"Transpose up to position on track '{track.name}' (measure:beat) [default: end of track]: ").strip()
            if end_pos_str == "":
                end_beat = float('inf')
            else:
                end_beat = self.parse_position_to_beats(end_pos_str)
                if end_beat is None: return

            if end_beat <= start_beat:
                print("Error: End position must be after the start position.")
                return

            transpose_value = int(input("Transpose by how many semitones (e.g., 12 for up, -12 for down): ").strip())
            if not -127 <= transpose_value <= 127:
                print("Error: Transposition value must be between -127 and 127.")
                return

        except ValueError:
            print("Error: Invalid number.")
            return

        # --- Find events to transpose ---
        events_to_transpose = [
            event for event in track.events
            if start_beat <= event.start_time < end_beat
        ]

        if not events_to_transpose:
            print("No notes found in the specified range to transpose.")
            return

        # --- Confirmation ---
        confirm_message = (
            f"Transpose {len(events_to_transpose)} event(s) on track '{track.name}' by {transpose_value} semitones. "
            f"Are you sure? [y/N] "
        )
        if input(confirm_message).lower() != 'y':
            print("Transpose cancelled.")
            return

        # --- Transpose notes ---
        transposed_note_count = 0
        clamped_note_count = 0
        for event in events_to_transpose:
            for note in event.notes:
                original_pitch = note.pitch
                new_pitch = original_pitch + transpose_value

                if not 0 <= new_pitch <= 127:
                    clamped_pitch = max(0, min(127, new_pitch))
                    print(f"Warning: Transposing note {original_pitch} by {transpose_value} results in an out-of-range pitch ({new_pitch}). Clamping to {clamped_pitch}.")
                    note.pitch = clamped_pitch
                    clamped_note_count += 1
                else:
                    note.pitch = new_pitch
                transposed_note_count += 1

        print(f"Transposed {transposed_note_count} note(s) on track '{track.name}'.")
        if clamped_note_count > 0:
            print(f"{clamped_note_count} note(s) were clamped to the valid MIDI pitch range (0-127).")

    def assign_port(self, track_index: int, port_name: str):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            print("Error: Port assignment is currently only supported for MIDI tracks.")
            return
        track.output_port_name = port_name
        print(f"Assigned port '{port_name}' to track '{track.name}'.")

    def unassign_port(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            print("Error: Port un-assignment is currently only supported for MIDI tracks.")
            return

        if track.output_port_name:
            print(f"Un-assigned port from track '{track.name}'.")
            track.output_port_name = None
        else:
            print(f"Track '{track.name}' has no port assigned.")

    def set_bank(self, track_index: int, msb: int, lsb: int = 0):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            print("Error: Bank select is only available for MIDI tracks.")
            return
        if not 0 <= msb <= 127 and 0 <= lsb <= 127:
            print("Error: Bank values (MSB, LSB) must be between 0 and 127.")
            return

        track.bank_msb = msb
        track.bank_lsb = lsb
        print(f"Set bank for track '{track.name}' to MSB={msb}, LSB={lsb}.")

    def set_channel(self, track_index: int, channel: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            print("Error: MIDI channel can only be set for MIDI tracks.")
            return
        if not 1 <= channel <= 16:
            print("Error: MIDI channel must be between 1 and 16.")
            return

        track.channel = channel - 1 # Convert to 0-indexed for mido
        print(f"Set MIDI channel for track '{track.name}' to {channel}.")

    def set_program(self, track_index: int, program: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            print("Error: Program change is only available for MIDI tracks.")
            return
        if not 0 <= program <= 127:
            print("Error: Program number must be between 0 and 127.")
            return

        track.instrument = program
        print(f"Set program for track '{track.name}' to {program + 1}.")

    def set_track_volume(self, track_index: int, volume: float):
        """Sets the volume for a specific audio or MIDI track."""
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        track = self.song.tracks[track_index]
        if not isinstance(track, (AudioTrack, MidiTrack)):
            print("Error: Volume can only be set for audio or MIDI tracks.")
            return

        if not 0.0 <= volume <= 1.0:
            print("Error: Volume must be between 0.0 and 1.0.")
            return

        track.volume = volume
        print(f"Volume for track '{track.name}' set to {volume:.2f}.")

        if isinstance(track, AudioTrack):
            # If playback is active, send a live volume change command
            with self.process_lock:
                for ap in self.active_audio_processes:
                    if ap.track_index == track_index:
                        self._send_ipc_command(
                            ap.socket_path,
                            {"command": ["set_property", "volume", volume * 100]}
                        )
                        break
        elif isinstance(track, MidiTrack):
            # If playback is active, send a live CC#7 message
            if self.playback_state == "playing" and track.output_port_name:
                port = self.open_ports.get(track.output_port_name)
                if port:
                    midi_volume = int(volume * 127)
                    port.send(mido.Message('control_change', channel=track.channel, control=7, value=midi_volume))

    def set_track_velocity(self, track_index: int, velocity: float):
        """Sets the velocity multiplier for a specific MIDI track."""
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            print("Error: Velocity can only be set for MIDI tracks.")
            return

        if not 0.0 <= velocity:
            print("Error: Velocity multiplier must be a positive number.")
            return

        track.velocity = velocity
        print(f"Velocity for track '{track.name}' set to {velocity:.2f}.")

    def toggle_mute(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        track = self.song.tracks[track_index]
        track.is_muted = not track.is_muted
        status = "Muted" if track.is_muted else "Unmuted"
        print(f"Track '{track.name}' is now {status}.")

        if isinstance(track, AudioTrack) and self.playback_state != "stopped":
            with self.process_lock:
                active_process = next((p for p in self.active_audio_processes if p.track_index == track_index), None)

                if active_process:
                    # Process exists, just send the mute/unmute command
                    self._send_ipc_command(
                        active_process.socket_path,
                        {"command": ["set_property", "mute", track.is_muted]}
                    )
                elif not track.is_muted:
                    # No process exists, and we are UNMUTING. Start a new one.
                    print(f"Starting playback for newly unmuted track '{track.name}'...")
                    current_beat = self._get_current_beat()

                    audio_thread = threading.Thread(
                        target=self._play_audio_track,
                        args=(track, track_index, current_beat)
                    )
                    audio_thread.daemon = True
                    self.audio_threads.append(audio_thread)
                    audio_thread.start()

    def toggle_solo(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        target_track = self.song.tracks[track_index]
        is_being_soloed = not target_track.is_solo
        target_track.is_solo = is_being_soloed

        if is_being_soloed:
            for i, other_track in enumerate(self.song.tracks):
                if i == track_index:
                    continue
                if other_track.is_solo:
                    other_track.is_solo = False
                    print(f"Track '{other_track.name}' is now Un-soloed.")

        status = "Solo" if target_track.is_solo else "Un-soloed"
        print(f"Track '{target_track.name}' is now {status}.")

    def send_cc_message(self, port_name: str, channel: int, control: int, value: int):
        """Sends a single MIDI Control Change message to a specified port."""
        port = None
        is_temp_port = False
        try:
            # Check if the port is one of the open virtual ports
            found_virtual = False
            for vp in self.virtual_ports:
                if vp.name == port_name:
                    port = vp
                    found_virtual = True
                    break

            # If not a virtual port, open it as a temporary hardware port
            if not found_virtual:
                port = open_output(port_name)
                is_temp_port = True

            if port:
                # The channel from the user is 1-16, mido needs 0-15
                msg = mido.Message('control_change', channel=channel - 1, control=control, value=value)
                port.send(msg)
                print(f"Sent CC message to '{port_name}': Ch={channel}, CC={control}, Val={value}")
                return True # Indicate success
            else:
                print(f"Error: Could not find or open port '{port_name}'.")
                return False # Indicate failure

        except Exception as e:
            print(f"Error sending CC message to port '{port_name}': {e}")
            return False # Indicate failure
        finally:
            if is_temp_port and port and not port.closed:
                port.close()

    def prime_all_tracks(self):
        """Sends the current program/bank state for all assigned MIDI tracks."""
        print("Priming all assigned MIDI tracks...")
        for track in self.song.tracks:
            if not isinstance(track, MidiTrack) or not track.output_port_name:
                continue

            port = None
            is_temp_port = False
            port_name = track.output_port_name
            try:
                found_virtual = False
                for vp in self.virtual_ports:
                    if vp.name in port_name:
                        port = vp
                        found_virtual = True
                        break

                if not found_virtual:
                    port = open_output(port_name)
                    is_temp_port = True

                if port:
                    print(f"  - Sending state for track '{track.name}' to '{port.name}' on Ch: {track.channel + 1}")
                    if track.bank_msb is not None:
                        port.send(mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb))
                    if track.bank_lsb is not None:
                        port.send(mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb))
                    port.send(mido.Message('program_change', channel=track.channel, program=track.instrument))
            except Exception as e:
                print(f"  - Could not send state to port '{port_name}': {e}")
            finally:
                if is_temp_port and port:
                    port.close()
        print("Priming complete.")

    def load_song(self, filepath: str):
        try:
            self.song = import_song(filepath)
            print(f"Successfully loaded song from '{filepath}'.")
        except Exception as e:
            print(f"Error loading MIDI file: {e}")

    def save_song(self, filepath: str):
        try:
            export_to_midi(self.song, filepath)
            print(f"Song successfully saved to '{filepath}'.")
        except Exception as e:
            print(f"Error saving MIDI file: {e}")

    def save_project(self, basename: str):
        project_filepath = f"{basename}.proj.json"
        try:
            project_data = {
                "song": self.song,
                "virtual_ports": [vp.name for vp in self.virtual_ports],
                "audio_player_command": self.audio_player_command,
            }
            with open(project_filepath, 'w') as f:
                json.dump(project_data, f, indent=4, cls=CustomSongEncoder)
            print(f"Project saved to '{project_filepath}'")
        except Exception as e:
            print(f"Error saving project file: {e}")

    def load_project(self, basename: str):
        project_filepath = f"{basename}.proj.json"
        try:
            with open(project_filepath, 'r') as f:
                project_data = json.load(f, object_hook=song_decoder)

            self.song = project_data.get("song", Song(name="New Song"))

            # Restore audio player command, with a fallback for older projects
            self.audio_player_command = project_data.get(
                "audio_player_command",
                self.DEFAULT_AUDIO_PLAYER_COMMAND
            )
            # Handle migration from mplayer to mpv
            if "mplayer" in self.audio_player_command:
                print("Warning: Old 'mplayer' command found in project. Updating to 'mpv' default.")
                self.audio_player_command = self.DEFAULT_AUDIO_PLAYER_COMMAND

            # Restore virtual ports
            self.close_virtual_ports()
            self.virtual_ports = []
            for vp_name in project_data.get("virtual_ports", []):
                self.create_virtual_port(vp_name)

            print(f"Successfully loaded project from '{project_filepath}'")
        except FileNotFoundError:
            print(f"Error: Project file not found at '{project_filepath}'")
        except Exception as e:
            print(f"Error loading project file: {e}")

    def list_tracks(self) -> str:
        if not self.song.tracks:
            return "No tracks in the song."
        lines = [f"Song: {self.song.name} | Tempo: {self.song.tempo} BPM | Time Signature: {self.song.time_signature_numerator}/{self.song.time_signature_denominator}"]

        metro_status = "OFF"
        if self.song.metronome_enabled:
            port_info = f" -> Port: {self.song.metronome_port_name}" if self.song.metronome_port_name else " (No port assigned)"
            metro_status = f"ON{port_info}"
        lines.append(f"Metronome: {metro_status}")

        lines.append("=" * 20)
        for i, track in enumerate(self.song.tracks):
            status_info = ""
            if track.is_muted: status_info += " [M]"
            if track.is_solo: status_info += " [S]"

            if isinstance(track, MidiTrack):
                bank_info = ""
                if track.bank_msb is not None: bank_info = f", Bank: {track.bank_msb}:{track.bank_lsb or 0}"
                ch_info = f"Ch: {track.channel + 1}"
                prog_info = f"Prog: {track.instrument + 1}"
                vol_info = f"Vol: {track.volume:.2f}"
                vel_info = f"Vel: {track.velocity:.2f}"
                port_info = f" -> Port: {track.output_port_name}" if track.output_port_name else ""
                lines.append(f"[{i}] {track.name} (MIDI){status_info} ({ch_info}, {prog_info}{bank_info}, {vol_info}, {vel_info}, {len(track.events)} events){port_info}")
            elif isinstance(track, AudioTrack):
                start_pos_str = self._format_beats_to_position(track.start_time)
                lines.append(f"[{i}] {track.name} (Audio){status_info} (File: {track.filepath}, Starts at: {start_pos_str}, Vol: {track.volume:.2f})")
            else:
                lines.append(f"[{i}] {track.name} (Unknown Type){status_info}")
        return "\n".join(lines)

    def list_ports(self) -> str:
        lines = []
        try:
            lines.append("Available MIDI Input Ports:")
            input_ports = get_input_names()
            if input_ports:
                for i, port in enumerate(input_ports): lines.append(f"  [{i}] {port}")
            else:
                lines.append("  (None found)")

            lines.append("\nAvailable MIDI Output Ports:")
            output_ports = get_output_names()
            virtual_port_names = [vp.name for vp in self.virtual_ports]
            all_outputs = output_ports + virtual_port_names
            if all_outputs:
                for i, port in enumerate(all_outputs): lines.append(f"  [{i}] {port}")
            else:
                lines.append("  (None found)")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting MIDI ports: {e}"

    def create_virtual_port(self, name: str):
        try:
            port = open_output(name, virtual=True)
            self.virtual_ports.append(port)
            print(f"Created virtual MIDI port: '{name}'")
        except Exception as e:
            print(f"Error creating virtual port: {e}")

    def close_virtual_ports(self):
        for port in self.virtual_ports:
            if not port.closed:
                port.close()
        print("Virtual ports closed.")

    def delete_virtual_port(self, name: str):
        port_to_delete = None
        for vp in self.virtual_ports:
            if vp.name == name:
                port_to_delete = vp
                break

        if port_to_delete:
            for track in self.song.tracks:
                if isinstance(track, MidiTrack) and track.output_port_name == port_to_delete.name:
                    track.output_port_name = None
                    print(f"Un-assigned port from track '{track.name}'.")
            port_to_delete.close()
            self.virtual_ports.remove(port_to_delete)
            print(f"Virtual port '{name}' deleted.")
        else:
            print(f"Error: Virtual port '{name}' not found.")

    def _recording_thread_main(self, target_track: MidiTrack, start_beat: float, inport_name: str, outport_name: Optional[str], num_beats_to_record: Optional[float], original_mute_state: bool):
        """
        The main loop for the MIDI recording thread. This is a two-phase process.
        1. Waiting Phase: A blocking call waits for the first valid note_on message.
        During this time, no other tracks are playing.
        2. Recording Phase: Once the first note is received, playback of other tracks
        is started, and this thread switches to a non-blocking poll to record
        all subsequent notes in sync with the playback.
        """
        open_notes = {}
        outport = None
        is_virtual_port = False
        if self._stop_event.is_set():
            self._stop_event.clear()
            
        try:
            with mido.open_input(inport_name) as inport:
                if outport_name:
                    vp = next((p for p in self.virtual_ports if p.name == outport_name), None)
                    if vp:
                        outport = vp
                        is_virtual_port = True
                    else:
                        outport = open_output(outport_name)

                    print(f"Setting MIDI Thru instrument for track '{target_track.name}' on port '{outport_name}' to Ch:{target_track.channel + 1}, Prog:{target_track.instrument + 1}")
                    if target_track.bank_msb is not None:
                        outport.send(mido.Message('control_change', channel=target_track.channel, control=0, value=target_track.bank_msb))
                    if target_track.bank_lsb is not None:
                        outport.send(mido.Message('control_change', channel=target_track.channel, control=32, value=target_track.bank_lsb))
                    outport.send(mido.Message('program_change', channel=target_track.channel, program=target_track.instrument))

                # --- 1. Waiting Phase ---
                print("Waiting for first note to start recording...")
                first_msg = None
                while not self._stop_event.is_set():
                    for _ in inport.iter_pending(): pass
                    msg = inport.receive()
                    if outport:
                        if hasattr(msg, 'channel'):
                            thru_msg = msg.copy(channel=target_track.channel)
                            outport.send(thru_msg)
                        else:
                            outport.send(msg)
                    if msg.type == 'note_on' and msg.velocity > 0:
                        first_msg = msg
                        break

                if self._stop_event.is_set(): return

                # --- 2. Recording Phase ---
                first_note_time_beats = start_beat
                recording_start_time_sec = time.time()
                self.play(start_beat=first_note_time_beats)
                print(f"Recording started at beat {self._format_beats_to_position(first_note_time_beats)}. Type 'stop' to finish.")

                if first_msg is not None:
                    open_notes[first_msg.note] = (recording_start_time_sec, first_msg.velocity)

                while not self._stop_event.is_set():
                    for msg in inport.iter_pending():
                        if outport:
                            if hasattr(msg, 'channel'):
                                thru_msg = msg.copy(channel=target_track.channel)
                                outport.send(thru_msg)
                            else:
                                outport.send(msg)

                        now = time.time()
                        if msg.type == 'note_on' and msg.velocity > 0:
                            if msg.note not in open_notes:
                                open_notes[msg.note] = (now, msg.velocity)
                        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                            if msg.note in open_notes:
                                note_start_time_sec, velocity = open_notes.pop(msg.note)
                                duration_sec = now - note_start_time_sec
                                beats_per_second = self.song.tempo / 60.0
                                start_time_beats = first_note_time_beats + (note_start_time_sec - recording_start_time_sec) * beats_per_second
                                duration_beats = duration_sec * beats_per_second
                                note = Note(pitch=msg.note, velocity=velocity, duration=duration_beats)
                                event = Event(notes=[note], start_time=start_time_beats)
                                target_track.add_event(event)

                    if num_beats_to_record is not None:
                        elapsed_recording_beats = (time.time() - recording_start_time_sec) * (self.song.tempo / 60.0)
                        if elapsed_recording_beats >= num_beats_to_record:
                            print(f"\nFinished recording for {num_beats_to_record:.2f} beats.")
                            break
                    time.sleep(0.001)
        except Exception as e:
            print(f"\nAn error occurred during recording: {e}")
        finally:
            if outport and not is_virtual_port:
                outport.close()
            target_track.is_muted = original_mute_state
            self.is_recording = False
            print("\nRecording thread finished.")
        
    def _start_recording_internal(self, track_index: int, start_beat: float, num_beats_to_record: Optional[float], inport_name: str, replace_notes: bool):
        target_track = self.song.tracks[track_index]
        if not isinstance(target_track, MidiTrack):
            # This check is a safeguard, should be checked before calling
            print("Error: Recording is only supported for MIDI tracks.")
            return

        if replace_notes:
            end_beat = float('inf') if num_beats_to_record is None else start_beat + num_beats_to_record
            target_track.events = [e for e in target_track.events if not (start_beat <= e.start_time < end_beat)]
            print(f"Removed existing notes from beat {self._format_beats_to_position(start_beat)} onwards.")

        outport_name = target_track.output_port_name
        original_mute_state = target_track.is_muted
        target_track.is_muted = True

        self.is_recording = True
        self.recording_thread = threading.Thread(
            target=self._recording_thread_main,
            args=(target_track, start_beat, inport_name, outport_name, num_beats_to_record, original_mute_state)
        )
        self.recording_thread.daemon = True
        self.recording_thread.start()


    def record_track(self, track_index: int):
        if self.playback_state != "stopped":
            print("Error: Please stop playback before starting a new recording.")
            return
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        target_track = self.song.tracks[track_index]
        if not isinstance(target_track, MidiTrack):
            print("Error: Recording is only supported for MIDI tracks.")
            return

        try:
            # --- Gather Parameters ---
            start_pos_str = input(f"Start recording at position on track '{target_track.name}' (measure:beat) [default: 1:1]: ").strip()
            start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if start_beat is None: return

            measures_input = input("Record for how long (measures:beats)? (Press Enter for unlimited) ").strip()
            num_beats_to_record = None
            if measures_input:
                parts = measures_input.split(':')
                num_measures = int(parts[0])
                num_beats = int(parts[1]) if len(parts) == 2 else 0
                num_beats_to_record = (num_measures * self.song.time_signature_numerator) + num_beats

            replace_notes = False
            existing_notes_in_range = [e for e in target_track.events if e.start_time >= start_beat]
            if existing_notes_in_range:
                choice = input("There are existing notes. Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
                if choice.startswith('r'):
                    replace_notes = True

            input_ports = mido.get_input_names() # type: ignore
            if not input_ports:
                print("Error: No MIDI input ports found.")
                return
            print("Available MIDI input ports:")
            for i, port in enumerate(input_ports): print(f"  [{i}] {port}")
            inport_idx = int(input("Choose a port to record from: "))
            if not 0 <= inport_idx < len(input_ports):
                print("Error: Invalid port index.")
                return
            inport_name = input_ports[inport_idx]

        except (ValueError, IndexError):
            print("Error: Invalid number format or selection."); return

        # --- Store settings and start recording ---
        self.last_record_settings = {
            "track_index": track_index,
            "start_beat": start_beat,
            "num_beats_to_record": num_beats_to_record,
            "inport_name": inport_name,
            "replace_notes": replace_notes,
        }

        self._start_recording_internal(
            track_index=track_index,
            start_beat=start_beat,
            num_beats_to_record=num_beats_to_record,
            inport_name=inport_name,
            replace_notes=replace_notes
        )

    def record_bis(self):
        """Re-records using the last saved parameters."""
        if self.playback_state != "stopped":
            print("Error: Please stop playback before starting a new recording.")
            return

        if self.last_record_settings is None:
            print("Error: No previous recording settings found. Use 'record' first.")
            return

        print("Re-recording with last used settings...")
        # Unpack the stored settings and call the internal recording function
        self._start_recording_internal(**self.last_record_settings)

    def _send_ipc_command(self, socket_path: str, command: dict):
        """Sends a JSON command to the mpv IPC socket."""
        if not os.path.exists(socket_path):
            return  # Socket not ready yet
        try:
            # This only supports UNIX sockets for now.
            if sys.platform != "win32":
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.1)  # Don't block for too long
                    sock.connect(socket_path)
                    sock.sendall(json.dumps(command).encode('utf-8') + b'\n')
        except (socket.timeout, ConnectionRefusedError, FileNotFoundError):
            # These errors are expected if the socket is not ready, so we can ignore them.
            pass
        except Exception as e:
            # Log other, unexpected errors.
            print(f"\nError sending IPC command: {e}")

    def _play_audio_track(self, track: AudioTrack, track_index: int, start_beat: float, end_beat: Optional[float] = None):
        """Plays a single audio track in a separate mpv process."""
        import shlex
        process = None
        socket_path = ""
        tmp_sock = None
        try:
            # Create a persistent temporary file for the socket
            tmp_sock = tempfile.NamedTemporaryFile(prefix="mpv-socket-", delete=False)
            socket_path = tmp_sock.name
            tmp_sock.close()

            beats_per_second = self.song.tempo / 60.0
            if beats_per_second <= 0: return # Avoid division by zero

            # --- Build the mpv command ---
            command = shlex.split(self.audio_player_command)
            command.append(f"--input-ipc-server={socket_path}")
            command.append(f"--volume={track.volume * 100}")

            # --- Calculate seek, delay, and length ---
            # 1. Calculate seek or delay
            if track.start_time < start_beat:
                # Track starts before playback begins, so we seek into the audio file.
                seek_beats = start_beat - track.start_time
                seek_seconds = seek_beats / beats_per_second
                command.append(f"--start={seek_seconds}")
            else:
                # Track starts after playback begins, so we delay the audio.
                delay_beats = track.start_time - start_beat
                if delay_beats > 0:
                    delay_seconds = delay_beats / beats_per_second
                    command.append(f"--audio-delay={delay_seconds}")

            # 2. Calculate playback length if end_beat is specified
            if end_beat is not None:
                # The effective start of playback for this track is the later of the global start_beat or the track's own start_time.
                effective_start_beat = max(start_beat, track.start_time)

                # The duration is from the effective start to the global end.
                duration_beats = end_beat - effective_start_beat

                if duration_beats > 0:
                    length_seconds = duration_beats / beats_per_second
                    command.append(f"--length={length_seconds}")
                else:
                    # If duration is non-positive, the track shouldn't be played at all.
                    return

            command.append(track.filepath)

            kwargs = {
                'stdin': subprocess.DEVNULL,
                'stdout': subprocess.DEVNULL,
                'stderr': subprocess.DEVNULL
            }

            if sys.platform == "win32":
                kwargs['creationflags'] = subprocess.DETACHED_PROCESS
            else:
                kwargs['preexec_fn'] = os.setsid

            process = subprocess.Popen(command, **kwargs)
            time.sleep(0.1)

            active_process_info = ActiveAudioProcess(
                process=process,
                socket_path=socket_path,
                track_index=track_index
            )
            with self.process_lock:
                self.active_audio_processes.append(active_process_info)

            process.wait()

        except Exception as e:
            if not self._stop_event.is_set():
                print(f"\n[ERROR] in audio playback thread for track '{track.name}': {e}")
        finally:
            # Cleanup for the socket file is handled by _shutdown_audio_processes
            # when the main playback stops.
            if socket_path and os.path.exists(socket_path):
                 try:
                     os.remove(socket_path)
                 except OSError:
                     pass

    def _metronome_thread_main(self):
        """
        Thread dédié au métronome. Il joue des clics en continu.
        """
        beats_per_measure = self.song.time_signature_numerator
        
        # Pour une précision optimale, on calcule le temps d'une pulsation.
        # on évite les divisions par zéro
        if self.song.tempo <= 0 or beats_per_measure <= 0:
            return

        beat_duration_sec = 60.0 / self.song.tempo
        downbeat_tick_message = mido.Message('note_on', channel=self.metronome_channel, note=self.metronome_pitch_downbeat, velocity=100)
        beat_tick_message = mido.Message('note_on', channel=self.metronome_channel, note=self.metronome_pitch_beat, velocity=100)
        note_off_message = mido.Message('note_off', channel=self.metronome_channel, note=self.metronome_pitch_beat, velocity=0) # Note off for either pitch

        last_tick_time = time.time()
        beat_count = 0
        
        metro_port = self.open_ports.get(self.song.metronome_port_name)

        while not self._stop_event.is_set():
            self._run_event.wait()
            if self._stop_event.is_set(): break

            # Live check to see if the metronome has been disabled
            if not self.song.metronome_enabled:
                time.sleep(0.1)
                continue

            now = time.time()
            # On utilise un calcul basé sur le temps réel pour éviter la dérive
            expected_time = last_tick_time + beat_duration_sec
            if now >= expected_time:
                # C'est l'heure d'un clic
                last_tick_time = now # Réinitialiser le temps pour le prochain beat
                
                # On détermine le type de clic (downbeat ou beat)
                pitch_to_send = self.metronome_pitch_downbeat if (beat_count % beats_per_measure) == 0 else self.metronome_pitch_beat
                note_on_message = mido.Message('note_on', channel=self.metronome_channel, note=pitch_to_send, velocity=100)
                note_off_message.note = pitch_to_send # Mettre à jour la note pour le message off

                if metro_port:
                    metro_port.send(note_on_message)
                    # Envoie un message note_off après un petit délai
                    time.sleep(0.05)
                    metro_port.send(note_off_message)

                beat_count += 1
            
            time.sleep(0.001) # Petite pause pour ne pas surcharger le CPU
            
    def _get_song_length_in_beats(self) -> float:
        """Calculates the total length of the song in beats, considering both MIDI and audio tracks."""
        max_beats = 0.0

        # Find the end of the last MIDI event
        for track in self.song.tracks:
            if isinstance(track, MidiTrack):
                is_any_track_soloed = any(t.is_solo for t in self.song.tracks)
                should_play = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                if not should_play:
                    continue
                for event in track.events:
                    for note in event.notes:
                        event_end_beat = event.start_time + note.duration
                        if event_end_beat > max_beats:
                            max_beats = event_end_beat

        # Find the end of the last audio track
        for track in self.song.tracks:
            if isinstance(track, AudioTrack):
                try:
                    # This check is to avoid including muted/soloed tracks that won't be played
                    is_any_track_soloed = any(t.is_solo for t in self.song.tracks)
                    should_play = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                    if not should_play:
                        continue

                    segment = AudioSegment.from_file(track.filepath)
                    duration_beats = (len(segment) / 1000.0) * (self.song.tempo / 60.0)
                    track_end_beat = track.start_time + duration_beats
                    if track_end_beat > max_beats:
                        max_beats = track_end_beat
                except Exception as e:
                    # Ignore files that can't be read or other errors
                    print(f"Could not calculate duration for {track.filepath}: {e}")
                    pass

        return max_beats

    def _get_current_beat(self) -> float:
        """Calculates the current playback position in beats."""
        if self.playback_state == "stopped":
            return 0.0

        # This needs to be available from the play() call
        start_time_sec = getattr(self, 'playback_start_time', 0)
        start_beat = getattr(self, 'last_start_beat', 0)

        if self.playback_state == "paused":
            # If paused, the current time is frozen at the moment of pausing.
            pause_start_time = getattr(self, 'pause_start_time', start_time_sec)
            elapsed_sec = (pause_start_time - start_time_sec) - self.total_paused_time
        else: # playing
            elapsed_sec = (time.time() - start_time_sec) - self.total_paused_time

        mido_tempo = mido.bpm2tempo(self.song.tempo)
        ticks_per_beat = self.song.ticks_per_beat
        current_ticks = mido.second2tick(elapsed_sec, ticks_per_beat, mido_tempo)
        current_beat = start_beat + (current_ticks / ticks_per_beat)
        return current_beat

    def _play_thread(self, start_beat: float = 0.0, end_beat: Optional[float] = None, loop: bool = False):
        try:
            master_event_list = []
            ticks_per_beat = self.song.ticks_per_beat
            
            # 1. Build master list of MIDI events only
            for track_idx, track in enumerate(self.song.tracks):
                if isinstance(track, MidiTrack):
                    if not track.output_port_name:
                        continue
                    # Add initial state messages (bank/program change)
                    if track.bank_msb is not None:
                        master_event_list.append({'type': 'midi', 'tick': 0, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb)})
                    if track.bank_lsb is not None:
                        master_event_list.append({'type': 'midi', 'tick': 0, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb)})
                    master_event_list.append({'type': 'midi', 'tick': 0, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': mido.Message('program_change', channel=track.channel, program=track.instrument)})
                    master_event_list.append({'type': 'midi', 'tick': 0, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': mido.Message('control_change', channel=track.channel, control=7, value=int(track.volume * 127))})

                    for event in track.events:
                        for note in event.notes:
                            start_tick = int(event.start_time * ticks_per_beat)
                            end_tick = start_tick + int(note.duration * ticks_per_beat)
                            scaled_velocity = int(note.velocity * track.velocity)
                            clamped_velocity = max(0, min(127, scaled_velocity))
                            master_event_list.append({'type': 'midi', 'tick': start_tick, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': mido.Message('note_on', channel=track.channel, note=note.pitch, velocity=clamped_velocity)})
                            master_event_list.append({'type': 'midi', 'tick': end_tick, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': mido.Message('note_off', channel=track.channel, note=note.pitch, velocity=0)})

            # 2. Filter and normalize events based on playback range
            start_tick = int(start_beat * ticks_per_beat)
            end_tick = float('inf') if end_beat is None else int(end_beat * ticks_per_beat)

            ranged_event_list = []
            for e in master_event_list:
                if start_tick <= e['tick'] < end_tick:
                    ranged_event_list.append(e.copy())
            
            for event in ranged_event_list:
                event['tick'] -= start_tick

            if not ranged_event_list:
                if not any(isinstance(t, AudioTrack) and not t.is_muted for t in self.song.tracks):
                     print("No events to play in the selected range.")
            else:
                ranged_event_list.sort(key=lambda e: e['tick'])

            # 3. Main playback loop
            start_time_sec = time.time()
            next_event_index = 0

            song_length_beats = float('inf') if self.is_recording else self._get_song_length_in_beats()
            if end_beat is not None:
                song_length_beats = min(song_length_beats, end_beat)

            while not self._stop_event.is_set():
                self._run_event.wait()
                if self._stop_event.is_set(): break

                elapsed_sec = (time.time() - start_time_sec) - self.total_paused_time
                mido_tempo = mido.bpm2tempo(self.song.tempo)
                current_ticks = mido.second2tick(elapsed_sec, ticks_per_beat, mido_tempo)
                current_beat_float = start_beat + (current_ticks / ticks_per_beat)

                if current_beat_float >= song_length_beats:
                    if loop and not self.is_recording:
                        # Stop old audio processes
                        self._shutdown_audio_processes()
                        for t in self.audio_threads:
                            if t.is_alive():
                                t.join(timeout=0.5)

                        # Relancer les pistes audio
                        self.audio_threads = []
                        is_any_track_soloed = any(t.is_solo for t in self.song.tracks)
                        for i, track in enumerate(self.song.tracks):
                            if isinstance(track, AudioTrack):
                                should_play = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                                if should_play:
                                    audio_thread = threading.Thread(
                                        target=self._play_audio_track,
                                        args=(track, i, start_beat)
                                    )
                                    audio_thread.daemon = True
                                    self.audio_threads.append(audio_thread)
                                    audio_thread.start()

                        # Reset pour la nouvelle boucle
                        start_time_sec = time.time()
                        next_event_index = 0
                        self.total_paused_time = 0.0
                        self._all_notes_off()
                        continue
                    else:
                        break # Exit the loop

                # Display counter
                mode = "Recording" if self.is_recording else "Playing"
                beats_per_measure = self.song.time_signature_numerator if self.song.time_signature_numerator > 0 else 4
                display_measure = int(current_beat_float / beats_per_measure) + 1
                display_beat_in_measure = int(current_beat_float % beats_per_measure) + 1
                print(f"\r{mode}: Measure {display_measure}, Beat {display_beat_in_measure} ", end="")

                # Dispatch MIDI events that are due
                while next_event_index < len(ranged_event_list) and ranged_event_list[next_event_index]['tick'] <= current_ticks:
                    event = ranged_event_list[next_event_index]
                    track = self.song.tracks[event['track_idx']]

                    is_any_track_soloed = any(t.is_solo for t in self.song.tracks)
                    should_play = (track.is_solo or not is_any_track_soloed) and not track.is_muted

                    if should_play:
                        if event['type'] == 'midi':
                            port = self.open_ports.get(event['port_name'])
                            if port: port.send(event['message'])
                    next_event_index += 1

                time.sleep(0.01)

            # After the loop is finished, wait for any audio threads to complete.
            for t in self.audio_threads:
                while t.is_alive() and not self._stop_event.is_set():
                    t.join(timeout=0.1)

        except Exception as e:
            if not self._stop_event.is_set():
                print(f"\nError during playback: {e}")
        finally:
            # This is the single point of truth for all cleanup
            self._shutdown_audio_processes()
            self._all_notes_off()
            for port in self.temporary_ports:
                if not port.closed:
                    port.close()
            self.temporary_ports = []
            self.open_ports.clear()
            self.playback_state = "stopped"
            if not self._stop_event.is_set():
                print("\nPlayback finished.")
                if self.is_recording:
                    self.stop()
                
    def play(self, start_beat: Optional[float] = None, end_beat: Optional[float] = None, loop: bool = False):
        # Case 1: play() is called with no args, which means "resume" or "play from last position"
        if start_beat is None:
            if self.playback_state == "paused":
                self.pause()  # This will resume playback
                return
            # If stopped, play from the last starting position
            start_beat = self.last_start_beat

        # Case 2: A new start position is given. Stop any current playback.
        if self.playback_state != "stopped":
            self.stop()
            if self.playback_thread and self.playback_thread.is_alive():
                self.playback_thread.join(timeout=1.0)
            for t in self.audio_threads:
                if t.is_alive():
                    t.join(timeout=1.0)

        # Remember this start position for future resume/restart
        self.last_start_beat = start_beat

        if end_beat is not None and end_beat <= start_beat:
            print("Error: End position must be after the start position.")
            return

        # --- Reset state for new playback ---
        self.total_paused_time = 0.0
        self.open_ports.clear()
        self.temporary_ports = []
        self.audio_threads = [] # Clear the list for new audio threads

        # --- Audio Playback ---
        is_any_track_soloed = any(t.is_solo for t in self.song.tracks)
        has_audio_to_play = False
        for i, track in enumerate(self.song.tracks):
            if isinstance(track, AudioTrack):
                should_play = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                # Check if the track is within the playback range at all
                if should_play and track.start_time < (end_beat if end_beat is not None else float('inf')):
                    audio_thread = threading.Thread(
                        target=self._play_audio_track,
                        args=(track, i, start_beat, end_beat)
                    )
                    audio_thread.daemon = True
                    self.audio_threads.append(audio_thread)
                    has_audio_to_play = True

        # --- MIDI Port Setup ---
        required_ports = {track.output_port_name for track in self.song.tracks if isinstance(track, MidiTrack) and track.output_port_name}
        if self.song.metronome_enabled and self.song.metronome_port_name:
            required_ports.add(self.song.metronome_port_name)

        if not required_ports and not has_audio_to_play:
            print("Nothing to play: No MIDI ports assigned, no audio tracks, and metronome is off.")
            return

        # Open all required MIDI ports
        for name in required_ports:
            vp = next((p for p in self.virtual_ports if p.name == name), None)
            if vp:
                self.open_ports[name] = vp
            else:
                try:
                    self.open_ports[name] = open_output(name)
                    self.temporary_ports.append(self.open_ports[name])
                except Exception as e:
                    print(f"Error opening port '{name}': {e}. Aborting playback.")
                    for p in self.temporary_ports: p.close()
                    return

        # --- Start Playback Threads ---
        self._stop_event.clear()
        self._run_event.set()
        self.playback_state = "playing"
        self.playback_start_time = time.time()

        if self.song.metronome_enabled and self.song.metronome_port_name:
            self.metronome_thread = threading.Thread(target=self._metronome_thread_main)
            self.metronome_thread.daemon = True
            self.metronome_thread.start()

        self.playback_thread = threading.Thread(
            target=self._play_thread,
            kwargs={'start_beat': start_beat, 'end_beat': end_beat, 'loop': loop}
        )
        self.playback_thread.daemon = True
        self.playback_thread.start()

        # Start all audio threads
        for t in self.audio_threads:
            t.start()

    def pause(self):
        if self.playback_state == "stopped":
            print("Nothing to pause.")
            return

        if self.playback_state == "playing":
            # Pause MIDI playback
            self._run_event.clear()
            self.pause_start_time = time.time()
            self._all_notes_off()

            # Pause all active audio processes by sending IPC commands
            with self.process_lock:
                for ap in self.active_audio_processes:
                    # Per user request: pause, then mute.
                    self._send_ipc_command(ap.socket_path, {"command": ["set_property", "pause", True]})
                    self._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", True]})

            self.playback_state = "paused"
            print("Playback paused.")

        elif self.playback_state == "paused":
            # Resume MIDI playback
            self.total_paused_time += time.time() - self.pause_start_time
            self._run_event.set()

            # Resume all active audio processes by sending IPC commands
            with self.process_lock:
                for ap in self.active_audio_processes:
                    # Per user request: unmute, then unpause.
                    self._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", False]})
                    self._send_ipc_command(ap.socket_path, {"command": ["set_property", "pause", False]})

            self.playback_state = "playing"
            print("Resuming playback...")

    def _shutdown_audio_processes(self):
        """Stops all active audio subprocesses and cleans up their temp files."""
        import os
        with self.process_lock:
            for ap in list(self.active_audio_processes):
                try:
                    # Use the quit command via IPC if possible
                    if ap.socket_path and os.path.exists(ap.socket_path):
                         self._send_ipc_command(ap.socket_path, {"command": ["quit"]})
                         time.sleep(0.1) # Give it a moment to quit

                    if ap.process.poll() is None:
                        ap.process.terminate()
                        ap.process.wait(timeout=1.0)
                except (subprocess.TimeoutExpired, Exception):
                    if ap.process.poll() is None:
                        ap.process.kill()

                if ap.temp_filepath and os.path.exists(ap.temp_filepath):
                    try:
                        os.remove(ap.temp_filepath)
                    except OSError:
                        pass
                if ap.socket_path and os.path.exists(ap.socket_path):
                    try:
                        os.remove(ap.socket_path)
                    except OSError:
                        pass
            self.active_audio_processes.clear()

    def stop(self):
        if not self.is_recording and self.playback_state == "stopped":
            print("Already stopped.")
            return

        print("Stopping session...")
        self._stop_event.set()

        if self.playback_state == "paused":
            self._run_event.set()

        if self.playback_thread and self.playback_thread.is_alive() and threading.current_thread() != self.playback_thread:
            self.playback_thread.join(timeout=2.0)
        
        # Assurez-vous d'arrêter aussi le thread du métronome
        if self.metronome_thread and self.metronome_thread.is_alive():
            self.metronome_thread.join(timeout=2.0)


        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=2.0)

        # Reset all state
        self.playback_state = "stopped"
        self.recording_thread = None
        self.is_recording = False
        print("Session stopped.")

    def restart(self):
        """Restarts playback from the beginning."""
        if self.playback_state != "stopped":
            self.stop()
        # Explicitly play from the beginning (beat 0)
        self.play(start_beat=0.0)