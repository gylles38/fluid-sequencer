from .midi_export import export_to_midi
from .midi_import import import_song
from .models import AnyTrack, AudioTrack, AutomationTrack, AutomationPoint, CCMessage, Event, MidiTrack, Note, Song, MidiMapping
from .terminal_input import cancellable_input, UserInputCancelled
from copy import deepcopy
from dataclasses import dataclass, asdict, is_dataclass, fields
import json
import math
import mido
import jack
from mido import get_input_names, get_output_names, open_output # type: ignore
import numpy as np
from pydub import AudioSegment
import os
import signal
import queue
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
        if isinstance(o, (Song, MidiTrack, AudioTrack, AutomationTrack, Event, Note, CCMessage, AutomationPoint, MidiMapping)):
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
        elif type_name == 'AutomationTrack':
            return AutomationTrack(**d)
        elif type_name == 'Event':
            return Event(**d)
        elif type_name == 'Note':
            return Note(**d)
        elif type_name == 'CCMessage':
            return CCMessage(**d)
        elif type_name == 'AutomationPoint':
            return AutomationPoint(**d)
        elif type_name == 'MidiMapping':
            return MidiMapping(**d)
    return d


class JackManager:
    def __init__(self, sequencer: 'Sequencer'):
        self.sequencer = sequencer
        self.jack_client = None
        self.is_running = False
        self.last_beat = 0.0
        self.open_ports = {}
        self.next_event_indices = []
        self._active_notes = {} # key: (track_idx, note_pitch), value: end_beat
        self._metronome_notes_to_turn_off = []
        self.active_audio_processes: List[ActiveAudioProcess] = []
        self.process_lock = threading.Lock()
        self._sync_thread = None
        self._sync_stop_event = threading.Event()
        self._display_thread = None
        self._display_stop_event = threading.Event()
        self.automation_events = []
        self.next_automation_event_index = 0

    def _display_loop(self):
        """A loop in a separate thread to display the current transport position."""
        last_pos_str = ""
        while not self._display_stop_event.is_set():
            try:
                if self.jack_client and self.jack_client.transport_state == jack.ROLLING:
                    # Display the sequencer's internal beat counter, which drives the notes.
                    current_beat = self.last_beat
                    pos_str = self.sequencer._format_beats_to_position(current_beat)

                    if pos_str != last_pos_str:
                        sys.stdout.write(f"\r  {pos_str}  ")
                        sys.stdout.flush()
                        last_pos_str = pos_str
                else:
                    if last_pos_str != "":
                        # Clear the line when transport stops
                        sys.stdout.write("\r" + " " * (len(last_pos_str) + 4) + "\r")
                        sys.stdout.flush()
                        last_pos_str = ""

            except jack.JackError:
                break
            except Exception as e:
                print(f"\nError in display loop: {e}", file=sys.stderr)
                break

            time.sleep(0.05)

    def _prepare_automation_events(self):
        """Generates and sorts all automation events for the song."""
        self.automation_events.clear()
        for track in self.sequencer.song.tracks:
            if isinstance(track, AutomationTrack):
                # Only process automation for tracks that should be audible
                target_track_index = track.target_track_index
                if 0 <= target_track_index < len(self.sequencer.song.tracks):
                    target_track = self.sequencer.song.tracks[target_track_index]
                    is_any_track_soloed = any(t.is_solo for t in self.sequencer.song.tracks if hasattr(t, 'is_solo'))

                    # An automation track itself can be muted/soloed
                    auto_track_should_play = (not hasattr(track, 'is_solo') or track.is_solo or not is_any_track_soloed) and \
                                             (not hasattr(track, 'is_muted') or not track.is_muted)

                    # The target track can also be muted/soloed
                    target_track_should_play = (not hasattr(target_track, 'is_solo') or target_track.is_solo or not is_any_track_soloed) and \
                                               (not hasattr(target_track, 'is_muted') or not target_track.is_muted)

                    if auto_track_should_play and target_track_should_play:
                        generated = self.sequencer._generate_automation_events(track)
                        self.automation_events.extend(generated)

        self.automation_events.sort(key=lambda e: e['time'])
        # print(f"Prepared {len(self.automation_events)} automation events.")


    def start(self):
        if self.is_running:
            print("JACK client is already running.")
            return

        try:
            self.jack_client = jack.Client(f"{self.sequencer.song.name}-sequencer")

            # --- MIDI Port Setup ---
            self.open_ports.clear()
            required_ports = {track.output_port_name for track in self.sequencer.song.tracks if isinstance(track, MidiTrack) and track.output_port_name}
            if self.sequencer.song.metronome_enabled and self.sequencer.song.metronome_port_name:
                required_ports.add(self.sequencer.song.metronome_port_name)

            for name in required_ports:
                vp = next((p for p in self.sequencer.virtual_ports if p.name == name), None)
                if vp:
                    self.open_ports[name] = vp
                else:
                    try:
                        self.open_ports[name] = mido.open_output(name)
                    except Exception as e:
                        print(f"Could not open MIDI port '{name}': {e}")

            # --- Audio Track Setup ---
            with self.process_lock:
                self.active_audio_processes.clear()
                for i, track in enumerate(self.sequencer.song.tracks):
                    if isinstance(track, AudioTrack):
                        self._launch_audio_track_player(track, i)

            # --- Automation Setup ---
            self._prepare_automation_events()

            self.jack_client.set_process_callback(self._process_callback)
            self.jack_client.set_timebase_callback(self._time_callback)
            self.jack_client.activate()
            self.is_running = True

            # --- Initial Transport Sync ---
            state, pos_struct = self.jack_client.transport_query_struct()
            pos_dict = jack.position2dict(pos_struct)
            self.sequencer.song.tempo = pos_dict.get('beats_per_minute', self.sequencer.song.tempo)

            frame = pos_dict.get('frame', 0)
            samplerate = self.jack_client.samplerate
            beats_per_second = self.sequencer.song.tempo / 60.0

            if samplerate > 0 and beats_per_second > 0:
                initial_beat = (frame / samplerate) * beats_per_second
                self._sync_playhead_to_beat(initial_beat)
            else:
                self._sync_playhead_to_beat(0.0)

            # Start sync thread
            self._sync_stop_event.clear()
            self._sync_thread = threading.Thread(target=self._mpv_sync_loop)
            self._sync_thread.daemon = True
            self._sync_thread.start()

            # Start display thread
            self._display_stop_event.clear()
            self._display_thread = threading.Thread(target=self._display_loop)
            self._display_thread.daemon = True
            self._display_thread.start()

            print("JACK client started and activated.")
        except jack.JackError as e:
            print(f"Error starting JACK client: {e}")
            if self.jack_client:
                self.jack_client.close()
            self.jack_client = None

    def stop(self):
        if not self.is_running or not self.jack_client:
            return

        self._sync_stop_event.set()
        if self._sync_thread:
            self._sync_thread.join(timeout=1.0)
        self._sync_thread = None

        self._display_stop_event.set()
        if self._display_thread:
            self._display_thread.join(timeout=1.0)
        self._display_thread = None

        self._shutdown_audio_processes()
        self.jack_client.deactivate()
        self.jack_client.close()
        self.jack_client = None
        self.is_running = False

        for port in self.open_ports.values():
            is_virtual = any(vp.name == port.name for vp in self.sequencer.virtual_ports)
            if not is_virtual and not port.closed:
                port.close()
        self.open_ports.clear()
        print("JACK client stopped.")

    def _send_ipc_command(self, socket_path, command_data):
        try:
            if sys.platform == "win32":
                # On Windows, use named pipes. The path needs to be formatted specially.
                pipe_name = r'\\.\pipe\\' + os.path.basename(socket_path)
                # This is a simplified implementation; robust named pipe communication
                # might require more complex handling (e.g., using pywin32).
                # For now, we attempt a simple write.
                with open(pipe_name, 'w') as pipe:
                    pipe.write(json.dumps(command_data) + '\n')
            else:
                # On Unix-like systems, use Unix domain sockets.
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(0.1)  # Don't block for too long
                    s.connect(socket_path)
                    s.sendall(json.dumps(command_data).encode('utf-8') + b'\n')
        except (socket.timeout, ConnectionRefusedError, FileNotFoundError, BrokenPipeError):
            # These errors are expected if mpv is not ready or has been closed.
            pass
        except Exception as e:
            # Log other, unexpected errors.
            print(f"Error sending IPC command to {socket_path}: {e}", file=sys.stderr)

    def _mpv_sync_loop(self):
        """A loop in a separate thread to keep mpv instances synced with JACK transport."""
        time.sleep(0.5)  # Give mpv processes time to start and create their sockets

        was_rolling = False
        while not self._sync_stop_event.is_set():
            if not self.jack_client:
                time.sleep(0.1)
                continue

            try:
                is_rolling = self.jack_client.transport_state == jack.ROLLING

                if is_rolling != was_rolling:
                    with self.process_lock:
                        for ap in self.active_audio_processes:
                            command = {"command": ["set", "pause", not is_rolling]}
                            self._send_ipc_command(ap.socket_path, command)
                    was_rolling = is_rolling

                if is_rolling:
                    _, pos_struct = self.jack_client.transport_query_struct()
                    pos = jack.position2dict(pos_struct)

                    bar = pos.get('bar', 1)
                    beat = pos.get('beat', 1)
                    tick = pos.get('tick', 0)
                    ticks_per_beat = pos.get('ticks_per_beat', self.sequencer.song.ticks_per_beat)
                    beats_per_bar = pos.get('beats_per_bar', self.sequencer.song.time_signature_numerator)

                    current_beat = (bar - 1) * beats_per_bar + (beat - 1) + (tick / ticks_per_beat)

                    with self.process_lock:
                        for ap in self.active_audio_processes:
                            track = self.sequencer.song.tracks[ap.track_index]
                            if isinstance(track, AudioTrack):
                                beats_per_second = self.sequencer.song.tempo / 60.0
                                if beats_per_second > 0:
                                    mpv_time = (current_beat - track.start_time) / beats_per_second

                                    if mpv_time >= 0:
                                        command = {"command": ["set", "time-pos", mpv_time]}
                                        self._send_ipc_command(ap.socket_path, command)
            except jack.JackError:
                # This can happen if the client is shut down while we're in the loop
                break
            except Exception as e:
                print(f"Error in mpv sync loop: {e}", file=sys.stderr)

            time.sleep(0.1)  # Sync every 100ms

    def _launch_audio_track_player(self, track: AudioTrack, track_index: int):
        import shlex

        # Create a unique socket path for IPC
        socket_dir = tempfile.gettempdir()
        # Use a consistent naming scheme for sockets
        socket_filename = f"mpv-socket-{os.getpid()}-{track_index}"
        socket_path = os.path.join(socket_dir, socket_filename)

        # Clean up old socket if it exists
        if sys.platform != "win32" and os.path.exists(socket_path):
            os.unlink(socket_path)

        command = shlex.split(self.sequencer.audio_player_command)
        command.extend([
            f"--input-ipc-server={socket_path}",
            "--pause",  # Start paused, sync loop will control playback
            track.filepath
        ])

        kwargs = {
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL
        }
        if sys.platform != "win32":
            kwargs['preexec_fn'] = os.setsid

        try:
            process = subprocess.Popen(command, **kwargs)

            active_process_info = ActiveAudioProcess(
                process=process,
                socket_path=socket_path,
                track_index=track_index
            )
            self.active_audio_processes.append(active_process_info)
            print(f"Launched mpv for track {track_index} with IPC socket: {socket_path}")
        except Exception as e:
            print(f"Error launching mpv for track {track_index}: {e}")
            # Clean up the socket file if process fails to start
            if sys.platform != "win32" and os.path.exists(socket_path):
                os.unlink(socket_path)

    def _shutdown_audio_processes(self):
        """Stops all active audio subprocesses."""
        with self.process_lock:
            for ap in self.active_audio_processes:
                try:
                    if ap.process.poll() is None:
                        ap.process.terminate()
                        ap.process.wait(timeout=1.0)
                except (subprocess.TimeoutExpired, Exception):
                    if ap.process.poll() is None:
                        ap.process.kill()
                # Clean up socket file
                try:
                    if sys.platform != "win32" and ap.socket_path and os.path.exists(ap.socket_path):
                        os.unlink(ap.socket_path)
                except Exception as e:
                    print(f"Error removing socket file {ap.socket_path}: {e}", file=sys.stderr)
            self.active_audio_processes.clear()

    def _sync_playhead_to_beat(self, beat_pos: float):
        """Sets the internal playhead to a specific beat and updates event indices."""
        self.last_beat = beat_pos

        # Reset and find the correct starting index for MIDI events
        num_tracks = len(self.sequencer.song.tracks)
        self.next_event_indices = [0] * num_tracks
        self._active_notes.clear() # Clear any lingering notes from previous state

        for i, track in enumerate(self.sequencer.song.tracks):
            if isinstance(track, MidiTrack):
                for j, event in enumerate(track.events):
                    if event.start_time >= self.last_beat:
                        self.next_event_indices[i] = j
                        break
                else:
                    self.next_event_indices[i] = len(track.events)

        # Reset and find the correct starting index for automation events
        self.next_automation_event_index = 0
        for i, event in enumerate(self.automation_events):
            if event['time'] >= self.last_beat:
                self.next_automation_event_index = i
                break
        else: # All events are in the past
            self.next_automation_event_index = len(self.automation_events)

    def _time_callback(self, state, blocksize, pos, new_pos):
        if new_pos:
            pos_dict = jack.position2dict(pos)
            self.sequencer.song.tempo = pos_dict.get('beats_per_minute', self.sequencer.song.tempo)

            frame = pos_dict.get('frame', 0)
            samplerate = self.jack_client.samplerate
            beats_per_second = self.sequencer.song.tempo / 60.0

            if samplerate > 0 and beats_per_second > 0:
                current_beat = (frame / samplerate) * beats_per_second
                self._sync_playhead_to_beat(current_beat)
            else:
                self._sync_playhead_to_beat(0.0)


    def _process_callback(self, frames: int):
        try:
            # --- Turn off metronome notes from previous block ---
            if self.sequencer.song.metronome_enabled and self.sequencer.song.metronome_port_name in self.open_ports:
                port = self.open_ports[self.sequencer.song.metronome_port_name]
                for note_off_msg in self._metronome_notes_to_turn_off:
                    port.send(note_off_msg)
                self._metronome_notes_to_turn_off.clear()

            if not self.jack_client or self.jack_client.transport_state != jack.ROLLING:
                # When transport is stopped, send note-offs for any lingering notes
                if self._active_notes:
                    for (track_idx, pitch), end_beat in list(self._active_notes.items()):
                        track = self.sequencer.song.tracks[track_idx]
                        if isinstance(track, MidiTrack) and track.output_port_name in self.open_ports:
                            port = self.open_ports[track.output_port_name]
                            port.send(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0))
                    self._active_notes.clear()
                return

            state, pos_struct = self.jack_client.transport_query_struct()
            pos = jack.position2dict(pos_struct)

            samplerate = self.jack_client.samplerate
            tempo = self.sequencer.song.tempo
            beats_per_second = tempo / 60.0

            # Beat at the start of the current processing block
            start_beat_of_block = self.last_beat
            # Beat at the end of the current processing block
            end_beat_of_block = start_beat_of_block + (frames / samplerate) * beats_per_second

            # --- Send Note Offs for notes that ended in this block ---
            for (track_idx, pitch), end_beat in list(self._active_notes.items()):
                if start_beat_of_block <= end_beat < end_beat_of_block:
                    track = self.sequencer.song.tracks[track_idx]
                    if isinstance(track, MidiTrack) and track.output_port_name in self.open_ports:
                        port = self.open_ports[track.output_port_name]
                        port.send(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0))
                    del self._active_notes[(track_idx, pitch)]

            # --- Send Note Ons for notes starting in this block ---
            for i, track in enumerate(self.sequencer.song.tracks):
                if not isinstance(track, MidiTrack) or not track.output_port_name in self.open_ports:
                    continue

                port = self.open_ports[track.output_port_name]

                # Ensure next_event_indices is initialized
                if i >= len(self.next_event_indices):
                    self.next_event_indices.extend([0] * (i - len(self.next_event_indices) + 1))

                # Iterate through events for this track
                while self.next_event_indices[i] < len(track.events):
                    event = track.events[self.next_event_indices[i]]

                    if start_beat_of_block <= event.start_time < end_beat_of_block:
                        # This event starts in the current block
                        for note in event.notes:
                            note_on_msg = mido.Message('note_on', channel=track.channel, note=note.pitch, velocity=int(note.velocity * track.velocity))
                            port.send(note_on_msg)

                            note_end_beat = event.start_time + note.duration
                            self._active_notes[(i, note.pitch)] = note_end_beat

                        for cc in event.cc_messages:
                            cc_msg = mido.Message('control_change', channel=track.channel, control=cc.control, value=cc.value)
                            port.send(cc_msg)

                        self.next_event_indices[i] += 1
                    elif event.start_time >= end_beat_of_block:
                        # This event is in the future, so we can stop checking for this track
                        break
                    else:
                        # This event was in the past, so we skip it (should have been caught by a seek)
                        self.next_event_indices[i] += 1

            # --- Process Automation Events ---
            while self.next_automation_event_index < len(self.automation_events):
                event = self.automation_events[self.next_automation_event_index]
                event_time = event['time']

                if start_beat_of_block <= event_time < end_beat_of_block:
                    target_track_index = event['target_track_index']
                    if not 0 <= target_track_index < len(self.sequencer.song.tracks):
                        self.next_automation_event_index += 1
                        continue

                    target_track = self.sequencer.song.tracks[target_track_index]
                    param_config = event['param_config']
                    value = event['value']
                    param_name = event['parameter'].lower()

                    if param_config['type'] == 'midi_cc':
                        if isinstance(target_track, MidiTrack) and target_track.output_port_name in self.open_ports:
                            port = self.open_ports[target_track.output_port_name]

                            midi_value = 0
                            if param_name == 'vol': # volume
                                midi_value = int(value * 127)
                            elif param_name == 'pan': # pan
                                midi_value = int((value + 1.0) / 2.0 * 127)
                            else: # other CCs are assumed to be 0-127
                                midi_value = int(value)

                            # Ensure value is within MIDI range
                            midi_value = max(0, min(127, midi_value))

                            msg = mido.Message('control_change', channel=target_track.channel, control=param_config['control'], value=midi_value)
                            port.send(msg)

                    elif param_config['type'] == 'program_change':
                        if isinstance(target_track, MidiTrack) and target_track.output_port_name in self.open_ports:
                            port = self.open_ports[target_track.output_port_name]
                            program_value = max(0, min(127, int(value)))
                            msg = mido.Message('program_change', channel=target_track.channel, program=program_value)
                            port.send(msg)

                    elif param_config['type'] == 'velocity_multiplier':
                        if isinstance(target_track, MidiTrack):
                            target_track.velocity = value

                    self.next_automation_event_index += 1
                elif event_time >= end_beat_of_block:
                    break
                else: # event in the past
                    self.next_automation_event_index += 1

            # --- Play Range Stop Handling ---
            if self.sequencer.play_range_enabled and end_beat_of_block >= self.sequencer.play_range_end_beat:
                if start_beat_of_block < self.sequencer.play_range_end_beat:
                    self.jack_client.transport_stop()
                    self.sequencer.play_range_enabled = False # Disable after triggering

            # --- Loop Handling ---
            if self.sequencer.loop_enabled and end_beat_of_block >= self.sequencer.loop_end_beat:
                # Check if the start of the block was before the end point, to avoid re-triggering on every block after the end.
                if start_beat_of_block < self.sequencer.loop_end_beat:
                    beats_per_second = self.sequencer.song.tempo / 60.0
                    samplerate = self.jack_client.samplerate

                    if beats_per_second > 0 and samplerate > 0:
                        # Convert loop start beat to target frame
                        target_frame = int((self.sequencer.loop_start_beat / beats_per_second) * samplerate)

                        # Convert loop start beat to target frame
                        target_frame = int((self.sequencer.loop_start_beat / beats_per_second) * samplerate)

                        # Query for a valid position object and set only the frame.
                        # This is simpler and more robust than calculating bar/beat/tick.
                        _ , pos = self.jack_client.transport_query_struct()
                        pos.frame = target_frame

                        self.jack_client.transport_reposition_struct(pos)

            # --- Metronome Click Generation ---
            if self.sequencer.song.metronome_enabled and self.sequencer.song.metronome_port_name in self.open_ports:
                port = self.open_ports[self.sequencer.song.metronome_port_name]

                # Find the next integer beat to check
                beat_to_check = math.floor(start_beat_of_block) + 1

                while beat_to_check < end_beat_of_block:
                    beats_per_measure = self.sequencer.song.time_signature_numerator
                    # 0-indexed beat for modulo operation
                    is_downbeat = ((beat_to_check - 1) % beats_per_measure) == 0 if beats_per_measure > 0 else beat_to_check == 1

                    pitch = self.sequencer.metronome_pitch_downbeat if is_downbeat else self.sequencer.metronome_pitch_beat

                    note_on = mido.Message('note_on', channel=self.sequencer.metronome_channel, note=pitch, velocity=100)
                    note_off = mido.Message('note_off', channel=self.sequencer.metronome_channel, note=pitch, velocity=0)

                    port.send(note_on)
                    self._metronome_notes_to_turn_off.append(note_off)

                    beat_to_check += 1

            self.last_beat = end_beat_of_block
        except Exception as e:
            # It's critical not to let exceptions escape the callback
            print(f"\nError in JACK process callback: {e}")


