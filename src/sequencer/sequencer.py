import time
import mido
import threading
import json
from typing import Optional
from .models import Song, Track, Event, Note
from .midi_import import import_song
from .midi_export import export_to_midi

class Sequencer:
    def __init__(self, tempo: int = 120):
        self.song = Song(name="New Song", tempo=tempo)
        self.playback_state = "stopped"
        self.playback_thread = None
        self.open_ports = {}
        self.virtual_ports = []
        self.temporary_ports = []
        self._stop_event = threading.Event()
        self._run_event = threading.Event()
        self._run_event.set()

        self.metronome_only_mode = False

        # Metronome settings
        self.metronome_channel = 9  # Channel 10 (0-indexed)
        self.metronome_pitch_downbeat = 76  # High Wood Block
        self.metronome_pitch_beat = 77  # Low Wood Block

    def _all_notes_off(self):
        for port in self.open_ports.values():
            if port and not port.closed:
                for channel in range(16):
                    port.send(mido.Message('control_change', channel=channel, control=123, value=0))
        print("Sent all notes off to all open ports.")

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

    def add_track(self, name: str, instrument: int = 0):
        track = Track(name=name, instrument=instrument)
        self.song.add_track(track)
        print(f"Track '{name}' added.")

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
        start_measure = None
        end_measure = None

        try:
            s_measure_input = input("Erase from measure (default: all track): ").strip()
            if s_measure_input == "":
                start_measure = None # This signals a full erase
            else:
                start_measure = int(s_measure_input)
                # Only ask for end measure if a start measure was given
                e_measure_input = input(f"Erase up to measure (optional, press Enter for end of track): ").strip()
                if e_measure_input != "":
                    end_measure = int(e_measure_input)
        except ValueError:
            print("Error: Invalid measure number.")
            return

        # --- Confirmation ---
        confirm_message = ""
        if start_measure is None:
            confirm_message = f"Are you sure you want to erase ALL notes from track '{track.name}'? [y/N] "
        else:
            end_str = f" to measure {end_measure}" if end_measure else " to the end of the track"
            confirm_message = f"Are you sure you want to erase notes from measure {start_measure}{end_str} on track '{track.name}'? [y/N] "

        if input(confirm_message).lower() != 'y':
            print("Erase cancelled.")
            return

        # --- Execution ---
        if start_measure is None:
            track.events.clear()
            print(f"Erased all events from track '{track.name}'.")
            return

        # Ranged erase logic
        beats_per_measure = self.song.time_signature_numerator * (4 / self.song.time_signature_denominator)

        start_beat = (start_measure - 1) * beats_per_measure

        end_beat = float('inf')
        if end_measure is not None:
            if end_measure < start_measure:
                print("Error: End measure cannot be before the start measure.")
                return
            end_beat = end_measure * beats_per_measure

        initial_event_count = len(track.events)
        track.events = [
            event for event in track.events
            if not (start_beat <= event.start_time < end_beat)
        ]
        removed_count = initial_event_count - len(track.events)

        print(f"Erased {removed_count} event(s) from track '{track.name}'.")

    def rename_track(self, track_index: int, new_name: str):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        old_name = self.song.tracks[track_index].name
        self.song.tracks[track_index].name = new_name
        print(f"Track '{old_name}' renamed to '{new_name}'.")

    def move_track_section(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        track = self.song.tracks[track_index]

        try:
            start_measure = int(input("Move from start measure: ").strip())
            num_measures = int(input("Number of measures to move: ").strip())
            destination_measure = int(input("Move to destination measure: ").strip())

            if start_measure < 1 or num_measures < 1 or destination_measure < 1:
                print("Error: Measure numbers and count must be 1 or greater.")
                return

            if destination_measure >= start_measure and destination_measure < start_measure + num_measures:
                print("Error: Destination cannot be inside the source range.")
                return

        except ValueError:
            print("Error: Invalid number.")
            return

        # Confirmation
        confirm_message = (
            f"On track '{track.name}', move {num_measures} measure(s) "
            f"from measure {start_measure} to measure {destination_measure}. Are you sure? [y/N] "
        )
        if input(confirm_message).lower() != 'y':
            print("Move cancelled.")
            return

        # --- Core move logic ---
        beats_per_measure = self.song.time_signature_numerator * (4 / self.song.time_signature_denominator)

        source_start_beat = (start_measure - 1) * beats_per_measure
        source_end_beat = source_start_beat + (num_measures * beats_per_measure)

        offset_beats = (destination_measure - start_measure) * beats_per_measure

        events_to_move = []
        other_events = []

        for event in track.events:
            if source_start_beat <= event.start_time < source_end_beat:
                events_to_move.append(event)
            else:
                other_events.append(event)

        if not events_to_move:
            print("No notes found in the specified source range to move.")
            return

        moved_count = 0
        for event in events_to_move:
            new_start_time = event.start_time + offset_beats
            if new_start_time < 0:
                print(f"Warning: Moving event would result in a negative start time ({new_start_time:.2f} beats). Skipping this event.")
                other_events.append(event) # Put it back without moving it
            else:
                event.start_time = new_start_time
                moved_count += 1

        # Recombine and sort
        track.events = other_events + events_to_move
        track.events.sort(key=lambda e: e.start_time)

        print(f"Moved {moved_count} event(s) on track '{track.name}'.")

    def assign_port(self, track_index: int, port_name: str):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        self.song.tracks[track_index].output_port_name = port_name
        print(f"Assigned port '{port_name}' to track '{self.song.tracks[track_index].name}'.")

    def unassign_port(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        track = self.song.tracks[track_index]
        if track.output_port_name:
            print(f"Un-assigned port from track '{track.name}'.")
            track.output_port_name = None
        else:
            print(f"Track '{track.name}' has no port assigned.")

    def set_bank(self, track_index: int, msb: int, lsb: int = 0):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        if not 0 <= msb <= 127 and 0 <= lsb <= 127:
            print("Error: Bank values (MSB, LSB) must be between 0 and 127.")
            return

        track = self.song.tracks[track_index]
        track.bank_msb = msb
        track.bank_lsb = lsb
        print(f"Set bank for track '{track.name}' to MSB={msb}, LSB={lsb}.")

    def set_channel(self, track_index: int, channel: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        if not 1 <= channel <= 16:
            print("Error: MIDI channel must be between 1 and 16.")
            return

        track = self.song.tracks[track_index]
        track.channel = channel - 1 # Convert to 0-indexed for mido
        print(f"Set MIDI channel for track '{track.name}' to {channel}.")

    def set_program(self, track_index: int, program: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        if not 0 <= program <= 127:
            print("Error: Program number must be between 0 and 127.")
            return

        track = self.song.tracks[track_index]
        track.instrument = program
        print(f"Set program for track '{track.name}' to {program + 1}.")

    def toggle_mute(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        track = self.song.tracks[track_index]
        track.is_muted = not track.is_muted
        status = "Muted" if track.is_muted else "Unmuted"
        print(f"Track '{track.name}' is now {status}.")

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

    def prime_all_tracks(self):
        """Sends the current program/bank state for all assigned tracks."""
        print("Priming all assigned tracks...")
        for track in self.song.tracks:
            if not track.output_port_name:
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
                    port = mido.open_output(port_name)
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
        midi_filepath = f"{basename}.mid"
        project_filepath = f"{basename}.proj.json"
        self.save_song(midi_filepath)
        try:
            project_data = {
                "midi_file": midi_filepath,
                "virtual_ports": [vp.name for vp in self.virtual_ports],
                "track_assignments": [
                    {
                        "track_name": t.name,
                        "port_name": t.output_port_name
                    }
                    for t in self.song.tracks if t.output_port_name
                ],
                "metronome_settings": {
                    "enabled": self.song.metronome_enabled,
                    "port_name": self.song.metronome_port_name
                }
            }
            with open(project_filepath, 'w') as f:
                json.dump(project_data, f, indent=4)
            print(f"Project configuration saved to '{project_filepath}'")
        except Exception as e:
            print(f"Error saving project file: {e}")

    def load_project(self, basename: str):
        project_filepath = f"{basename}.proj.json"
        try:
            with open(project_filepath, 'r') as f:
                project_data = json.load(f)

            midi_file = project_data.get("midi_file")
            if not midi_file:
                print("Error: Project file is missing 'midi_file' key.")
                return
            self.load_song(midi_file)

            self.close_virtual_ports()
            self.virtual_ports = []
            for vp_name in project_data.get("virtual_ports", []):
                self.create_virtual_port(vp_name)

            assignments = project_data.get("track_assignments", [])
            for assignment in assignments:
                track_name = assignment.get("track_name")
                port_name = assignment.get("port_name")
                if track_name and port_name:
                    track_indices = [i for i, t in enumerate(self.song.tracks) if t.name == track_name]
                    if track_indices:
                        self.assign_port(track_indices[0], port_name)
                    else:
                        print(f"Warning: Could not find track '{track_name}' to assign port.")

            metronome_settings = project_data.get("metronome_settings", {})
            self.song.metronome_enabled = metronome_settings.get("enabled", False)
            self.song.metronome_port_name = metronome_settings.get("port_name")
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
            bank_info = ""
            if track.bank_msb is not None: bank_info = f", Bank: {track.bank_msb}:{track.bank_lsb or 0}"
            ch_info = f"Ch: {track.channel + 1}"
            prog_info = f"Prog: {track.instrument + 1}"
            port_info = f" -> Port: {track.output_port_name}" if track.output_port_name else ""
            lines.append(f"[{i}] {track.name}{status_info} ({ch_info}, {prog_info}{bank_info}, {len(track.events)} events){port_info}")
        return "\n".join(lines)

    def list_ports(self) -> str:
        lines = []
        try:
            lines.append("Available MIDI Input Ports:")
            input_ports = mido.get_input_names()
            if input_ports:
                for i, port in enumerate(input_ports): lines.append(f"  [{i}] {port}")
            else:
                lines.append("  (None found)")

            lines.append("\nAvailable MIDI Output Ports:")
            output_ports = mido.get_output_names()
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
            port = mido.open_output(name, virtual=True)
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
                if track.output_port_name == port_to_delete.name:
                    track.output_port_name = None
                    print(f"Un-assigned port from track '{track.name}'.")
            port_to_delete.close()
            self.virtual_ports.remove(port_to_delete)
            print(f"Virtual port '{name}' deleted.")
        else:
            print(f"Error: Virtual port '{name}' not found.")

    def record_track(self, track_index: int, start_measure: Optional[int] = None):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        if start_measure is None:
            try:
                measure_input = input("Start recording at measure (default: 1): ").strip()
                if measure_input == "":
                    start_measure = 1
                else:
                    start_measure = int(measure_input)
            except ValueError:
                print("Error: Invalid measure number.")
                return

        if start_measure < 1:
            print("Error: Start measure must be 1 or greater.")
            return

        target_track = self.song.tracks[track_index]
        beats_per_measure = self.song.time_signature_numerator * (4 / self.song.time_signature_denominator)
        start_beat = (start_measure - 1) * beats_per_measure

        # Check for existing notes from the start_beat onwards
        existing_notes_in_range = [
            event for event in target_track.events
            if event.start_time >= start_beat
        ]

        overwrite_mode = "add"
        num_measures_to_record = None

        if existing_notes_in_range:
            print("There are existing notes from this measure onwards.")
            while True:
                choice = input("Do you want to (r)eplace the existing notes or (a)dd to them? [r/a] ").lower()
                if choice in ['r', 'replace']:
                    overwrite_mode = "replace"
                    break
                elif choice in ['a', 'add']:
                    overwrite_mode = "add"
                    break
                else:
                    print("Invalid choice. Please enter 'r' or 'a'.")

        # Ask for number of measures to record
        while True:
            try:
                measures_input = input("How many measures to record? (Press Enter for unlimited) ").strip()
                if measures_input == "":
                    num_measures_to_record = None
                    break
                else:
                    num_measures_to_record = int(measures_input)
                    if num_measures_to_record <= 0:
                        print("Error: Number of measures must be positive.")
                        continue
                    break
            except ValueError:
                print("Error: Invalid number.")

        # Handle overwrite logic
        if overwrite_mode == "replace":
            end_beat = float('inf')
            if num_measures_to_record is not None:
                end_beat = start_beat + (num_measures_to_record * beats_per_measure)

            # Remove events within the specified range
            initial_event_count = len(target_track.events)
            target_track.events = [
                event for event in target_track.events
                if not (start_beat <= event.start_time < end_beat)
            ]
            removed_count = initial_event_count - len(target_track.events)
            if removed_count > 0:
                print(f"Removed {removed_count} event(s) from the recording range.")

        inport_name, outport_name = None, None
        try:
            input_ports = mido.get_input_names()
            if not input_ports:
                print("Error: No MIDI input ports found.")
                return
            print("Available MIDI input ports:")
            for i, port in enumerate(input_ports): print(f"  [{i}] {port}")
            inport_idx = int(input("Choose a port to record from: "))
            inport_name = input_ports[inport_idx]

            thru_choice = input("Enable MIDI Thru to an output port? [y/N] ").lower()
            if thru_choice == 'y':
                hardware_ports = mido.get_output_names()
                virtual_port_names = [vp.name for vp in self.virtual_ports]
                all_outputs = hardware_ports + virtual_port_names
                if not all_outputs:
                    print("No MIDI output ports found for Thru.")
                else:
                    print("Available MIDI output ports:")
                    for i, port in enumerate(all_outputs): print(f"  [{i}] {port}")
                    outport_idx = int(input("Choose a port for MIDI Thru (or -1 to disable): "))
                    if 0 <= outport_idx < len(all_outputs):
                        outport_name = all_outputs[outport_idx]
        except (ValueError, IndexError):
            print("Error: Invalid selection.")
            return

        open_notes = {}
        outport = None
        try:
            if self.song.metronome_enabled:
                self.metronome_only_mode = True
                # Call play with a default start_measure to prevent interactive prompts
                self.play(start_measure=1)

            with mido.open_input(inport_name) as inport:
                if outport_name:
                    outport = mido.open_output(outport_name)
                    print(f"Listening on '{inport_name}' with MIDI Thru to '{outport_name}'. Waiting for first note...")
                else:
                    print(f"Listening on '{inport_name}'. Waiting for first note...")

                recording_start_time_sec = None
                beats_per_second = self.song.tempo / 60

                max_duration_beats = None
                if num_measures_to_record is not None:
                    max_duration_beats = num_measures_to_record * beats_per_measure
                    print(f"Recording for {num_measures_to_record} measure(s) ({max_duration_beats:.2f} beats).")

                for msg in inport:
                    if outport: outport.send(msg)
                    now = time.time()

                    if recording_start_time_sec is None:
                        recording_start_time_sec = now
                        print("Recording started. Press Ctrl+C or play for the specified duration to stop.")

                    if max_duration_beats is not None:
                        elapsed_beats = (now - recording_start_time_sec) * beats_per_second
                        if elapsed_beats >= max_duration_beats:
                            print(f"\nFinished recording {num_measures_to_record} measure(s).")
                            break

                    if msg.type == 'note_on' and msg.velocity > 0:
                        if msg.note not in open_notes:
                            open_notes[msg.note] = (now, msg.velocity)
                    elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                        if msg.note in open_notes:
                            start_time_sec, velocity = open_notes.pop(msg.note)
                            duration_sec = now - start_time_sec

                            start_time_beats = start_beat + (start_time_sec - recording_start_time_sec) * beats_per_second
                            duration_beats = duration_sec * beats_per_second

                            note = Note(pitch=msg.note, velocity=velocity, duration=duration_beats)
                            event = Event(notes=[note], start_time=start_time_beats)
                            target_track.add_event(event)
                            print(f"Recorded note: {note.pitch}, start: {start_time_beats:.2f}, duration: {duration_beats:.2f} beats")
        except KeyboardInterrupt:
            print("\nRecording stopped.")
        except Exception as e:
            print(f"An error occurred during recording: {e}")
        finally:
            if self.metronome_only_mode:
                self.stop()
                self.metronome_only_mode = False

            if outport:
                outport.close()
                print(f"Closed Thru port '{outport_name}'.")

    def _play_thread(self, start_measure: int = 1, end_measure: Optional[int] = None, loop: bool = False):
        # Metronome-only mode for recording count-in
        if self.metronome_only_mode:
            port = self.open_ports.get(self.song.metronome_port_name)
            if not port:
                print(f"Error: Metronome port '{self.song.metronome_port_name}' not open.")
                return

            try:
                ticks_per_beat = 480
                beat_counter = 0
                start_time_sec = time.time()
                playback_cursor_sec = 0.0

                while not self._stop_event.is_set():
                    mido_tempo = mido.bpm2tempo(self.song.tempo)
                    delta_sec = mido.tick2second(ticks_per_beat, ticks_per_beat, mido_tempo)

                    if beat_counter > 0:
                        playback_cursor_sec += delta_sec

                    target_real_time_sec = start_time_sec + playback_cursor_sec
                    sleep_duration = target_real_time_sec - time.time()
                    if sleep_duration > 0:
                        time.sleep(sleep_duration)

                    if self._stop_event.is_set():
                        break

                    is_downbeat = (beat_counter % self.song.time_signature_numerator) == 0
                    pitch = self.metronome_pitch_downbeat if is_downbeat else self.metronome_pitch_beat

                    note_on = mido.Message('note_on', channel=self.metronome_channel, note=pitch, velocity=100)
                    note_off = mido.Message('note_off', channel=self.metronome_channel, note=pitch, velocity=0)

                    port.send(note_on)
                    time.sleep(0.05)
                    port.send(note_off)

                    beat_counter += 1
            except Exception as e:
                print(f"Error in metronome thread: {e}")
            finally:
                # The main stop() method handles port cleanup and state change
                print("Metronome stopped.")
            return

        # Full playback mode
        try:
            master_event_list = []
            ticks_per_beat = 480

            # 1. Build track events
            for track_idx, track in enumerate(self.song.tracks):
                if not track.output_port_name:
                    continue
                # Add bank select and program change messages
                if track.bank_msb is not None:
                    master_event_list.append({'tick': 0, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb)})
                if track.bank_lsb is not None:
                    master_event_list.append({'tick': 0, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb)})
                program_change_msg = mido.Message('program_change', channel=track.channel, program=track.instrument)
                master_event_list.append({'tick': 0, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': program_change_msg})
                # Add note events
                for event in track.events:
                    for note in event.notes:
                        start_tick = int(event.start_time * ticks_per_beat)
                        end_tick = start_tick + int(note.duration * ticks_per_beat)
                        note_on_msg = mido.Message('note_on', channel=track.channel, note=note.pitch, velocity=note.velocity)
                        note_off_msg = mido.Message('note_off', channel=track.channel, note=note.pitch, velocity=0)
                        master_event_list.append({'tick': start_tick, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': note_on_msg})
                        master_event_list.append({'tick': end_tick, 'track_idx': track_idx, 'port_name': track.output_port_name, 'message': note_off_msg})

            # 2. Build metronome events
            if self.song.metronome_enabled and self.song.metronome_port_name:
                last_event_tick = 0
                if master_event_list:
                    last_event_tick = max(e['tick'] for e in master_event_list)
                num_beats = (last_event_tick // ticks_per_beat) + 1
                for beat in range(num_beats):
                    tick = beat * ticks_per_beat
                    is_downbeat = (beat % self.song.time_signature_numerator) == 0
                    pitch = self.metronome_pitch_downbeat if is_downbeat else self.metronome_pitch_beat
                    note_on = mido.Message('note_on', channel=self.metronome_channel, note=pitch, velocity=100)
                    note_off = mido.Message('note_off', channel=self.metronome_channel, note=pitch, velocity=0)
                    master_event_list.append({'tick': tick, 'track_idx': -1, 'port_name': self.song.metronome_port_name, 'message': note_on})
                    master_event_list.append({'tick': tick + ticks_per_beat // 4, 'track_idx': -1, 'port_name': self.song.metronome_port_name, 'message': note_off})

            # 3. Filter and normalize events for ranged playback
            beats_per_measure = self.song.time_signature_numerator * (4 / self.song.time_signature_denominator)
            start_beat = (start_measure - 1) * beats_per_measure
            start_tick = int(start_beat * ticks_per_beat)

            end_tick = float('inf')
            if end_measure is not None:
                # The end beat is the start of the measure *after* the end_measure
                end_beat = end_measure * beats_per_measure
                end_tick = int(end_beat * ticks_per_beat)

            # Filter events that are within the playback range
            ranged_event_list = [
                event for event in master_event_list
                if start_tick <= event['tick'] < end_tick
            ]

            # Normalize ticks so playback starts immediately
            if start_tick > 0 and ranged_event_list:
                # Create a shallow copy of the event dictionaries
                ranged_event_list = [e.copy() for e in ranged_event_list]
                for event in ranged_event_list:
                    event['tick'] -= start_tick

            if not ranged_event_list:
                print("No notes to play in the selected range.")
                return

            # 4. Sort and play
            ranged_event_list.sort(key=lambda e: e['tick'])

            if loop:
                print("Looping playback... Press 'stop' to exit.")

            # The main loop for playback, which can be repeated for the "loop" feature
            while not self._stop_event.is_set():
                if loop:
                    loop_message = f"Looping measures {start_measure}"
                    if end_measure:
                        loop_message += f" to {end_measure}."
                    else:
                        loop_message += " to end."
                    print(loop_message)
                else:
                    print(f"Playing on {len(self.open_ports)} port(s)...")

                last_tick = 0
                start_time_sec = time.time()
                playback_cursor_sec = 0.0

                for event_details in ranged_event_list:
                    self._run_event.wait()
                    if self._stop_event.is_set(): break

                    delta_ticks = event_details['tick'] - last_tick
                    if delta_ticks > 0:
                        mido_tempo = mido.bpm2tempo(self.song.tempo)
                        delta_sec = mido.tick2second(delta_ticks, ticks_per_beat, mido_tempo)
                        playback_cursor_sec += delta_sec

                    target_real_time_sec = start_time_sec + playback_cursor_sec
                    sleep_duration = target_real_time_sec - time.time()
                    if sleep_duration > 0:
                        time.sleep(sleep_duration)

                    if self._stop_event.is_set(): break

                    port_name = event_details['port_name']
                    port = self.open_ports.get(port_name)
                    if not port: continue

                    if event_details['track_idx'] != -1:
                        track = self.song.tracks[event_details['track_idx']]
                        is_any_track_soloed = any(t.is_solo for t in self.song.tracks)
                        should_play_event = False
                        if is_any_track_soloed:
                            if track.is_solo: should_play_event = True
                        elif not track.is_muted:
                            should_play_event = True
                        if should_play_event:
                            port.send(event_details['message'])
                    else: # Metronome events
                        # Only play metronome if it falls within the original, non-normalized tick range
                        original_tick = event_details['tick'] + start_tick
                        if self.song.metronome_enabled and start_tick <= original_tick < end_tick:
                            port.send(event_details['message'])

                    last_tick = event_details['tick']

                if not loop or self._stop_event.is_set():
                    break

                self._all_notes_off()
                time.sleep(0.1)
        except Exception as e:
            print(f"\nError during playback: {e}")
        finally:
            self._all_notes_off()
            for port in self.temporary_ports:
                if not port.closed:
                    port.close()
            self.temporary_ports = []
            self.open_ports.clear()
            self.playback_state = "stopped"
            print("Playback finished.")

    def play(self, start_measure: Optional[int] = None, end_measure: Optional[int] = None, loop: bool = False):
        if self.playback_state == "playing":
            print("Already playing.")
            return
        if self.playback_state == "paused":
            self.pause()
            return

        # Only prompt for measures if not in metronome-only mode (for recording)
        if not self.metronome_only_mode:
            try:
                if start_measure is None:
                    measure_input = input("Start at measure (default: 1): ").strip()
                    start_measure = 1 if measure_input == "" else int(measure_input)

                if end_measure is None:
                    measure_input = input("End at measure (optional, press Enter for end of song): ").strip()
                    if measure_input != "":
                        end_measure = int(measure_input)
                    else:
                        end_measure = None # Explicitly set to None if user presses Enter

            except ValueError:
                print("Error: Invalid measure number.")
                return

        # If we are in metronome only mode and no start measure was passed, default to 1
        # This is a safeguard, as record_track should now always pass start_measure=1
        if self.metronome_only_mode and start_measure is None:
            start_measure = 1

        if start_measure < 1:
            print("Error: Start measure must be 1 or greater.")
            return
        if end_measure is not None and end_measure < start_measure:
            print("Error: End measure cannot be before the start measure.")
            return

        self.open_ports.clear()
        self.temporary_ports = []

        required_ports = {t.output_port_name for t in self.song.tracks if t.output_port_name}
        if self.song.metronome_enabled and self.song.metronome_port_name:
            required_ports.add(self.song.metronome_port_name)

        if not required_ports:
            if self.metronome_only_mode:
                print("No metronome port assigned. Use 'assignmetro'.")
            else:
                print("No tracks have an assigned output port. Use 'assign' command first.")
            return

        for name in required_ports:
            found_virtual = False
            for vp in self.virtual_ports:
                if vp.name in name:
                    self.open_ports[name] = vp
                    print(f"Using existing virtual port: {name}")
                    found_virtual = True
                    break
            if not found_virtual:
                try:
                    temp_port = mido.open_output(name)
                    self.open_ports[name] = temp_port
                    self.temporary_ports.append(temp_port)
                    print(f"Opened temporary hardware port: {name}")
                except Exception as e:
                    print(f"Error opening hardware port '{name}': {e}")
                    for p in self.temporary_ports:
                        p.close()
                    self.temporary_ports = []
                    self.open_ports.clear()
                    return

        self._stop_event.clear()
        self._run_event.set()
        self.playback_state = "playing"
        self.playback_thread = threading.Thread(
            target=self._play_thread,
            kwargs={'start_measure': start_measure, 'end_measure': end_measure, 'loop': loop}
        )
        self.playback_thread.start()

    def pause(self):
        if self.playback_state == "stopped":
            print("Nothing to pause.")
            return
        if self.playback_state == "playing":
            self._all_notes_off()
            self._run_event.clear()
            self.playback_state = "paused"
            print("Playback paused.")
        elif self.playback_state == "paused":
            self._run_event.set()
            self.playback_state = "playing"
            print("Resuming playback...")

    def stop(self):
        if self.playback_state == "stopped":
            print("Already stopped.")
            return
        self._all_notes_off()
        self._stop_event.set()
        if self.playback_state == "paused":
            self.playback_state = "playing" # Set to playing to allow thread to exit wait
            self._run_event.set()
        if self.playback_thread:
            self.playback_thread.join()
        self.playback_state = "stopped"
        print("Playback stopped.")

    def restart(self):
        """Restarts playback from the beginning."""
        if self.playback_state != "stopped":
            self.stop()
        self.play()