class Sequencer:
    DEFAULT_AUDIO_PLAYER_COMMAND = "mpv --really-quiet --no-video --idle"

    def __init__(self, tempo: int = 120):
        self.song = Song(name="New Song", tempo=tempo)
        self.playback_state = "stopped"
        self.jack_manager = JackManager(self)
        self.midi_listener_thread = None
        self._midi_listener_stop_event = threading.Event()
        self.control_port_name: Optional[str] = None
        self.open_ports = {}
        self.virtual_ports = []
        self.temporary_ports = []
        self.audio_player_command: str = self.DEFAULT_AUDIO_PLAYER_COMMAND

        self.last_start_beat = 0.0
        self.recording_thread = None
        self.is_recording = False
        self._stop_event = threading.Event()

        # Loop settings
        self.loop_enabled = False
        self.loop_start_beat = 0.0
        self.loop_end_beat = 0.0

        # Play range settings
        self.play_range_enabled = False
        self.play_range_start_beat = 0.0
        self.play_range_end_beat = 0.0

        # Metronome settings
        self.metronome_channel = 9  # Channel 10 (0-indexed)
        self.metronome_pitch_downbeat = 76  # High Wood Block
        self.metronome_pitch_beat = 77  # Low Wood Block

        self.last_record_settings = None
        self.is_dirty = False
        self.last_project_basename = None

    def _all_notes_off(self):
        for port in self.open_ports.values():
            if port and not port.closed:
                for channel in range(16):
                    port.send(mido.Message('control_change', channel=channel, control=123, value=0))
        # print("Sent all notes off to all open ports.")

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
        self.is_dirty = True
        print(f"Tempo set to {self.song.tempo} BPM.")

    def set_time_signature(self, numerator: int, denominator: int):
        # Basic validation
        if not (numerator > 0 and denominator > 0 and (denominator & (denominator - 1) == 0)):
            print("Error: Invalid time signature. Denominator must be a power of 2.")
            return
        self.song.time_signature_numerator = numerator
        self.song.time_signature_denominator = denominator
        self.is_dirty = True
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
        self.is_dirty = True

    def add_automation_track(self, name: str, target_track_index: int):
        """Adds a new automation track to the song."""
        if not 0 <= target_track_index < len(self.song.tracks):
            print("Error: Invalid target track index.")
            return

        target_track = self.song.tracks[target_track_index]
        if isinstance(target_track, AutomationTrack):
            print("Error: Automation tracks cannot target other automation tracks.")
            return

        track = AutomationTrack(name=name, target_track_index=target_track_index)
        self.song.add_track(track)
        self.is_dirty = True
        print(f"Automation track '{name}' added, targeting track {target_track_index} ('{target_track.name}').")

    def add_automation_point(self, track_index: int, position_str: str, parameter: str, value: float, curve: str):
        """Adds an automation point to a specific automation track."""
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        track = self.song.tracks[track_index]
        if not isinstance(track, AutomationTrack):
            print("Error: Automation points can only be added to automation tracks.")
            return

        start_beat = self.parse_position_to_beats(position_str)
        if start_beat is None:
            return

        try:
            point = AutomationPoint(start_time=start_beat, parameter=parameter, value=value, curve=curve)
            track.add_point(point)
            self.is_dirty = True
            print(f"Added '{parameter}' automation point to track '{track.name}' at position {position_str}.")
        except ValueError as e:
            print(f"Error: {e}")

    def delete_track(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return False
        track_name = self.song.tracks[track_index].name
        self.song.tracks.pop(track_index)
        self.is_dirty = True
        print(f"Track '{track_name}' deleted.")
        return True

    def add_cc_event(self, track_index: int, position_str: str, control: int, value: int):
        """Adds a CC event to a specific track at a given position."""
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            print("Error: CC events can only be added to MIDI tracks.")
            return

        start_beat = self.parse_position_to_beats(position_str)
        if start_beat is None:
            return # Error is printed by parse_position_to_beats

        try:
            # Create the CC message, which will validate its own values
            new_cc = CCMessage(control=control, value=value)
        except ValueError as e:
            print(f"Error: Invalid CC value. {e}")
            return

        # Check if an event already exists at this exact start time
        existing_event = None
        for event in track.events:
            if math.isclose(event.start_time, start_beat):
                existing_event = event
                break

        if existing_event:
            # Add the CC message to the existing event
            existing_event.cc_messages.append(new_cc)
            print(f"Added CC to existing event at position {position_str} on track '{track.name}'.")
        else:
            # Create a new event with this CC message and add it to the track
            new_event = Event(start_time=start_beat, cc_messages=[new_cc])
            track.add_event(new_event) # add_event handles sorting
            print(f"Added new CC event at position {position_str} on track '{track.name}'.")
        self.is_dirty = True

    def erase_track(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        track = self.song.tracks[track_index]

        if not isinstance(track, (MidiTrack, AutomationTrack)):
            print("Error: Erasing is only supported for MIDI and Automation tracks.")
            return

        try:
            start_pos_str = cancellable_input(f"Erase from position on track '{track.name}' (measure:beat) [default: 1:1]: ").strip()
            start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if start_beat is None: return

            end_pos_str = cancellable_input(f"Erase up to position on track '{track.name}' (measure:beat) [default: end of track]: ").strip()
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
        except UserInputCancelled:
            print("\nErase cancelled.")
            return

        if isinstance(track, MidiTrack):
            # --- Get what to erase ---
            erase_choice = "all"
            erase_options = {
                "a": "all",
                "n": "notes",
                "c": "cc",
            }
            while True:
                choice_str = cancellable_input(
                    "What do you want to erase? (a)ll, (n)otes, (c)c: "
                ).lower()
                if choice_str in erase_options:
                    erase_choice = erase_options[choice_str]
                    break
                else:
                    print("Invalid choice. Please try again.")

            # --- Confirmation ---
            end_str_display = (
                f"up to {end_pos_str}" if end_pos_str else "to the end of the track"
            )
            confirm_message = f"Erase {erase_choice} from {start_pos_str} {end_str_display} on track '{track.name}'? [y/N] "
            if cancellable_input(confirm_message).lower() != "y":
                print("Erase cancelled.")
                return

            # --- Process events ---
            final_events = []
            events_to_shift = []
            modified_count = 0
            deleted_count = 0

            for event in list(track.events): # Iterate over a copy
                if start_beat <= event.start_time < end_beat:
                    # This event is within the erase range
                    event_modified = False
                    if erase_choice == "all" or erase_choice == "notes":
                        if event.notes:
                            event.notes.clear()
                            event_modified = True
                    if erase_choice == "all" or erase_choice == "cc":
                        if event.cc_messages:
                            event.cc_messages.clear()
                            event_modified = True

                    if event_modified:
                        modified_count += 1

                    # If the event is now empty, don't keep it.
                    is_empty = not event.notes and not event.cc_messages
                    if not is_empty:
                        final_events.append(event)
                    else:
                        deleted_count += 1
                elif event.start_time >= end_beat:
                    events_to_shift.append(event)
                else:
                    # This event is before the range, so keep it
                    final_events.append(event)

            # --- Handle shifting ---
            shift_confirmed = False
            if events_to_shift:
                shift_choice = cancellable_input(f"Shift subsequent {len(events_to_shift)} event(s) to start after the erased section? [y/N]: ").lower()
                if shift_choice == 'y':
                    shift_offset = end_beat - start_beat
                    for event in events_to_shift:
                        event.start_time -= shift_offset
                    shift_confirmed = True

            final_events.extend(events_to_shift)
            track.events = final_events
            track.events.sort(key=lambda e: e.start_time)

            # --- Report results ---
            report = [f"Modified {modified_count} event(s)"]
            if shift_confirmed:
                report.append(f"shifted {len(events_to_shift)} event(s)")

            if modified_count > 0 or shift_confirmed:
                 self.is_dirty = True
                 print(f"Operation complete: {', '.join(report)} from track '{track.name}'.")
            else:
                 print("No events were modified or shifted.")

        elif isinstance(track, AutomationTrack):
            # --- Get what to erase ---
            params_in_range = sorted(list({p.parameter for p in track.points if start_beat <= p.start_time < end_beat}))
            if not params_in_range:
                print("No automation points found in the specified range.")
                return

            prompt = "What do you want to erase? (all"
            for p in params_in_range:
                prompt += f", {p}"
            prompt += "): "

            erase_choice = "all"
            while True:
                choice_str = cancellable_input(prompt).lower()
                if choice_str == "all" or choice_str in params_in_range:
                    erase_choice = choice_str
                    break
                else:
                    print("Invalid choice. Please try again.")

            # --- Confirmation ---
            end_str_display = (f"up to {end_pos_str}" if end_pos_str else "to the end of the track")
            confirm_message = f"Erase {erase_choice} points from {start_pos_str} {end_str_display} on track '{track.name}'? [y/N] "
            if cancellable_input(confirm_message).lower() != "y":
                print("Erase cancelled.")
                return

            # --- Process points ---
            points_to_keep = []
            deleted_count = 0
            for point in track.points:
                is_in_range = start_beat <= point.start_time < end_beat
                should_delete = is_in_range and (erase_choice == "all" or point.parameter == erase_choice)

                if not should_delete:
                    points_to_keep.append(point)
                else:
                    deleted_count += 1

            if deleted_count > 0:
                track.points = points_to_keep
                self.is_dirty = True
                print(f"Erased {deleted_count} point(s) from track '{track.name}'.")
            else:
                print("No points were erased.")

    def rename_track(self, track_index: int, new_name: str):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        old_name = self.song.tracks[track_index].name
        self.song.tracks[track_index].name = new_name
        self.is_dirty = True
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
            start_pos_str = cancellable_input(f"Move from position on track '{source_track.name}' (measure:beat) [default: 1:1]: ").strip()
            source_start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if source_start_beat is None: return

            end_pos_str = cancellable_input(f"Move up to position on track '{source_track.name}' (measure:beat): ").strip()
            source_end_beat = self.parse_position_to_beats(end_pos_str)
            if source_end_beat is None: return

            if source_end_beat <= source_start_beat:
                print("Error: End position must be after the start position.")
                return

            # Get destination
            dest_track_idx_str = cancellable_input(f"Move to destination track index (default: {track_index}, '{source_track.name}'): ").strip()
            dest_track_idx = track_index if dest_track_idx_str == "" else int(dest_track_idx_str)

            if not 0 <= dest_track_idx < len(self.song.tracks):
                print("Error: Invalid destination track index.")
                return

            dest_track = self.song.tracks[dest_track_idx]
            if not isinstance(dest_track, MidiTrack):
                print("Error: Destination track must be a MIDI track.")
                return

            dest_pos_str = cancellable_input(f"Move to destination position on track '{dest_track.name}' (measure:beat) [default: 1:1]: ").strip()
            destination_start_beat = self.parse_position_to_beats(dest_pos_str, default="1:1")
            if destination_start_beat is None: return

        except (ValueError, UserInputCancelled):
            print("\nMove cancelled.")
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
        if cancellable_input(confirm_message).lower() != 'y':
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
                choice = cancellable_input("Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
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
            self.is_dirty = True
            print(f"Operation complete: {', '.join(report)}.")

    def copy_track_section(self):
        if not self.song.tracks:
            print("No tracks to copy from.")
            return

        try:
            # Get source track
            source_track_idx = int(cancellable_input("Copy from track index: ").strip())
            if not 0 <= source_track_idx < len(self.song.tracks):
                print("Error: Invalid source track index.")
                return
            source_track = self.song.tracks[source_track_idx]
            if not isinstance(source_track, MidiTrack):
                print("Error: Copying events is only supported for MIDI tracks.")
                return

            # Get source range
            start_pos_str = cancellable_input(f"Copy from position on track '{source_track.name}' (measure:beat) [default: 1:1]: ").strip()
            source_start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if source_start_beat is None: return

            end_pos_str = cancellable_input(f"Copy up to position on track '{source_track.name}' (measure:beat): ").strip()
            source_end_beat = self.parse_position_to_beats(end_pos_str)
            if source_end_beat is None: return

            if source_end_beat <= source_start_beat:
                print("Error: End position must be after the start position.")
                return

            # Get destination
            dest_track_idx = int(cancellable_input(f"Copy to destination track index (default: {source_track_idx}): ").strip() or str(source_track_idx))
            if not 0 <= dest_track_idx < len(self.song.tracks):
                print("Error: Invalid destination track index.")
                return
            dest_track = self.song.tracks[dest_track_idx]
            if not isinstance(dest_track, MidiTrack):
                print("Error: Destination track must be a MIDI track.")
                return

            dest_pos_str = cancellable_input(f"Copy to destination position on track '{dest_track.name}' (measure:beat) [default: 1:1]: ").strip()
            destination_start_beat = self.parse_position_to_beats(dest_pos_str, default="1:1")
            if destination_start_beat is None: return

        except (ValueError, UserInputCancelled):
            print("\nCopy cancelled.")
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
        if cancellable_input(confirm_message).lower() != 'y':
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
                choice = cancellable_input("Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
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
            self.is_dirty = True
            print(f"Operation complete: {', '.join(report)}.")

    def transpose_track_section(self):
        if not self.song.tracks:
            print("No tracks to transpose.")
            return

        try:
            track_idx = int(cancellable_input("Transpose track index: ").strip())
            if not 0 <= track_idx < len(self.song.tracks):
                print("Error: Invalid track index.")
                return
            track = self.song.tracks[track_idx]
            if not isinstance(track, MidiTrack):
                print("Error: Transposing is only supported for MIDI tracks.")
                return

            start_pos_str = cancellable_input(f"Transpose from position on track '{track.name}' (measure:beat) [default: 1:1]: ").strip()
            start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if start_beat is None: return

            end_pos_str = cancellable_input(f"Transpose up to position on track '{track.name}' (measure:beat) [default: end of track]: ").strip()
            if end_pos_str == "":
                end_beat = float('inf')
            else:
                end_beat = self.parse_position_to_beats(end_pos_str)
                if end_beat is None: return

            if end_beat <= start_beat:
                print("Error: End position must be after the start position.")
                return

            transpose_value = int(cancellable_input("Transpose by how many semitones (e.g., 12 for up, -12 for down): ").strip())
            if not -127 <= transpose_value <= 127:
                print("Error: Transposition value must be between -127 and 127.")
                return

        except (ValueError, UserInputCancelled):
            print("\nTranspose cancelled.")
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
        if cancellable_input(confirm_message).lower() != 'y':
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

        self.is_dirty = True
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
        self.is_dirty = True
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
            self.is_dirty = True
        else:
            print(f"Track '{track.name}' has no port assigned.")

    def set_audio_player_command(self, command: str):
        """Sets the command for the external audio player."""
        self.audio_player_command = command
        self.is_dirty = True
        print(f"Audio player command set to: {command}")
        print("Note: The audio filepath will be appended to this command.")

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
        self.is_dirty = True
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
        self.is_dirty = True
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
        self.is_dirty = True
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
        self.is_dirty = True
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

    def set_track_pan(self, track_index: int, pan: float):
        """Sets the pan for a specific audio or MIDI track."""
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        track = self.song.tracks[track_index]
        if not isinstance(track, (AudioTrack, MidiTrack)):
            print("Error: Pan can only be set for audio or MIDI tracks.")
            return

        if not -1.0 <= pan <= 1.0:
            print("Error: Pan must be between -1.0 (left) and 1.0 (right).")
            return

        track.pan = pan
        self.is_dirty = True
        print(f"Pan for track '{track.name}' set to {pan:.2f}.")

        if isinstance(track, AudioTrack):
            # If playback is active, send a live pan change command
            with self.process_lock:
                for ap in self.active_audio_processes:
                    if ap.track_index == track_index:
                        self._send_ipc_command(
                            ap.socket_path, {"command": ["set_property", "pan", pan]}
                        )
                        break
        elif isinstance(track, MidiTrack):
            # If playback is active, send a live CC#10 message
            if self.playback_state == "playing" and track.output_port_name:
                port = self.open_ports.get(track.output_port_name)
                if port:
                    # Map pan from -1.0..1.0 to 0..127
                    midi_pan = int((pan + 1.0) / 2.0 * 127)
                    port.send(
                        mido.Message(
                            "control_change", channel=track.channel, control=10, value=midi_pan
                        )
                    )

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
        self.is_dirty = True
        print(f"Velocity for track '{track.name}' set to {velocity:.2f}.")

    def toggle_mute(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        track = self.song.tracks[track_index]
        track.is_muted = not track.is_muted
        status = "Muted" if track.is_muted else "Unmuted"
        self.is_dirty = True
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
                    # Starting a new process mid-stream is complex and handled by the user restarting playback.
                    # For now, we just print a message.
                    print(f"Track '{track.name}' will start on next playback.")

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
        self.is_dirty = True
        print(f"Track '{target_track.name}' is now {status}.")

        # If playback is active, apply the solo state changes immediately.
        if self.playback_state != "stopped":
            is_any_track_soloed = any(t.is_solo for t in self.song.tracks)

            for i, track in enumerate(self.song.tracks):
                # Determine if the track should be audible
                should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted

                if isinstance(track, AudioTrack):
                    with self.process_lock:
                        active_process = next((p for p in self.active_audio_processes if p.track_index == i), None)
                        if active_process:
                            # Mute if it shouldn't be audible, unmute if it should
                            self._send_ipc_command(
                                active_process.socket_path,
                                {"command": ["set_property", "mute", not should_be_audible]}
                            )
                        elif should_be_audible:
                            # If the track should be playing but isn't, start it.
                            print(f"Starting playback for newly audible track '{track.name}'...")
                            # Starting a new process mid-stream is complex and handled by the user restarting playback.
                            # For now, we just print a message.
                            print(f"Track '{track.name}' will start on next playback.")

                elif isinstance(track, MidiTrack):
                    if not should_be_audible and track.output_port_name:
                        port = self.open_ports.get(track.output_port_name)
                        if port:
                            # Send all notes off for this track's channel
                            port.send(mido.Message('control_change', channel=track.channel, control=123, value=0))

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

    def set_control_port(self, port_name: str):
        """Sets the MIDI input port for control messages and starts listening."""
        if self.midi_listener_thread and self.midi_listener_thread.is_alive():
            print("A control port is already active. Please unset it first.")
            return

        self.control_port_name = port_name
        self._midi_listener_stop_event.clear()
        self.midi_listener_thread = threading.Thread(
            target=self._midi_listener_loop,
            args=(port_name,)
        )
        self.midi_listener_thread.daemon = True
        self.midi_listener_thread.start()
        print(f"Listening for control messages on '{port_name}'.")

    def unset_control_port(self):
        """Stops listening for control messages and closes the port."""
        if not self.midi_listener_thread or not self.midi_listener_thread.is_alive():
            print("No active control port to unset.")
            return

        self._midi_listener_stop_event.set()
        if self.midi_listener_thread:
            self.midi_listener_thread.join(timeout=1.0)
        self.control_port_name = None
        print("Stopped listening for control messages.")

    def _midi_listener_loop(self, port_name: str):
        """
        The main loop for the MIDI control message listener thread.
        This loop handles live parameter changes and, if recording is active,
        triggers the recording of automation points.
        """
        try:
            with mido.open_input(port_name) as inport:
                while not self._midi_listener_stop_event.is_set():
                    for msg in inport.iter_pending():
                        if msg.type == 'control_change':
                            for mapping in self.song.midi_mappings:
                                if mapping.channel == msg.channel and mapping.control == msg.control:
                                    # Always apply the action live for real-time feedback
                                    self._apply_midi_mapping_action(mapping, msg.value)
                                    # If recording, also save it as an automation point
                                    if self.is_recording:
                                        self._record_automation_from_mapping(mapping, msg.value)
                    time.sleep(0.01)
        except Exception as e:
            print(f"\nError in MIDI listener thread for port '{port_name}': {e}")

    def _get_parameter_value_from_cc(self, action: str, cc_value: int) -> float:
        """Converts a MIDI CC value (0-127) to a normalized parameter value."""
        if action == 'volume':
            return cc_value / 127.0
        elif action == 'pan':
            # Convert 0-127 to -1.0 to 1.0
            return (cc_value / 127.0) * 2.0 - 1.0
        # For program changes or other direct value mappings, return the value as-is.
        return float(cc_value)

    def _apply_midi_mapping_action(self, mapping: 'MidiMapping', value: int):
        """Applies the action defined in a MidiMapping."""
        if not 0 <= mapping.track_index < len(self.song.tracks):
            return

        param_value = self._get_parameter_value_from_cc(mapping.action, value)

        if mapping.action == 'volume':
            self.set_track_volume(mapping.track_index, param_value)
        elif mapping.action == 'pan':
            self.set_track_pan(mapping.track_index, param_value)
        elif mapping.action == 'program':
            # Program change expects an integer
            self.set_program(mapping.track_index, int(param_value))

        print(f"\rCC -> Track {mapping.track_index} {mapping.action.capitalize()}: {value}   ", end="")
        sys.stdout.flush()

    def _record_automation_from_mapping(self, mapping: 'MidiMapping', value: int):
        """Records a received CC value as an automation point."""
        auto_track_idx = self._find_or_create_automation_track(mapping.track_index, mapping.action)
        if auto_track_idx is None:
            return

        auto_track = self.song.tracks[auto_track_idx]
        if not isinstance(auto_track, AutomationTrack):
            return # Should not happen

        current_beat = self._get_current_beat()
        point_value = self._get_parameter_value_from_cc(mapping.action, value)

        # Create and add the automation point
        new_point = AutomationPoint(
            start_time=current_beat,
            parameter=mapping.action,
            value=point_value,
            curve='linear' # Linear is a sensible default for recorded automation
        )
        auto_track.add_point(new_point)
        self.is_dirty = True

    def load_song(self, filepath: str):
        try:
            self.song = import_song(filepath)
            self.is_dirty = True
            self.last_project_basename = None
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
                "control_port_name": self.control_port_name,
                "audio_player_command": self.audio_player_command,
            }
            with open(project_filepath, 'w') as f:
                json.dump(project_data, f, indent=4, cls=CustomSongEncoder)
            self.is_dirty = False
            self.last_project_basename = basename
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

            # Restore control port
            self.unset_control_port()
            control_port_name = project_data.get("control_port_name")
            if control_port_name:
                self.set_control_port(control_port_name)

            self.is_dirty = False
            self.last_project_basename = basename
            print(f"Successfully loaded project from '{project_filepath}'")
        except FileNotFoundError:
            print(f"Error: Project file not found at '{project_filepath}'")
        except Exception as e:
            print(f"Error loading project file: {e}")

    def new_project(self):
        """Resets the sequencer to a new, empty project."""
        if self.playback_state != "stopped":
            self.stop()

        self.song = Song(name="New Song", tempo=120)
        self.close_virtual_ports()
        self.virtual_ports = []
        self.unset_control_port()
        self.last_project_basename = None
        self.is_dirty = False
        print("New project created.")

    def list_tracks(self) -> str:
        if not self.song.tracks:
            return "No tracks in the song."
        lines = [f"Song: {self.song.name} | Tempo: {self.song.tempo} BPM | Time Signature: {self.song.time_signature_numerator}/{self.song.time_signature_denominator}"]

        metro_status = "OFF"
        if self.song.metronome_enabled:
            port_info = f" -> Port: {self.song.metronome_port_name}" if self.song.metronome_port_name else " (No port assigned)"
            metro_status = f"ON{port_info}"
        lines.append(f"Metronome: {metro_status}")

        loop_status = "OFF"
        if self.loop_enabled:
            start_pos = self._format_beats_to_position(self.loop_start_beat)
            end_pos = self._format_beats_to_position(self.loop_end_beat)
            loop_status = f"ON ({start_pos} -> {end_pos})"
        lines.append(f"Loop: {loop_status}")

        play_range_status = "OFF"
        if self.play_range_enabled:
            start_pos = self._format_beats_to_position(self.play_range_start_beat)
            end_pos = self._format_beats_to_position(self.play_range_end_beat)
            play_range_status = f"ON ({start_pos} -> {end_pos})"
        lines.append(f"Play Range: {play_range_status}")

        lines.append("=" * 20)
        for i, track in enumerate(self.song.tracks):
            status_info = ""
            if hasattr(track, 'is_muted') and track.is_muted: status_info += " [M]"
            if hasattr(track, 'is_solo') and track.is_solo: status_info += " [S]"

            if isinstance(track, MidiTrack):
                bank_info = ""
                if track.bank_msb is not None: bank_info = f", Bank: {track.bank_msb}:{track.bank_lsb or 0}"
                ch_info = f"Ch: {track.channel + 1}"
                prog_info = f"Prog: {track.instrument + 1}"
                vol_info = f"Vol: {track.volume:.2f}"
                pan_info = f"Pan: {track.pan:.2f}"
                vel_info = f"Vel: {track.velocity:.2f}"
                port_info = f" -> Port: {track.output_port_name}" if track.output_port_name else ""
                lines.append(f"[{i}] {track.name} (MIDI){status_info} ({ch_info}, {prog_info}{bank_info}, {vol_info}, {pan_info}, {vel_info}, {len(track.events)} events){port_info}")
            elif isinstance(track, AudioTrack):
                start_pos_str = self._format_beats_to_position(track.start_time)
                vol_info = f"Vol: {track.volume:.2f}"
                pan_info = f"Pan: {track.pan:.2f}"
                lines.append(f"[{i}] {track.name} (Audio){status_info} (File: {track.filepath}, Starts at: {start_pos_str}, {vol_info}, {pan_info})")
            elif isinstance(track, AutomationTrack):
                target_track_name = "N/A"
                if 0 <= track.target_track_index < len(self.song.tracks):
                    target_track_name = self.song.tracks[track.target_track_index].name
                lines.append(f"[{i}] {track.name} (Automation){status_info} (Target: {track.target_track_index} '{target_track_name}', {len(track.points)} points)")
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
            self.is_dirty = True
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
            self.is_dirty = True
            print(f"Virtual port '{name}' deleted.")
        else:
            print(f"Error: Virtual port '{name}' not found.")

    def _find_or_create_automation_track(self, target_track_index: int, parameter_name: str) -> Optional[int]:
        """
        Finds an existing automation track for a given target track and parameter,
        or creates one if it doesn't exist.
        Returns the index of the automation track, or None if the target is invalid.
        """
        if not 0 <= target_track_index < len(self.song.tracks):
            return None

        target_track = self.song.tracks[target_track_index]
        # A more specific name for the automation track
        new_track_name = f"{target_track.name} {parameter_name.capitalize()} Automation"

        # Search for an existing track with the exact same name and target
        for i, track in enumerate(self.song.tracks):
            if (isinstance(track, AutomationTrack) and
                track.target_track_index == target_track_index and
                track.name == new_track_name):
                return i

        # If no track is found, create a new one
        print(f"\nCreating new automation track: '{new_track_name}'")
        new_track = AutomationTrack(name=new_track_name, target_track_index=target_track_index)
        self.song.add_track(new_track)
        self.is_dirty = True

        # Return the index of the newly created track
        return len(self.song.tracks) - 1

    def _get_current_beat(self) -> float:
        """Gets the current beat from the JACK transport if active."""
        if self.jack_manager and self.jack_manager.is_running and self.jack_manager.jack_client:
            try:
                _, pos_struct = self.jack_manager.jack_client.transport_query_struct()
                pos = jack.position2dict(pos_struct)
                bar = pos.get('bar', 1)
                beat = pos.get('beat', 1)
                tick = pos.get('tick', 0)
                ticks_per_beat = pos.get('ticks_per_beat', self.song.ticks_per_beat)
                beats_per_bar = pos.get('beats_per_bar', self.song.time_signature_numerator)
                return (bar - 1) * beats_per_bar + (beat - 1) + (tick / ticks_per_beat)
            except (jack.JackError, AttributeError):
                 # This can happen if the client is not active or during shutdown
                return 0.0
        return 0.0

    def _recording_thread_main(self, target_track: MidiTrack, start_beat: float, inport_name: str, outport_name: Optional[str], num_beats_to_record: Optional[float], original_mute_state: bool):
        """The main loop for the MIDI recording thread, now driven by JACK."""
        open_notes = {}  # key: note_pitch, value: (start_beat, velocity)
        outport = None
        is_virtual_port = False
        recording_started_beat = None

        try:
            with mido.open_input(inport_name) as inport:
                if outport_name:
                    vp = next((p for p in self.virtual_ports if p.name == outport_name), None)
                    if vp:
                        outport = vp
                        is_virtual_port = True
                    else:
                        outport = open_output(outport_name)
                    # Send initial program/bank change for MIDI thru
                    if target_track.bank_msb is not None: outport.send(mido.Message('control_change', channel=target_track.channel, control=0, value=target_track.bank_msb))
                    if target_track.bank_lsb is not None: outport.send(mido.Message('control_change', channel=target_track.channel, control=32, value=target_track.bank_lsb))
                    outport.send(mido.Message('program_change', channel=target_track.channel, program=target_track.instrument))

                print(f"Armed for recording on track '{target_track.name}'. Waiting for JACK transport to roll past {self._format_beats_to_position(start_beat)}.")

                while not self._stop_event.is_set():
                    current_beat = self._get_current_beat()

                    # Wait until we are at the punch-in point and transport is rolling
                    is_rolling = self.jack_manager.jack_client and self.jack_manager.jack_client.transport_state == jack.ROLLING
                    if not is_rolling or current_beat < start_beat:
                        time.sleep(0.01)
                        continue

                    if recording_started_beat is None:
                        recording_started_beat = current_beat
                        print(f"Recording started at beat {self._format_beats_to_position(current_beat)}. Type 'stop' to finish.")

                    for msg in inport.iter_pending():
                        if outport and hasattr(msg, 'channel'):
                            outport.send(msg.copy(channel=target_track.channel))

                        current_beat_for_msg = current_beat # Use the beat from the start of the loop

                        if msg.type == 'note_on' and msg.velocity > 0:
                            if msg.note not in open_notes:
                                open_notes[msg.note] = (current_beat_for_msg, msg.velocity)
                        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                            if msg.note in open_notes:
                                note_on_beat, velocity = open_notes.pop(msg.note)
                                duration_beats = current_beat_for_msg - note_on_beat

                                if duration_beats <= 0: duration_beats = 0.01

                                note = Note(pitch=msg.note, velocity=velocity, duration=duration_beats)
                                target_track.add_event(Event(notes=[note], start_time=note_on_beat))
                                self.is_dirty = True

                    if num_beats_to_record and recording_started_beat is not None and (current_beat - recording_started_beat) >= num_beats_to_record:
                        print(f"\nFinished recording for {num_beats_to_record:.2f} beats.")
                        self._stop_event.set() # Signal to stop the recording loop

                    time.sleep(0.001)

        except Exception as e:
            print(f"\nAn error occurred during recording: {e}")
        finally:
            if outport:
                for note_pitch in open_notes:
                    outport.send(mido.Message('note_off', channel=target_track.channel, note=note_pitch, velocity=0))
                if not is_virtual_port and outport and not outport.closed:
                    outport.close()

            target_track.is_muted = original_mute_state
            self.is_recording = False
            # The main CLI loop should handle stopping the JACK client now
            print("\nRecording thread finished.")
    def _start_recording_internal(self, track_index: int, start_beat: float, num_beats_to_record: Optional[float], inport_name: str, replace_notes: bool):
        target_track = self.song.tracks[track_index]
        if not isinstance(target_track, MidiTrack):
            # This check is a safeguard, should be checked before calling
            print("Error: Recording is only supported for MIDI tracks.")
            return

        if replace_notes:
            end_beat = (
                float("inf")
                if num_beats_to_record is None
                else start_beat + num_beats_to_record
            )

            events_to_keep = []
            for event in target_track.events:
                if start_beat <= event.start_time < end_beat:
                    # This event is in the range to be cleared of notes
                    if event.notes:
                        event.notes.clear()

                    # If the event is now empty, we don't add it to the keep list.
                    # Otherwise, we keep the event with its other messages intact.
                    is_empty = not event.notes and not event.cc_messages
                    if not is_empty:
                        events_to_keep.append(event)
                else:
                    # This event is outside the range, so we keep it as is.
                    events_to_keep.append(event)

            target_track.events = events_to_keep
            print(
                f"Removed existing notes from beat {self._format_beats_to_position(start_beat)} onwards."
            )

        outport_name = target_track.output_port_name
        original_mute_state = target_track.is_muted
        if replace_notes:
            target_track.is_muted = True
        # When overdubbing (not replacing), we don't mute the track so the user can hear existing notes.
        # The playback engine uses a snapshot of events from before recording started,
        # and new notes are passed through via the recording thread's MIDI thru.

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
            start_pos_str = cancellable_input(f"Start recording at position on track '{target_track.name}' (measure:beat) [default: 1:1]: ").strip()
            start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if start_beat is None: return

            measures_input = cancellable_input("Record for how long (measures:beats)? (Press Enter for unlimited) ").strip()
            num_beats_to_record = None
            if measures_input:
                parts = measures_input.split(':')
                num_measures = int(parts[0])
                num_beats = int(parts[1]) if len(parts) == 2 else 0
                num_beats_to_record = (num_measures * self.song.time_signature_numerator) + num_beats

            replace_notes = False
            existing_notes_in_range = [e for e in target_track.events if e.start_time >= start_beat]
            if existing_notes_in_range:
                choice = cancellable_input("There are existing notes. Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
                if choice.startswith('r'):
                    replace_notes = True

            input_ports = mido.get_input_names() # type: ignore
            if not input_ports:
                print("Error: No MIDI input ports found.")
                return
            print("Available MIDI input ports:")
            for i, port in enumerate(input_ports): print(f"  [{i}] {port}")
            inport_idx = int(cancellable_input("Choose a port to record from: "))
            if not 0 <= inport_idx < len(input_ports):
                print("Error: Invalid port index.")
                return
            inport_name = input_ports[inport_idx]

        except (ValueError, IndexError, UserInputCancelled):
            print("\nRecord cancelled."); return

        self._stop_event.clear()

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

        # Make a copy of the settings to modify for this run
        settings = self.last_record_settings.copy()

        # Explicitly ask about replacing notes for the 'bis' call
        track_index = settings['track_index']
        start_beat = settings['start_beat']
        target_track = self.song.tracks[track_index]

        replace_notes = False
        # Check for existing notes in the recording range
        existing_notes_in_range = [e for e in target_track.events if e.start_time >= start_beat]
        if existing_notes_in_range:
            try:
                choice = cancellable_input("There are existing notes. Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
                if choice.startswith('r'):
                    replace_notes = True
            except UserInputCancelled:
                print("\nRecord cancelled.")
                return

        # Update the 'replace_notes' for this specific call
        settings['replace_notes'] = replace_notes

        # Unpack the potentially modified settings and call the internal recording function
        self._start_recording_internal(**settings)

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

    def _generate_automation_events(self, auto_track: 'AutomationTrack') -> List[dict]:
        """
        Generates a list of concrete MIDI/audio events from an automation track.
        """
        generated_events = []
        points = sorted(auto_track.points, key=lambda p: p.start_time)
        if not points:
            return []

        # Find the target track and its type
        target_track_index = auto_track.target_track_index
        if not 0 <= target_track_index < len(self.song.tracks):
            return [] # Invalid target
        target_track = self.song.tracks[target_track_index]


        param_map = {
            "vol": {"type": "midi_cc", "control": 7},
            "pan": {"type": "midi_cc", "control": 10},
            "vel": {"type": "velocity_multiplier"},
            "prog": {"type": "program_change"},
            # Generic CCs like "cc1", "cc11", etc.
            **{f"cc{i}": {"type": "midi_cc", "control": i} for i in range(128)}
        }


        for i, start_point in enumerate(points):
            param_config = param_map.get(start_point.parameter.lower())
            if not param_config:
                continue # Skip unknown parameters

            # Always add the start point of any curve
            generated_events.append({
                "time": start_point.start_time,
                "target_track_index": target_track_index,
                "parameter": start_point.parameter,
                "param_config": param_config,
                "value": start_point.value
            })

            # If there's no next point or the curve is 'none', we're done with this point.
            if i + 1 >= len(points) or start_point.curve == "none":
                continue

            end_point = points[i+1]

            # A curve can only be formed between points of the same parameter
            if start_point.parameter != end_point.parameter:
                continue

            start_time = start_point.start_time
            end_time = end_point.start_time
            start_val = start_point.value
            end_val = end_point.value

            time_diff = end_time - start_time
            if time_diff <= 0:
                continue

            # Granularity: 1/16th of a beat
            granularity = 1.0 / 16.0
            num_steps = int(time_diff / granularity)
            if num_steps <= 1:
                continue

            # Generate normalized time steps (from 0 to 1), excluding the first step (t=0)
            # because the start_point is already added.
            t = np.linspace(0, 1, num_steps, endpoint=False)[1:]
            time_steps = start_time + t * time_diff
            value_range = end_val - start_val
            value_steps = None

            if start_point.curve == "linear":
                value_steps = start_val + t * value_range
            elif start_point.curve == "ease-in":
                # y = x^2
                value_steps = start_val + (t**2) * value_range
            elif start_point.curve == "ease-out":
                # y = 1 - (1-x)^2
                value_steps = start_val + (1 - (1 - t)**2) * value_range
            elif start_point.curve in ["ease-in-out", "sine"]:
                # y = 0.5 * (1 - cos(pi * x))
                value_steps = start_val + (0.5 * (1 - np.cos(np.pi * t))) * value_range
            else:
                # Unknown curve type, do nothing more for this segment
                continue

            # Append the generated intermediate points
            for step_time, step_value in zip(time_steps, value_steps):
                generated_events.append({
                    "time": step_time,
                    "target_track_index": target_track_index,
                    "parameter": start_point.parameter,
                    "param_config": param_config,
                    "value": step_value
                })

        return generated_events


    def play(self, start_beat: Optional[float] = None):
        """
        Starts or seeks the JACK transport.
        If start_beat is provided, it seeks the transport to that position.
        It then ensures the transport is rolling.
        """
        # 1. Ensure client is running.
        if not self.jack_manager.is_running:
            print("JACK client not active. Starting...")
            self.jack_manager.start()
            # Give a moment for the client to be fully active before commanding it
            time.sleep(0.1)

        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            print("Error: Could not start JACK client.")
            return

        # 2. If a start beat is given, reposition the transport.
        if start_beat is not None:
            try:
                beats_per_second = self.song.tempo / 60.0
                samplerate = self.jack_manager.jack_client.samplerate
                if beats_per_second > 0 and samplerate > 0:
                    target_frame = int((start_beat / beats_per_second) * samplerate)

                    # Query the current position to get a valid Position object to modify.
                    _ , pos = self.jack_manager.jack_client.transport_query_struct()

                    pos.frame = target_frame
                    # Setting only the frame is a common use case, and the C library
                    # likely defaults to using the frame if no valid mask is set.
                    # We will rely on this default behavior and not set pos.valid.

                    self.jack_manager.jack_client.transport_reposition_struct(pos)
                    print(f"Seeking JACK transport to {self._format_beats_to_position(start_beat)}.")
                    # After repositioning, we need to manually sync our internal state
                    # because the _time_callback might not fire immediately.
                    self.jack_manager._sync_playhead_to_beat(start_beat)

            except jack.JackError as e:
                print(f"Error seeking JACK transport: {e}")
                return # Don't try to start if seek failed

        # 3. Start the transport rolling if it's not already.
        try:
            if self.jack_manager.jack_client.transport_state != jack.ROLLING:
                self.jack_manager.jack_client.transport_start()
                # The "transport started" message is now handled by the pause command for clarity
        except jack.JackError as e:
            print(f"Error starting JACK transport: {e}")

        # Finally, update our internal state to "playing"
        self.playback_state = "playing"

    def pause(self):
        """Toggles the JACK transport state between rolling and stopped."""
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            print("JACK client not running. Please start playback first.")
            return

        try:
            if self.jack_manager.jack_client.transport_state == jack.ROLLING:
                self.jack_manager.jack_client.transport_stop()
                print("JACK transport stopped.")
            else:
                self.jack_manager.jack_client.transport_start()
                print("JACK transport started.")
        except jack.JackError as e:
            print(f"Error controlling JACK transport: {e}")

    def stop(self):
        if not self.is_recording and self.playback_state == "stopped":
            print("Already stopped.")
            return

        if self.is_recording and self.recording_thread:
            self._stop_event.set()
            self.recording_thread.join(timeout=1.0)
            self.is_recording = False

        print("Stopping JACK client...")
        self.jack_manager.stop()
        self.playback_state = "stopped"
        self._all_notes_off()
        print("Session stopped.")

    def restart(self):
        """Restarts playback from the beginning."""
        print("When slaved to JACK, playback must be controlled by the JACK transport master.")
        print("Use your master application to return to the start of the song.")

    def send_cc_message(self, port_name: str, channel: int, control: int, value: int):
        """Sends a single CC message to a specified port."""
        port = self.open_ports.get(port_name)

        # Check if the port is a virtual port that is already open
        if not port:
            vp = next((p for p in self.virtual_ports if p.name == port_name), None)
            if vp:
                port = vp

        is_temp_port = False
        if not port:
            try:
                # If not found in open or virtual ports, try to open it as a hardware port
                port = open_output(port_name)
                is_temp_port = True
            except Exception as e:
                print(f"Error: Could not open MIDI port '{port_name}': {e}")
                return

        if port:
            try:
                if not 0 <= channel <= 15:
                    print("Error: Channel must be between 0 and 15.")
                    return
                if not 0 <= control <= 127:
                    print("Error: CC number must be between 0 and 127.")
                    return
                if not 0 <= value <= 127:
                    print("Error: CC value must be between 0 and 127.")
                    return

                msg = mido.Message('control_change', channel=channel, control=control, value=value)
                port.send(msg)
                # Add a small delay to ensure the message is sent, especially for temporary ports.
                time.sleep(0.01)
                print(f"Sent CC message to {port_name}: Ch={channel+1}, CC={control}, Val={value}")

            except Exception as e:
                print(f"Error sending CC message: {e}")
            finally:
                if is_temp_port and port:
                    port.close()