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
from typing import List, Optional, Any, Dict
from functools import wraps
from contextlib import contextmanager
from contextlib import contextmanager

@contextmanager
def suppress_stdout_stderr():
    """A context manager for suppressing stdout and stderr."""
    with open(os.devnull, 'w') as fnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = fnull
        sys.stderr = fnull
        try:
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

@dataclass
class ActiveAudioProcess:
    process: subprocess.Popen
    socket_path: str
    track_index: int
    temp_filepath: Optional[str] = None # No longer mixing to a temp file


class CustomSongEncoder(json.JSONEncoder):
    def default(self, o):
        if is_dataclass(o):
            d = {f.name: getattr(o, f.name) for f in fields(o)}
            d['__type__'] = o.__class__.__name__
            return d
        return super().default(o)

def song_decoder(d):
    if '__type__' in d:
        type_name = d.pop('__type__')
        cls = getattr(sys.modules[__name__], type_name, None)
        if cls:
            return cls(**d)
    return d


from kivy.properties import NumericProperty
from kivy.event import EventDispatcher

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
        self.sync_lock = threading.Lock()
        self._display_thread = None
        self._display_stop_event = threading.Event()
        self.automation_events = []
        self.next_automation_event_index = 0
        self.event_to_ignore: Optional[dict] = None

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

    def start(self):
            if self.is_running:
                print("JACK client is already running.")
                return

            try:
                self.jack_client = jack.Client(f"{self.sequencer.song.name}-sequencer")

                # --- MIDI Port Setup ---
                self.open_ports.clear()
                required_ports = {track.output_port_name for track in self.sequencer.song.tracks if isinstance(track, MidiTrack) and track.output_port_name}
                if self.sequencer.song.metronome_port_name:
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

                # --- Wait for audio players to be ready ---
                if sys.platform != "win32":
                    print("Waiting for audio player sockets...")
                    with self.process_lock:
                        audio_processes = list(self.active_audio_processes)

                    for ap in audio_processes:
                        track = self.sequencer.song.tracks[ap.track_index]
                        max_wait_time = 5.0  # 5 secondes par socket
                        start_time = time.time()
                        is_ready = False
                        while time.time() - start_time < max_wait_time:
                            if self._is_socket_responsive(ap.socket_path):
                                print(f"  - Socket for track '{track.name}' is responsive.")
                                is_ready = True
                                break
                            time.sleep(0.1)

                        if not is_ready:
                            print(f"Warning: Timed out waiting for audio player for track '{track.name}'. Playback may fail for this track.", file=sys.stderr)
                else:
                    # On Windows, a simple delay is the most practical approach.
                    time.sleep(2.0)

                # --- Prime Audio Tracks Immediately After They Are Ready ---
                print("Priming audio tracks with initial state...")
                is_any_track_soloed = any(t.is_solo for t in self.sequencer.song.tracks if hasattr(t, 'is_solo'))

                with self.process_lock:
                    for ap in self.active_audio_processes:
                        track = self.sequencer.song.tracks[ap.track_index]
                        if isinstance(track, AudioTrack):
                            print(f"  - Priming Audio track '{track.name}'")
                            # Ajout de vérifications pour voir si les commandes réussissent
                            if not self._send_ipc_command(ap.socket_path, {"command": ["set_property", "volume", track.volume * 100]}):
                                print(f"    - Warning: Failed to set volume for track '{track.name}'.", file=sys.stderr)
                            if not self._send_ipc_command(ap.socket_path, {"command": ["set_property", "balance", track.pan]}):
                                print(f"    - Warning: Failed to set pan for track '{track.name}'.", file=sys.stderr)
                            
                            should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                            if not self._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", not should_be_audible]}):
                                print(f"    - Warning: Failed to set mute state for track '{track.name}'.", file=sys.stderr)

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

                if not self.sequencer.gui_mode:
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

    def _send_ipc_command(self, socket_path, command_data) -> bool:
        try:
            if sys.platform == "win32":
                pipe_name = r'\\.\pipe\\' + os.path.basename(socket_path)
                with open(pipe_name, 'w') as pipe:
                    pipe.write(json.dumps(command_data) + '\n')
            else:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(0.1)
                    s.connect(socket_path)
                    s.sendall(json.dumps(command_data).encode('utf-8') + b'\n')
            return True
        except (socket.timeout, ConnectionRefusedError, FileNotFoundError, BrokenPipeError):
            return False
        except Exception as e:
            print(f"Error sending IPC command to {socket_path}: {e}", file=sys.stderr)
            return False

    def _query_ipc_command(self, socket_path, command_data, timeout=0.2):
        """Sends a command and waits for a JSON response."""
        try:
            if sys.platform == "win32":
                # For Windows, named pipes behave a bit differently and this simple read/write might not be sufficient.
                # Given the bug report context (unix sockets), we'll focus on the unix implementation.
                # A simple connect check is the fallback.
                return {"error": "success"} if self._is_socket_connectable(socket_path) else None

            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(socket_path)
                s.sendall(json.dumps(command_data).encode('utf-8') + b'\n')

                # Read the response
                response_data = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break  # Connection closed
                    response_data += chunk
                    if b'\n' in response_data:
                        break # Full response received

                # There could be multiple JSON objects, we only care about the first
                response_line = response_data.split(b'\n', 1)[0]
                response_json = json.loads(response_line.decode('utf-8'))
                return response_json

        except (socket.timeout, ConnectionRefusedError, FileNotFoundError, BrokenPipeError, json.JSONDecodeError):
            return None
        except Exception:
            # Silently fail, as this is just a check.
            return None

    def _is_socket_responsive(self, socket_path: str) -> bool:
        """Checks if an mpv socket is not just connectable, but also responding to commands."""
        command = {"command": ["get_property", "pid"]}
        response = self._query_ipc_command(socket_path, command)
        return response is not None and response.get("error") == "success"

    def _is_socket_connectable(self, socket_path: str) -> bool:
        if sys.platform == "win32":
            return True
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(socket_path)
            return True
        except (socket.timeout, ConnectionRefusedError, FileNotFoundError):
            return False

    def set_all_audio_pause_state(self, is_paused: bool):
        with self.process_lock:
            for ap in self.active_audio_processes:
                command = {"command": ["set_property", "pause", is_paused]}
                self._send_ipc_command(ap.socket_path, command)

    def _launch_audio_track_player(self, track: AudioTrack, track_index: int):
        import shlex
        socket_dir = tempfile.gettempdir()
        socket_filename = f"mpv-socket-{os.getpid()}-{track_index}"
        socket_path = os.path.join(socket_dir, socket_filename)

        if sys.platform != "win32" and os.path.exists(socket_path):
            os.unlink(socket_path)

        command = shlex.split(self.sequencer.audio_player_command)
        command.extend([
            f"--input-ipc-server={socket_path}",
            "--pause",
            track.filepath
        ])

        kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        if sys.platform != "win32":
            kwargs['preexec_fn'] = os.setsid

        try:
            process = subprocess.Popen(command, **kwargs)
            self.active_audio_processes.append(ActiveAudioProcess(process=process, socket_path=socket_path, track_index=track_index))
            print(f"Launched mpv for track {track_index} with IPC socket: {socket_path}")
        except Exception as e:
            print(f"Error launching mpv for track {track_index}: {e}")
            if sys.platform != "win32" and os.path.exists(socket_path):
                os.unlink(socket_path)

    def _shutdown_audio_processes(self):
        with self.process_lock:
            for ap in self.active_audio_processes:
                try:
                    if ap.process.poll() is None:
                        ap.process.terminate()
                        ap.process.wait(timeout=1.0)
                except (subprocess.TimeoutExpired, Exception):
                    if ap.process.poll() is None:
                        ap.process.kill()
                try:
                    if sys.platform != "win32" and ap.socket_path and os.path.exists(ap.socket_path):
                        os.unlink(ap.socket_path)
                except Exception as e:
                    print(f"Error removing socket file {ap.socket_path}: {e}", file=sys.stderr)
            self.active_audio_processes.clear()

    def seek_audio_to_beat(self, beat_pos: float):
        beats_per_second = self.sequencer.song.tempo / 60.0
        if beats_per_second <= 0:
            return
        with self.process_lock:
            for ap in self.active_audio_processes:
                track = self.sequencer.song.tracks[ap.track_index]
                if isinstance(track, AudioTrack):
                    mpv_time = (beat_pos - track.start_time) / beats_per_second
                    if mpv_time < 0:
                        mpv_time = 0.0
                    command = {"command": ["seek", mpv_time, "absolute"]}
                    self._send_ipc_command(ap.socket_path, command)

    def _sync_playhead_to_beat(self, beat_pos: float):
        self.last_beat = beat_pos
        num_tracks = len(self.sequencer.song.tracks)  # Corrigé: self.sequencer.song
        self.next_event_indices = [0] * num_tracks
        self._active_notes.clear()

        for i, track in enumerate(self.sequencer.song.tracks):  # Corrigé: self.sequencer.song
            if isinstance(track, MidiTrack):
                for j, event in enumerate(track.events):
                    if event.start_time >= self.last_beat:
                        self.next_event_indices[i] = j
                        break
                else:
                    self.next_event_indices[i] = len(track.events)

        self.next_automation_event_index = 0
        for i, event in enumerate(self.automation_events):
            if event['time'] >= self.last_beat:
                self.next_automation_event_index = i
                break
        else:
            self.next_automation_event_index = len(self.automation_events)

    def _time_callback(self, state, blocksize, pos, new_pos):
        if new_pos:
            pos_dict = jack.position2dict(pos)
            self.sequencer.song.tempo = pos_dict.get('beats_per_minute', self.sequencer.song.tempo)  # Corrigé: self.sequencer.song
            frame = pos_dict.get('frame', 0)
            samplerate = self.jack_client.samplerate
            beats_per_second = self.sequencer.song.tempo / 60.0  # Corrigé: self.sequencer.song
            current_beat = 0.0
            if samplerate > 0 and beats_per_second > 0:
                current_beat = (frame / samplerate) * beats_per_second
            self._sync_playhead_to_beat(current_beat)
            self.seek_audio_to_beat(current_beat)

    def _process_callback(self, frames: int):
        try:
            with self.sync_lock:
                if self.sequencer.song.metronome_enabled and self.sequencer.song.metronome_port_name in self.open_ports:
                    port = self.open_ports[self.sequencer.song.metronome_port_name]
                    for note_off_msg in self._metronome_notes_to_turn_off:
                        port.send(note_off_msg)
                    self._metronome_notes_to_turn_off.clear()

                if not self.jack_client or self.jack_client.transport_state != jack.ROLLING:
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
            tempo = self.sequencer.song.tempo  # Corrigé: self.sequencer.song
            beats_per_second = tempo / 60.0
            start_beat_of_block = self.last_beat
            end_beat_of_block = start_beat_of_block + (frames / samplerate) * beats_per_second

            for (track_idx, pitch), end_beat in list(self._active_notes.items()):
                if start_beat_of_block <= end_beat < end_beat_of_block:
                    track = self.sequencer.song.tracks[track_idx]  # Corrigé: self.sequencer.song
                    if isinstance(track, MidiTrack) and track.output_port_name in self.open_ports:
                        port = self.open_ports[track.output_port_name]
                        port.send(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0))
                    del self._active_notes[(track_idx, pitch)]

            is_any_track_soloed = any(t.is_solo for t in self.sequencer.song.tracks if hasattr(t, 'is_solo'))  # Corrigé: self.sequencer.song

            for i, track in enumerate(self.sequencer.song.tracks):  # Corrigé: self.sequencer.song
                if not isinstance(track, MidiTrack) or not track.output_port_name in self.open_ports:
                    continue

                should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                if not should_be_audible:
                    continue

                port = self.open_ports[track.output_port_name]
                if i >= len(self.next_event_indices):
                    self.next_event_indices.extend([0] * (i - len(self.next_event_indices) + 1))
                while self.next_event_indices[i] < len(track.events):
                    event = track.events[self.next_event_indices[i]]

                    if start_beat_of_block <= event.start_time < end_beat_of_block:
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
                        break
                    else:
                        self.next_event_indices[i] += 1

            while self.next_automation_event_index < len(self.automation_events):
                event = self.automation_events[self.next_automation_event_index]
                event_time = event['time']
                if start_beat_of_block <= event_time < end_beat_of_block:
                    target_track_index = event['target_track_index']
                    if not 0 <= target_track_index < len(self.sequencer.song.tracks):  # Corrigé: self.sequencer.song
                        self.next_automation_event_index += 1
                        continue
                    target_track = self.sequencer.song.tracks[target_track_index]  # Corrigé: self.sequencer.song
                    param_config = event['param_config']
                    value = event['value']
                    param_name = event['parameter'].lower()
                    if param_config['type'] == 'midi_cc':
                        if isinstance(target_track, MidiTrack) and target_track.output_port_name in self.open_ports:
                            port = self.open_ports[target_track.output_port_name]
                            midi_value = 0
                            if param_name == 'vol':
                                midi_value = int(value * 127)
                            elif param_name == 'pan':
                                midi_value = int((value + 1.0) / 2.0 * 127)
                            else:
                                midi_value = int(value)
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
                else:
                    self.next_automation_event_index += 1

            if self.sequencer.play_range_enabled and end_beat_of_block >= self.sequencer.play_range_end_beat:
                if start_beat_of_block < self.sequencer.play_range_end_beat:
                    self.jack_client.transport_stop()
                    self.set_all_audio_pause_state(True)
                    self.sequencer.play_range_enabled = False

            if self.sequencer.loop_enabled and end_beat_of_block >= self.sequencer.loop_end_beat:
                if start_beat_of_block < self.sequencer.loop_end_beat:
                    beats_per_second = self.sequencer.song.tempo / 60.0  # Corrigé: self.sequencer.song
                    samplerate = self.jack_client.samplerate
                    if beats_per_second > 0 and samplerate > 0:
                        target_frame = int((self.sequencer.loop_start_beat / beats_per_second) * samplerate)
                        _ , pos = self.jack_client.transport_query_struct()
                        pos.frame = target_frame
                        self.jack_client.transport_reposition_struct(pos)

            if self.sequencer.song.metronome_enabled and self.sequencer.song.metronome_port_name in self.open_ports:  # Corrigé: self.sequencer.song
                port = self.open_ports[self.sequencer.song.metronome_port_name]  # Corrigé: self.sequencer.song
                beat_to_check = math.ceil(start_beat_of_block)

                if beat_to_check < end_beat_of_block:
                    # Send pan control once per block if there are clicks
                    midi_pan = int((self.sequencer.song.metronome_pan + 1.0) / 2.0 * 127)  # Corrigé: self.sequencer.song
                    port.send(mido.Message('control_change', channel=self.sequencer.metronome_channel, control=10, value=midi_pan))

                while beat_to_check < end_beat_of_block:
                    beats_per_measure = self.sequencer.song.time_signature_numerator  # Corrigé: self.sequencer.song
                    is_downbeat = (int(beat_to_check) % beats_per_measure) == 0 if beats_per_measure > 0 else beat_to_check == 0
                    pitch = self.sequencer.metronome_pitch_downbeat if is_downbeat else self.sequencer.metronome_pitch_beat
                    velocity = int(100 * self.sequencer.song.metronome_volume)  # Corrigé: self.sequencer.song
                    note_on = mido.Message('note_on', channel=self.sequencer.metronome_channel, note=pitch, velocity=velocity)
                    note_off = mido.Message('note_off', channel=self.sequencer.metronome_channel, note=pitch, velocity=0)
                    port.send(note_on)
                    self._metronome_notes_to_turn_off.append(note_off)
                    beat_to_check += 1

            song_length_beats = self.sequencer.get_song_length_in_beats()
            if not self.sequencer.loop_enabled and song_length_beats > 0 and end_beat_of_block >= song_length_beats:
                if start_beat_of_block < song_length_beats:
                    self.jack_client.transport_stop()

            self.last_beat = end_beat_of_block
            if self.sequencer.gui_mode:
                self.sequencer.current_beat = self.last_beat
        except Exception as e:
            print(f"\nError in JACK process callback: {e}")


class Sequencer(EventDispatcher):
    current_beat = NumericProperty(0)
    DEFAULT_AUDIO_PLAYER_COMMAND = "mpv --really-quiet --no-video --idle --audio-device=jack"

    def __init__(self, tempo: int = 120, gui_mode=False):
        super().__init__()
        self.gui_mode = gui_mode
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

        self.loop_enabled = False
        self.loop_start_beat = 0.0
        self.loop_end_beat = 0.0

        self.play_range_enabled = False
        self.play_range_start_beat = 0.0
        self.play_range_end_beat = 0.0

        self.metronome_channel = 9
        self.metronome_pitch_downbeat = 76
        self.metronome_pitch_beat = 77

        self.last_record_settings = None
        self.is_dirty = False
        self.last_project_basename = None
        self._cached_song_length_beats: Optional[float] = None
        self.audio_track_duration_ms: Dict[str, int] = {}
        
        self.default_record_port: Optional[str] = None  # Port d'enregistrement par défaut        

    def set_default_record_port(self, port_name: str) -> str:
        """Définit le port MIDI d'entrée par défaut pour l'enregistrement"""
        try:
            # Vérifier que le port existe
            input_ports = get_input_names()
            if port_name not in input_ports:
                return f"Error: MIDI input port '{port_name}' not found."
            
            self.default_record_port = port_name
            self.is_dirty = True
            return f"Default record port set to: {port_name}"
        except Exception as e:
            return f"Error setting record port: {e}"

    def get_default_record_port(self) -> Optional[str]:
        """Retourne le port d'enregistrement par défaut"""
        return self.default_record_port

    def invalidate_song_length_cache(self):
        """Invalidates the cached song length."""
        self._cached_song_length_beats = None

    def get_song_length_in_beats(self) -> float:
        """
        Returns the cached song length in beats.
        If the cache is invalid, it recalculates, caches, and returns the length.
        """
        if self._cached_song_length_beats is None:
            self._cached_song_length_beats = self._calculate_song_length_in_beats()
        return self._cached_song_length_beats

    def _all_notes_off(self):
        for port in self.open_ports.values():
            if port and not port.closed:
                for channel in range(16):
                    port.send(mido.Message('control_change', channel=channel, control=123, value=0))

    def parse_position_to_beats(self, position_str: str, default: str = "1:1") -> Optional[float]:
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
            return (measure - 1) * beats_per_measure + (beat - 1)
        except (ValueError, IndexError):
            print("Error: Invalid format. Please enter numbers in 'measure:beat' format.")
            return None

    def _format_beats_to_position(self, beats: float) -> str:
        if beats is None:
            return ""
        beats_per_measure = self.song.time_signature_numerator
        if beats_per_measure == 0:
            return "1:1"
        measure = int(beats / beats_per_measure) + 1
        beat = int(beats % beats_per_measure) + 1
        return f"{measure}:{beat}"

    def set_tempo(self, tempo: int) -> str:
        if tempo <= 0:
            return "Error: Tempo must be positive."
        self.song.tempo = tempo
        self.is_dirty = True
        self.invalidate_song_length_cache()
        return f"Tempo set to {self.song.tempo} BPM."

    def set_time_signature(self, numerator: int, denominator: int) -> str:
        if not (numerator > 0 and denominator > 0 and (denominator & (denominator - 1) == 0)):
            return "Error: Invalid time signature. Denominator must be a power of 2."
        self.song.time_signature_numerator = numerator
        self.song.time_signature_denominator = denominator
        self.is_dirty = True
        return f"Time signature set to {numerator}/{denominator}."

    def add_track(self, name: str, track_type: str = 'midi', instrument: int = 0, filepath: Optional[str] = None):
        if track_type == 'midi':
            track = MidiTrack(name=name, instrument=instrument)
            self.song.add_track(track)
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return {"status": "success", "message": f"MIDI track '{name}' added."}
        elif track_type == 'audio':
            if not filepath:
                return {"status": "error", "message": "Error: Filepath is required for audio tracks."}
            try:
                with suppress_stdout_stderr():
                    segment = AudioSegment.from_file(filepath)
                self.audio_track_duration_ms[filepath] = len(segment)
            except FileNotFoundError:
                return {"status": "error", "message": f"Error: Audio file not found at '{filepath}'"}
            except Exception as e:
                return {"status": "error", "message": f"Error opening audio file: {e}"}
            track = AudioTrack(name=name, filepath=filepath)
            self.song.add_track(track)
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return {"status": "success", "message": f"Audio track '{name}' added with file '{filepath}'."}
        else:
            return {"status": "error", "message": f"Error: Unknown track type '{track_type}'. Must be 'midi' or 'audio'."}

    def add_automation_track(self, name: str, target_track_index: int):
        if not 0 <= target_track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid target track index."}
        target_track = self.song.tracks[target_track_index]
        if isinstance(target_track, AutomationTrack):
            return {"status": "error", "message": "Error: Automation tracks cannot target other automation tracks."}
        track = AutomationTrack(name=name, target_track_index=target_track_index)
        self.song.add_track(track)
        self.is_dirty = True
        return {"status": "success", "message": f"Automation track '{name}' added, targeting track {target_track_index} ('{target_track.name}')."}

    def add_automation_point(self, track_index: int, position_str: str, parameter: str, value: float, curve: str) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, AutomationTrack):
            return "Error: Automation points can only be added to automation tracks."
        start_beat = self.parse_position_to_beats(position_str)
        if start_beat is None:
            return "Error: Invalid position."
        try:
            point = AutomationPoint(start_time=start_beat, parameter=parameter, value=value, curve=curve)
            track.add_point(point)
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Added '{parameter}' automation point to track '{track.name}' at position {position_str}."
        except ValueError as e:
            return f"Error: {e}"

    def delete_track(self, track_index: int, confirm_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track_name = self.song.tracks[track_index].name

        if not api_mode:
            if confirmation_handler:
                confirm = confirmation_handler(f"Are you sure you want to delete track '{track_name}'? [y/N] ").lower()
            else:
                confirm = cancellable_input(f"Are you sure you want to delete track '{track_name}'? [y/N] ").lower()
            if confirm != 'y':
                return {"status": "cancelled", "message": "Deletion cancelled."}
        else:
            if confirm_str is None:
                return {"status": "prompt", "message": f"Are you sure you want to delete track '{track_name}'? [y/N] ", "next_arg": "confirm_str"}
            if confirm_str.lower() != 'y':
                return {"status": "cancelled", "message": "Deletion cancelled."}

        self.song.tracks.pop(track_index)
        self.is_dirty = True
        self.invalidate_song_length_cache()
        return {"status": "success", "message": f"Track '{track_name}' deleted."}

    def add_cc_event(self, track_index: int, position_str: str, control: int, value: int) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: CC events can only be added to MIDI tracks."
        start_beat = self.parse_position_to_beats(position_str)
        if start_beat is None:
            return "Error: Invalid position."
        try:
            new_cc = CCMessage(control=control, value=value)
        except ValueError as e:
            return f"Error: Invalid CC value. {e}"
        existing_event = None
        for event in track.events:
            if math.isclose(event.start_time, start_beat):
                existing_event = event
                break
        if existing_event:
            existing_event.cc_messages.append(new_cc)
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Added CC to existing event at position {position_str} on track '{track.name}'."
        else:
            new_event = Event(start_time=start_beat, cc_messages=[new_cc])
            track.add_event(new_event)
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Added new CC event at position {position_str} on track '{track.name}'."

    def erase_track(self, track_idx: int, start_beat: float, end_beat: float, erase_choice: str, shift_events: bool):
        if not 0 <= track_idx < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_idx]
        if not isinstance(track, (MidiTrack, AutomationTrack)):
            return "Error: Erasing is only supported for MIDI and Automation tracks."
        if end_beat <= start_beat:
            return "Error: End position must be after the start position."

        if isinstance(track, MidiTrack):
            erase_notes = erase_choice.startswith('a') or erase_choice.startswith('n')
            erase_ccs = erase_choice.startswith('a') or erase_choice.startswith('c')

            final_events = []
            events_to_shift = []
            modified_count = 0
            deleted_count = 0
            for event in list(track.events):
                if start_beat <= event.start_time < end_beat:
                    event_modified = False
                    if erase_notes:
                        if event.notes:
                            event.notes.clear()
                            event_modified = True
                    if erase_ccs:
                        if event.cc_messages:
                            event.cc_messages.clear()
                            event_modified = True
                    if event_modified:
                        modified_count += 1
                    if not event.notes and not event.cc_messages:
                        deleted_count += 1
                    else:
                        final_events.append(event)
                elif event.start_time >= end_beat:
                    events_to_shift.append(event)
                else:
                    final_events.append(event)

            if shift_events:
                shift_offset = end_beat - start_beat
                for event in events_to_shift:
                    event.start_time -= shift_offset

            final_events.extend(events_to_shift)
            track.events = final_events
            track.events.sort(key=lambda e: e.start_time)

            report = []
            if modified_count > 0: report.append(f"modified {modified_count} event(s)")
            if deleted_count > 0: report.append(f"deleted {deleted_count} empty event(s)")
            if shift_events: report.append(f"shifted {len(events_to_shift)} event(s)")

            if report:
                 self.is_dirty = True
                 self.invalidate_song_length_cache()
                 return f"Operation complete: {', '.join(report)} from track '{track.name}'."
            else:
                 return "No events were modified, deleted, or shifted."

        elif isinstance(track, AutomationTrack):
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
                self.invalidate_song_length_cache()
                return f"Erased {deleted_count} point(s) from track '{track.name}'."
            else:
                return "No points were erased."

        return "This should not be reached."

    def rename_track(self, track_index: int, new_name: str):
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        old_name = self.song.tracks[track_index].name
        self.song.tracks[track_index].name = new_name
        self.is_dirty = True
        return {"status": "success", "message": f"Track '{old_name}' renamed to '{new_name}'."}

    def move_track_section(self, source_track_idx: int, confirmation_handler=None) -> str:
        if not 0 <= source_track_idx < len(self.song.tracks):
            return "Error: Invalid source track index."
        source_track = self.song.tracks[source_track_idx]
        if not isinstance(source_track, MidiTrack):
            return "Error: Moving events is only supported for MIDI tracks."

        _input = confirmation_handler or cancellable_input

        try:
            start_pos_str = _input(f"Move from position on track '{source_track.name}' (measure:beat) [default: 1:1]: ").strip()
            source_start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if source_start_beat is None: return ""
            end_pos_str = _input(f"Move up to position on track '{source_track.name}' (measure:beat): ").strip()
            source_end_beat = self.parse_position_to_beats(end_pos_str)
            if source_end_beat is None: return ""
            if source_end_beat <= source_start_beat:
                return "Error: End position must be after the start position."
            dest_track_idx_str = _input(f"Move to destination track index (default: {source_track_idx}, '{source_track.name}'): ").strip()
            dest_track_idx = source_track_idx if dest_track_idx_str == "" else int(dest_track_idx_str)
            if not 0 <= dest_track_idx < len(self.song.tracks):
                return "Error: Invalid destination track index."
            dest_track = self.song.tracks[dest_track_idx]
            if not isinstance(dest_track, MidiTrack):
                return "Error: Destination track must be a MIDI track."
            dest_pos_str = _input(f"Move to destination position on track '{dest_track.name}' (measure:beat) [default: 1:1]: ").strip()
            destination_start_beat = self.parse_position_to_beats(dest_pos_str, default="1:1")
            if destination_start_beat is None: return ""
        except (ValueError, UserInputCancelled):
            return "\nMove cancelled."
        range_duration_beats = source_end_beat - source_start_beat
        destination_end_beat = destination_start_beat + range_duration_beats
        offset_beats = destination_start_beat - source_start_beat
        confirm_message = f"Move events from {start_pos_str} to {end_pos_str} on track '{source_track.name}' to start at {dest_pos_str} on track '{dest_track.name}'. Are you sure? [y/N] "
        if _input(confirm_message).lower() != 'y':
            return "Move cancelled."
        events_at_destination = [event for event in dest_track.events if destination_start_beat <= event.start_time < destination_end_beat]
        if source_track == dest_track:
            events_at_destination = [e for e in events_at_destination if not (source_start_beat <= e.start_time < source_end_beat)]
        overwrite_mode = "add"
        if events_at_destination:
            output = "There are existing notes at the destination.\n"
            while True:
                choice = _input("Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
                if choice in ['r', 'replace', 'a', 'add']:
                    overwrite_mode = choice[0]
                    break
                else:
                    output += "Invalid choice. Please enter 'r' or 'a'.\n"
            print(output)
        events_to_move = []
        remaining_source_events = []
        for event in source_track.events:
            if source_start_beat <= event.start_time < source_end_beat:
                events_to_move.append(event)
            else:
                remaining_source_events.append(event)
        deleted_event_count = 0
        if overwrite_mode == 'r':
            final_dest_events = []
            for event in dest_track.events:
                if not (destination_start_beat <= event.start_time < destination_end_beat):
                    final_dest_events.append(event)
                else:
                    if source_track == dest_track and source_start_beat <= event.start_time < source_end_beat:
                        final_dest_events.append(event)
                    else:
                        deleted_event_count += 1
            dest_track.events = final_dest_events
        if source_track != dest_track:
            source_track.events = remaining_source_events
        moved_event_count = 0
        for event in events_to_move:
            event.start_time += offset_beats
            if event.start_time < 0:
                print(f"Warning: Moving event would result in a negative start time ({event.start_time:.2f} beats). Skipping and keeping original.")
                source_track.add_event(event)
                continue
            if source_track != dest_track:
                dest_track.add_event(event)
            moved_event_count += 1
        source_track.events.sort(key=lambda e: e.start_time)
        dest_track.events.sort(key=lambda e: e.start_time)
        report = []
        if moved_event_count > 0:
            report.append(f"Moved {moved_event_count} event(s) from '{source_track.name}' to '{dest_track.name}'")
        if deleted_event_count > 0:
            report.append(f"deleted {deleted_event_count} event(s) at destination")
        if not report:
            return "No notes were found in the source range to move."
        else:
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Operation complete: {', '.join(report)}."

    def copy_track_section(self, source_track_idx: int, confirmation_handler=None) -> str:
        if not self.song.tracks:
            return "No tracks to copy from."

        _input = confirmation_handler or cancellable_input

        try:
            if not 0 <= source_track_idx < len(self.song.tracks):
                return "Error: Invalid source track index."
            source_track = self.song.tracks[source_track_idx]
            if not isinstance(source_track, MidiTrack):
                return "Error: Copying events is only supported for MIDI tracks."
            start_pos_str = _input(f"Copy from position on track '{source_track.name}' (measure:beat) [default: 1:1]: ").strip()
            source_start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if source_start_beat is None: return ""
            end_pos_str = _input(f"Copy up to position on track '{source_track.name}' (measure:beat): ").strip()
            source_end_beat = self.parse_position_to_beats(end_pos_str)
            if source_end_beat is None: return ""
            if source_end_beat <= source_start_beat:
                return "Error: End position must be after the start position."
            dest_track_idx = int(_input(f"Copy to destination track index (default: {source_track_idx}): ").strip() or str(source_track_idx))
            if not 0 <= dest_track_idx < len(self.song.tracks):
                return "Error: Invalid destination track index."
            dest_track = self.song.tracks[dest_track_idx]
            if not isinstance(dest_track, MidiTrack):
                return "Error: Destination track must be a MIDI track."
            dest_pos_str = _input(f"Copy to destination position on track '{dest_track.name}' (measure:beat) [default: 1:1]: ").strip()
            destination_start_beat = self.parse_position_to_beats(dest_pos_str, default="1:1")
            if destination_start_beat is None: return ""
        except (ValueError, UserInputCancelled):
            return "\nCopy cancelled."
        range_duration_beats = source_end_beat - source_start_beat
        destination_end_beat = destination_start_beat + range_duration_beats
        offset_beats = destination_start_beat - source_start_beat
        confirm_message = f"Copy events from {start_pos_str} to {end_pos_str} on track '{source_track.name}' to start at {dest_pos_str} on track '{dest_track.name}'. Are you sure? [y/N] "
        if _input(confirm_message).lower() != 'y':
            return "Copy cancelled."
        events_at_destination = [event for event in dest_track.events if destination_start_beat <= event.start_time < destination_end_beat]
        overwrite_mode = "add"
        if events_at_destination:
            output = "There are existing notes at the destination.\n"
            while True:
                choice = _input("Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
                if choice in ['r', 'replace']:
                    overwrite_mode = "replace"
                    break
                elif choice in ['a', 'add']:
                    overwrite_mode = "add"
                    break
                else:
                    output += "Invalid choice. Please enter 'r' or 'a'.\n"
            print(output)
        copied_event_count = 0
        deleted_event_count = 0
        source_events_to_copy = [event for event in source_track.events if source_start_beat <= event.start_time < source_end_beat]
        if overwrite_mode == "replace":
            initial_dest_event_count = len(dest_track.events)
            dest_track.events = [event for event in dest_track.events if not (destination_start_beat <= event.start_time < destination_end_beat)]
            deleted_event_count = initial_dest_event_count - len(dest_track.events)
        for event in source_events_to_copy:
            new_event = deepcopy(event)
            new_event.start_time += offset_beats
            if new_event.start_time < 0:
                print(f"Warning: Copying event would result in a negative start time ({new_event.start_time:.2f} beats). Skipping event.")
                continue
            dest_track.add_event(new_event)
            copied_event_count += 1
        dest_track.events.sort(key=lambda e: e.start_time)
        report = []
        if copied_event_count > 0:
            report.append(f"Copied {copied_event_count} event(s)")
        if deleted_event_count > 0:
            report.append(f"deleted {deleted_event_count} event(s) at destination")
        if not report:
            return "No notes were found in the source range to copy."
        else:
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Operation complete: {', '.join(report)}."

    def transpose_track_section(self, track_idx: int, start_pos_str: Optional[str] = None, end_pos_str: Optional[str] = None, transpose_value_str: Optional[str] = None, confirm_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        if not 0 <= track_idx < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track = self.song.tracks[track_idx]
        if not isinstance(track, MidiTrack):
            return {"status": "error", "message": "Error: Transposing is only supported for MIDI tracks."}

        _input = confirmation_handler or cancellable_input

        # Interactive CLI mode
        if not api_mode:
            try:
                start_pos_str = _input(f"Transpose from position on track '{track.name}' (measure:beat) [default: 1:1]: ").strip()
                start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
                if start_beat is None: return {"status": "error", "message": "Invalid start position."}

                end_pos_str_in = _input(f"Transpose up to position on track '{track.name}' (measure:beat) [default: end of track]: ").strip()
                if end_pos_str_in == "":
                    end_beat = float('inf')
                else:
                    end_beat = self.parse_position_to_beats(end_pos_str_in)
                    if end_beat is None: return {"status": "error", "message": "Invalid end position."}

                if end_beat <= start_beat:
                    return {"status": "error", "message": "Error: End position must be after the start position."}

                transpose_value = int(_input("Transpose by how many semitones (e.g., 12 for up, -12 for down): ").strip())
                if not -127 <= transpose_value <= 127:
                    return {"status": "error", "message": "Error: Transposition value must be between -127 and 127."}

                events_to_transpose = [event for event in track.events if start_beat <= event.start_time < end_beat]
                if not events_to_transpose:
                    return {"status": "success", "message": "No notes found in the specified range to transpose."}

                confirm_message = f"Transpose {len(events_to_transpose)} event(s) on track '{track.name}' by {transpose_value} semitones. Are you sure? [y/N] "
                if _input(confirm_message).lower() != 'y':
                    return {"status": "cancelled", "message": "Transpose cancelled."}

            except (ValueError, UserInputCancelled):
                return {"status": "cancelled", "message": "\nTranspose cancelled."}

        # API mode
        else:
            if start_pos_str is None:
                return {"status": "prompt", "message": f"Transpose from position on track '{track.name}' (measure:beat) [default: 1:1]: ", "next_arg": "start_pos_str"}
            start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if start_beat is None: return {"status": "error", "message": "Invalid start position format."}

            if end_pos_str is None:
                return {"status": "prompt", "message": f"Transpose up to position on track '{track.name}' (measure:beat) [default: end of track]: ", "next_arg": "end_pos_str"}
            if end_pos_str == "": end_beat = float('inf')
            else:
                end_beat = self.parse_position_to_beats(end_pos_str)
                if end_beat is None: return {"status": "error", "message": "Invalid end position format."}

            if end_beat <= start_beat:
                return {"status": "error", "message": "Error: End position must be after the start position."}

            if transpose_value_str is None:
                return {"status": "prompt", "message": "Transpose by how many semitones (e.g., 12 for up, -12 for down): ", "next_arg": "transpose_value_str"}
            try:
                transpose_value = int(transpose_value_str)
            except ValueError:
                return {"status": "error", "message": "Error: Invalid number for semitones."}
            if not -127 <= transpose_value <= 127:
                return {"status": "error", "message": "Error: Transposition value must be between -127 and 127."}

            events_to_transpose = [event for event in track.events if start_beat <= event.start_time < end_beat]
            if not events_to_transpose:
                return {"status": "success", "message": "No notes found in the specified range to transpose."}

            if confirm_str is None:
                return {"status": "prompt", "message": f"Transpose {len(events_to_transpose)} event(s) on track '{track.name}' by {transpose_value} semitones. Are you sure? [y/N] ", "next_arg": "confirm_str"}
            if confirm_str.lower() != 'y':
                return {"status": "cancelled", "message": "Transpose cancelled."}

        # Common execution logic
        transposed_note_count = 0
        clamped_note_count = 0
        for event in events_to_transpose:
            for note in event.notes:
                original_pitch = note.pitch
                new_pitch = original_pitch + transpose_value
                if not 0 <= new_pitch <= 127:
                    clamped_pitch = max(0, min(127, new_pitch))
                    note.pitch = clamped_pitch
                    clamped_note_count += 1
                else:
                    note.pitch = new_pitch
                transposed_note_count += 1
        self.is_dirty = True
        message = f"Transposed {transposed_note_count} note(s) on track '{track.name}'."
        if clamped_note_count > 0:
            message += f" {clamped_note_count} note(s) were clamped to the valid MIDI pitch range (0-127)."
        return {"status": "success", "message": message}

    def assign_port(self, track_index: int, port_name: str) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Port assignment is currently only supported for MIDI tracks."
        track.output_port_name = port_name
        self.is_dirty = True
        return f"Assigned port '{port_name}' to track '{track.name}'."

    def unassign_port(self, track_index: int) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Port un-assignment is currently only supported for MIDI tracks."
        if track.output_port_name:
            output = f"Un-assigned port from track '{track.name}'."
            track.output_port_name = None
            self.is_dirty = True
            return output
        else:
            return f"Track '{track.name}' has no port assigned."

    def set_audio_player_command(self, command: str):
        """Sets the command for the external audio player."""
        self.audio_player_command = command
        self.is_dirty = True
        print(f"Audio player command set to: {command}")
        print("Note: The audio filepath will be appended to this command.")

    def set_bank(self, track_index: int, msb: int, lsb: int = 0) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Bank select is only available for MIDI tracks."
        if not 0 <= msb <= 127 and 0 <= lsb <= 127:
            return "Error: Bank values (MSB, LSB) must be between 0 and 127."
        track.bank_msb = msb
        track.bank_lsb = lsb
        self.is_dirty = True
        return f"Set bank for track '{track.name}' to MSB={msb}, LSB={lsb}."

    def set_channel(self, track_index: int, channel: int) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: MIDI channel can only be set for MIDI tracks."
        if not 1 <= channel <= 16:
            return "Error: MIDI channel must be between 1 and 16."
        track.channel = channel - 1
        self.is_dirty = True
        return f"Set MIDI channel for track '{track.name}' to {channel}."

    def set_program(self, track_index: int, program: int) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Program change is only available for MIDI tracks."
        if not 0 <= program <= 127:
            return "Error: Program number must be between 0 and 127."
        track.instrument = program
        self.is_dirty = True
        return f"Set program for track '{track.name}' to {program + 1}."

    def set_metronome_track(self, track_index: int) -> str:
        """Designates a specific MIDI track as the one to be hidden in the UI."""
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."

        track_to_set = self.song.tracks[track_index]
        if not isinstance(track_to_set, MidiTrack):
            return "Error: Only MIDI tracks can be designated as metronome tracks."

        for i, track in enumerate(self.song.tracks):
            if isinstance(track, MidiTrack):
                track.is_metronome = (i == track_index)

        self.is_dirty = True
        return f"Track '{track_to_set.name}' is now designated as the metronome track."

    def set_track_volume(self, track_index: int, volume_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        """Sets the volume for a specific audio or MIDI track."""
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track = self.song.tracks[track_index]
        if not isinstance(track, (AudioTrack, MidiTrack)):
            return {"status": "error", "message": "Error: Volume can only be set for audio or MIDI tracks."}

        if volume_str is None:
            if api_mode:
                return {"status": "prompt", "message": "Enter volume (0.0 - 1.0): ", "next_arg": "volume_str"}
            else:
                _input = confirmation_handler or cancellable_input
                try:
                    volume_str = _input("Enter volume (0.0 - 1.0): ").strip()
                except UserInputCancelled:
                    return {"status": "cancelled", "message": "Cancelled."}

        try:
            volume = float(volume_str)
            if not 0.0 <= volume <= 1.0:
                return {"status": "error", "message": "Error: Volume must be between 0.0 and 1.0."}
        except ValueError:
            return {"status": "error", "message": "Error: Invalid volume."}

        track.volume = volume
        self.is_dirty = True

        if isinstance(track, AudioTrack):
            if self.jack_manager.is_running:
                with self.jack_manager.process_lock:
                    for ap in self.jack_manager.active_audio_processes:
                        if ap.track_index == track_index:
                            self.jack_manager._send_ipc_command(ap.socket_path, {"command": ["set_property", "volume", volume * 100]})
                            break
        elif isinstance(track, MidiTrack):
            if self.jack_manager.is_running and track.output_port_name:
                port = self.jack_manager.open_ports.get(track.output_port_name)
                if port:
                    midi_volume = int(volume * 127)
                    port.send(mido.Message("control_change", channel=track.channel, control=7, value=midi_volume))

        return {"status": "success", "message": f"Volume for track '{track.name}' set to {volume:.2f}."}

    def set_track_pan(self, track_index: int, pan_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        """Sets the pan for a specific audio or MIDI track."""
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track = self.song.tracks[track_index]
        if not isinstance(track, (AudioTrack, MidiTrack)):
            return {"status": "error", "message": "Error: Pan can only be set for audio or MIDI tracks."}

        if pan_str is None:
            if api_mode:
                return {"status": "prompt", "message": "Enter pan (-1.0 to 1.0): ", "next_arg": "pan_str"}
            else:
                _input = confirmation_handler or cancellable_input
                try:
                    pan_str = _input("Enter pan (-1.0 to 1.0): ").strip()
                except UserInputCancelled:
                    return {"status": "cancelled", "message": "Cancelled."}

        try:
            pan = float(pan_str)
            if not -1.0 <= pan <= 1.0:
                return {"status": "error", "message": "Error: Pan must be between -1.0 (left) and 1.0 (right)."}
        except ValueError:
            return {"status": "error", "message": "Error: Invalid pan value."}

        track.pan = pan
        self.is_dirty = True

        if isinstance(track, AudioTrack):
            if self.jack_manager.is_running:
                with self.jack_manager.process_lock:
                    for ap in self.jack_manager.active_audio_processes:
                        if ap.track_index == track_index:
                            # Pan value from -1.0 (L) to 1.0 (R)
                            # Gains for stereo panning that preserves stereo separation
                            gain_l = min(1.0, 1.0 - pan)
                            gain_r = min(1.0, 1.0 + pan)
                            # The filter string pans left and right channels independently
                            pan_filter = f"lavfi=[pan=stereo|FL={gain_l:.2f}*FL|FR={gain_r:.2f}*FR]"
                            command = {"command": ["set_property", "af", pan_filter]}
                            self.jack_manager._send_ipc_command(ap.socket_path, command)
                            break
        elif isinstance(track, MidiTrack):
            if self.jack_manager.is_running and track.output_port_name:
                port = self.jack_manager.open_ports.get(track.output_port_name)
                if port:
                    midi_pan = int((pan + 1.0) / 2.0 * 127)
                    port.send(mido.Message("control_change", channel=track.channel, control=10, value=midi_pan))

        return {"status": "success", "message": f"Pan for track '{track.name}' set to {pan:.2f}."}

    def set_track_velocity(self, track_index: int, velocity_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        """Sets the velocity multiplier for a specific MIDI track."""
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return {"status": "error", "message": "Error: Velocity can only be set for MIDI tracks."}

        if velocity_str is None:
            if api_mode:
                return {"status": "prompt", "message": "Enter velocity multiplier (e.g., 1.0): ", "next_arg": "velocity_str"}
            else:
                _input = confirmation_handler or cancellable_input
                try:
                    velocity_str = _input("Enter velocity multiplier (e.g., 1.0): ").strip()
                except UserInputCancelled:
                    return {"status": "cancelled", "message": "Cancelled."}

        try:
            velocity = float(velocity_str)
            if not 0.0 <= velocity:
                return {"status": "error", "message": "Error: Velocity multiplier must be a positive number."}
        except ValueError:
            return {"status": "error", "message": "Error: Invalid velocity value."}

        track.velocity = velocity
        self.is_dirty = True
        return {"status": "success", "message": f"Velocity for track '{track.name}' set to {velocity:.2f}."}

    def toggle_mute(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track = self.song.tracks[track_index]
        track.is_muted = not track.is_muted
        status = "Muted" if track.is_muted else "Unmuted"
        self.is_dirty = True
        self.invalidate_song_length_cache()
        if self.playback_state != "stopped":
            current_beat = self._get_current_beat()
            self._resync_all_at_beat(current_beat)
        return {"status": "success", "message": f"Track '{track.name}' is now {status}."}

    def toggle_solo(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        target_track = self.song.tracks[track_index]
        is_being_soloed = not target_track.is_solo
        target_track.is_solo = is_being_soloed
        output = ""
        if is_being_soloed:
            for i, other_track in enumerate(self.song.tracks):
                if i == track_index:
                    continue
                if hasattr(other_track, 'is_solo') and other_track.is_solo:
                    other_track.is_solo = False
                    output += f"Track '{other_track.name}' is now Un-soloed.\n"
        status = "Solo" if target_track.is_solo else "Un-soloed"
        self.is_dirty = True
        self.invalidate_song_length_cache()
        if self.playback_state != "stopped":
            current_beat = self._get_current_beat()
            self._resync_all_at_beat(current_beat)
        output += f"Track '{target_track.name}' is now {status}."
        return {"status": "success", "message": output}

    def prime_all_tracks(self) -> str:
        """Sends the current state (program, volume, pan, etc.) for all assigned MIDI tracks."""
        if not self.jack_manager.is_running:
            return "Warning: prime_all_tracks called but JACK manager is not running. State will not be sent."

        output = "Priming all MIDI tracks with initial state...\n"
        is_any_track_soloed = any(t.is_solo for t in self.song.tracks if hasattr(t, 'is_solo'))

        for i, track in enumerate(self.song.tracks):
            if self.is_recording and self.last_record_settings and i == self.last_record_settings.get('track_index'):
                continue

            if isinstance(track, MidiTrack) and track.output_port_name:
                should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                port = self.jack_manager.open_ports.get(track.output_port_name)

                if port:
                    if should_be_audible:
                        try:
                            output += f"  - Priming MIDI track '{track.name}' to '{port.name}' on Ch: {track.channel + 1}\n"
                            if track.bank_msb is not None:
                                port.send(mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb))
                            if track.bank_lsb is not None:
                                port.send(mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb))
                            port.send(mido.Message('program_change', channel=track.channel, program=track.instrument))
                            midi_volume = int(track.volume * 127)
                            port.send(mido.Message('control_change', channel=track.channel, control=7, value=midi_volume))
                            midi_pan = int((track.pan + 1.0) / 2.0 * 127)
                            port.send(mido.Message('control_change', channel=track.channel, control=10, value=midi_pan))
                        except Exception as e:
                            output += f"  - Could not send state to port '{track.output_port_name}': {e}\n"
                    else:
                        # Silence the track if it's not supposed to be audible
                        port.send(mido.Message('control_change', channel=track.channel, control=7, value=0)) # Volume to 0
                        port.send(mido.Message('control_change', channel=track.channel, control=123, value=0)) # All notes off
                else:
                    output += f"  - Skipping track '{track.name}', port '{track.output_port_name}' not open in JackManager.\n"
        return output

    def set_control_port(self, port_name: str) -> str:
        """Sets the MIDI input port for control messages and starts listening."""
        if self.midi_listener_thread and self.midi_listener_thread.is_alive():
            return "A control port is already active. Please unset it first."
        self.control_port_name = port_name
        self._midi_listener_stop_event.clear()
        self.midi_listener_thread = threading.Thread(target=self._midi_listener_loop, args=(port_name,))
        self.midi_listener_thread.daemon = True
        self.midi_listener_thread.start()
        return f"Listening for control messages on '{port_name}'."

    def unset_control_port(self) -> str:
        """Stops listening for control messages and closes the port."""
        if not self.midi_listener_thread or not self.midi_listener_thread.is_alive():
            return "No active control port to unset."
        self._midi_listener_stop_event.set()
        if self.midi_listener_thread:
            self.midi_listener_thread.join(timeout=1.0)
        self.control_port_name = None
        return "Stopped listening for control messages."

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
                                    self._apply_midi_mapping_action(mapping, msg.value)
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
            return (cc_value / 127.0) * 2.0 - 1.0
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
            return
        current_beat = self._get_current_beat()
        point_value = self._get_parameter_value_from_cc(mapping.action, value)
        new_point = AutomationPoint(start_time=current_beat, parameter=mapping.action, value=point_value, curve='linear')
        auto_track.add_point(new_point)
        self.is_dirty = True

    def load_song(self, filepath: str) -> str:
        try:
            self.song = import_song(filepath)
            self.is_dirty = True
            self.last_project_basename = None
            self.invalidate_song_length_cache()
            return f"Successfully loaded song from '{filepath}'."
        except Exception as e:
            return f"Error loading MIDI file: {e}"

    def save_song(self, filepath: str) -> str:
        try:
            export_to_midi(self.song, filepath)
            return f"Song successfully saved to '{filepath}'."
        except Exception as e:
            return f"Error saving MIDI file: {e}"

    def save_project(self, basename: str) -> str:
        project_filepath = f"{basename}.proj.json"
        try:
            # Update the song name to match the project basename
            self.song.name = basename
            project_data = {"song": self.song, "virtual_ports": [vp.name for vp in self.virtual_ports], "control_port_name": self.control_port_name, "audio_player_command": self.audio_player_command}
            with open(project_filepath, 'w') as f:
                json.dump(project_data, f, indent=4, cls=CustomSongEncoder)
            self.is_dirty = False
            self.last_project_basename = basename
            return f"Project saved to '{project_filepath}'"
        except Exception as e:
            return f"Error saving project file: {e}"

    def load_project(self, basename: str) -> str:
        project_filepath = f"{basename}.proj.json"
        try:
            with open(project_filepath, 'r') as f:
                project_data = json.load(f, object_hook=song_decoder)
            self.song = project_data.get("song", Song(name="New Song"))
            self.audio_player_command = project_data.get("audio_player_command", self.DEFAULT_AUDIO_PLAYER_COMMAND)
            if "mplayer" in self.audio_player_command:
                print("Warning: Old 'mplayer' command found in project. Updating to 'mpv' default.")
                self.audio_player_command = self.DEFAULT_AUDIO_PLAYER_COMMAND
            self.close_virtual_ports()
            self.virtual_ports = []
            for vp_name in project_data.get("virtual_ports", []):
                self.create_virtual_port(vp_name)
            self.unset_control_port()
            control_port_name = project_data.get("control_port_name")
            if control_port_name:
                self.set_control_port(control_port_name)
            self.is_dirty = False
            self.last_project_basename = basename
            self.invalidate_song_length_cache()
            return f"Successfully loaded project from '{project_filepath}'"
        except FileNotFoundError:
            return f"Error: Project file not found at '{project_filepath}'"
        except Exception as e:
            return f"Error loading project file: {e}"

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
        self.invalidate_song_length_cache()
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

    def create_virtual_port(self, name: str) -> str:
        try:
            port = open_output(name, virtual=True)
            self.virtual_ports.append(port)
            self.is_dirty = True
            return f"Created virtual MIDI port: '{name}'"
        except Exception as e:
            return f"Error creating virtual port: {e}"

    def close_virtual_ports(self):
        for port in self.virtual_ports:
            if not port.closed:
                port.close()
        print("Virtual ports closed.")

    def delete_virtual_port(self, name: str) -> str:
        port_to_delete = None
        for vp in self.virtual_ports:
            if vp.name == name:
                port_to_delete = vp
                break
        if port_to_delete:
            output = ""
            for track in self.song.tracks:
                if isinstance(track, MidiTrack) and track.output_port_name == port_to_delete.name:
                    track.output_port_name = None
                    output += f"Un-assigned port from track '{track.name}'.\n"
            port_to_delete.close()
            self.virtual_ports.remove(port_to_delete)
            self.is_dirty = True
            output += f"Virtual port '{name}' deleted."
            return output
        else:
            return f"Error: Virtual port '{name}' not found."

    def _find_or_create_automation_track(self, target_track_index: int, parameter_name: str) -> Optional[int]:
        if not 0 <= target_track_index < len(self.song.tracks):
            return None
        target_track = self.song.tracks[target_track_index]
        new_track_name = f"{target_track.name} {parameter_name.capitalize()} Automation"
        for i, track in enumerate(self.song.tracks):
            if isinstance(track, AutomationTrack) and track.target_track_index == target_track_index and track.name == new_track_name:
                return i
        print(f"\nCreating new automation track: '{new_track_name}'")
        new_track = AutomationTrack(name=new_track_name, target_track_index=target_track_index)
        self.song.add_track(new_track)
        self.is_dirty = True
        return len(self.song.tracks) - 1

    def _get_current_beat(self) -> float:
        if self.jack_manager and self.jack_manager.is_running and self.jack_manager.jack_client:
            try:
                _ , pos_struct = self.jack_manager.jack_client.transport_query_struct()
                pos = jack.position2dict(pos_struct)

                # Frame-based calculation is more reliable than bar/beat from transport
                frame = pos.get('frame', 0)
                samplerate = self.jack_manager.jack_client.samplerate
                beats_per_second = self.song.tempo / 60.0

                if samplerate > 0 and beats_per_second > 0:
                    return (frame / samplerate) * beats_per_second

                # Fallback for safety, but the primary method is now frame-based
                beats_per_bar = pos.get('beats_per_bar', self.song.time_signature_numerator)
                bar = pos.get('bar', 1)
                beat = pos.get('beat', 1)
                tick = pos.get('tick', 0)
                ticks_per_beat = pos.get('ticks_per_beat', self.song.ticks_per_beat)
                if ticks_per_beat > 0:
                    return (bar - 1) * beats_per_bar + (beat - 1) + (tick / ticks_per_beat)
                else:
                    return (bar - 1) * beats_per_bar + (beat - 1)

            except (jack.JackError, AttributeError):
                return 0.0
        return 0.0

    def _recording_thread_main(self, target_track, start_beat, inport_name, outport_name, num_beats_to_record, original_mute_state, enable_thru):
            # Le dictionnaire stockera maintenant un tuple: (start_beat, velocity)
            open_notes = {}
            outport = None
            first_note_detected = False
            recording_start_beat = None

            try:
                if outport_name and enable_thru:
                    try:
                        outport = mido.open_output(outport_name)
                        print(f"MIDI thru activé sur le port: {outport_name}")
                    except Exception as e:
                        print(f"Warning: Impossible d'ouvrir le port de sortie: {e}")

                with mido.open_input(inport_name) as inport:
                    print(f"Port d'entrée MIDI ouvert: {inport_name}")
                    list(inport.iter_pending())
                    print(f"En attente de la première note sur '{target_track.name}'...")
                    
                    wait_start_time = time.time()
                    while not self._stop_event.is_set() and not first_note_detected:
                        if time.time() - wait_start_time > 30:
                            print("Timeout: Aucune note reçue après 30 secondes")
                            return
                        
                        msg = inport.poll()
                        if msg and msg.type == 'note_on' and msg.velocity > 0:
                            print(f"Première note détectée: {msg.note} (vélocité: {msg.velocity})")
                            first_note_detected = True
                            
                            self.play(start_beat=start_beat)
                            time.sleep(0.05)
                            
                            current_beat = self._get_current_beat()
                            recording_start_beat = current_beat
                            
                            # Mémoriser le temps ET la vélocité
                            open_notes[msg.note] = (current_beat, msg.velocity)
                            print(f"Enregistrement démarré à la position: {self._format_beats_to_position(current_beat)}")
                            
                            if outport and enable_thru:
                                outport.send(msg.copy(channel=target_track.channel))
                        
                        time.sleep(0.001)

                    if not first_note_detected:
                        return

                    print("Début de l'enregistrement en temps réel...")
                    
                    while not self._stop_event.is_set():
                        current_beat = self._get_current_beat()
                        
                        for msg in inport.iter_pending():
                            if outport and enable_thru and hasattr(msg, 'channel'):
                                outport.send(msg.copy(channel=target_track.channel))

                            if msg.type == 'note_on' and msg.velocity > 0:
                                if msg.note not in open_notes:
                                    # Mémoriser le temps ET la vélocité
                                    open_notes[msg.note] = (current_beat, msg.velocity)
                                    print(f"Note ON: {msg.note} à {self._format_beats_to_position(current_beat)}")
                            
                            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                                if msg.note in open_notes:
                                    # Récupérer le temps ET la vélocité originale
                                    note_start, original_velocity = open_notes.pop(msg.note)
                                    duration = current_beat - note_start
                                    
                                    if duration > 0:
                                        # Utiliser la vélocité originale du "note_on"
                                        note = Note(pitch=msg.note, velocity=original_velocity, duration=duration)
                                        event = Event(notes=[note], start_time=note_start)
                                        
                                        target_track.add_event(event)
                                        self.is_dirty = True
                                        
                                        print(f"Note OFF: {msg.note}, durée: {duration:.2f} beats")
                        
                        if (num_beats_to_record and recording_start_beat and 
                            current_beat >= (recording_start_beat + num_beats_to_record)):
                            print("Fin de la durée d'enregistrement atteinte")
                            self._stop_event.set()
                            break
                        
                        time.sleep(0.001)

            except Exception as e:
                print(f"Erreur lors de l'enregistrement: {e}")
                import traceback
                traceback.print_exc()
            
            finally:
                print("Nettoyage de l'enregistrement...")
                
                current_beat = self._get_current_beat()
                # Gérer les notes orphelines
                for note, (note_start, original_velocity) in open_notes.items():
                    duration = current_beat - note_start
                    if duration > 0:
                        note_obj = Note(pitch=note, velocity=original_velocity, duration=duration)
                        event = Event(notes=[note_obj], start_time=note_start)
                        target_track.add_event(event)
                        print(f"Note orpheline fermée: {note}, durée: {duration:.2f} beats")
                
                if outport:
                    try: outport.close()
                    except: pass
                
                target_track.is_muted = original_mute_state
                self.is_recording = False
                self._stop_event.clear()
                
                print(f"Enregistrement terminé. {len(target_track.events)} événements enregistrés.")

    def _start_recording_internal(self, track_index: int, start_beat: float, num_beats_to_record: Optional[float], inport_name: str, replace_notes: bool, enable_thru: bool):
            target_track = self.song.tracks[track_index]
            if not isinstance(target_track, MidiTrack):
                print("Error: Recording is only supported for MIDI tracks.")
                return
            
            if target_track.record_mode == 'OFF':
                print(f"Error: Track '{target_track.name}' is not armed for recording.")
                return
            
            # --- NOUVELLE LOGIQUE D'ÉCRASEMENT AMÉLIORÉE ---
            if replace_notes:
                # Détermine la zone à effacer (de start_beat à l'infini pour un enregistrement ouvert)
                end_beat_for_deletion = float("inf") if num_beats_to_record is None else start_beat + num_beats_to_record
                
                final_events = []
                
                for event in target_track.events:
                    event_start_time = event.start_time
                    
                    # Cas 1: L'événement commence AVANT le point d'enregistrement.
                    # On vérifie si ses notes doivent être raccourcies.
                    if event_start_time < start_beat:
                        notes_to_keep_in_event = []
                        for note in event.notes:
                            note_end_time = event_start_time + note.duration
                            # Si la note se termine avant, on la garde telle quelle.
                            if note_end_time <= start_beat:
                                notes_to_keep_in_event.append(note)
                            # Si la note déborde sur la zone, on la tronçonne.
                            elif event_start_time < start_beat < note_end_time:
                                note.duration = start_beat - event_start_time
                                notes_to_keep_in_event.append(note)
                        
                        event.notes = notes_to_keep_in_event
                        final_events.append(event)

                    # Cas 2: L'événement commence DANS la zone à effacer.
                    # On supprime ses notes mais on garde les messages CC éventuels.
                    elif start_beat <= event_start_time < end_beat_for_deletion:
                        event.notes.clear()
                        if event.cc_messages:
                            final_events.append(event)
                    
                    # Cas 3: L'événement commence APRÈS la zone (peu probable ici).
                    else:
                        final_events.append(event)

                # Nettoyage final : on enlève les événements devenus complètement vides.
                target_track.events = [e for e in final_events if e.notes or e.cc_messages]
                print(f"Notes existantes effacées/tronquées à partir de la position {self._format_beats_to_position(start_beat)}.")

            outport_name = target_track.output_port_name
            original_mute_state = target_track.is_muted
            if replace_notes:
                target_track.is_muted = True
                
            self.is_recording = True
            self.recording_thread = threading.Thread(target=self._recording_thread_main, args=(target_track, start_beat, inport_name, outport_name, num_beats_to_record, original_mute_state, enable_thru))
            self.recording_thread.daemon = True
            self.recording_thread.start()

    def prepare_recording(self, track_idx: int, start_beat: float, inport_name: str):
        """Prépare l'enregistrement qui sera déclenché par la première note MIDI"""
        if not 0 <= track_idx < len(self.song.tracks):
            return "Error: Invalid track index."
        
        target_track = self.song.tracks[track_idx]
        if not isinstance(target_track, MidiTrack):
            return "Error: Recording is only supported for MIDI tracks."
        
        # Vérifier que la piste est armée
        if target_track.record_mode == 'OFF':
            return f"Error: Track '{target_track.name}' is not armed for recording."
        
        # Déterminer le port d'entrée
        if inport_name is None:
            if self.default_record_port:
                inport_name = self.default_record_port
            else:
                # Trouver un port d'entrée par défaut
                input_ports = get_input_names()
                if input_ports:
                    inport_name = input_ports[0]
                else:
                    return "Error: No MIDI input ports available and no default port set."  
        
        # Sauvegarder les paramètres pour le démarrage différé
        self.last_record_settings = {
            "track_index": track_idx, 
            "start_beat": start_beat, 
            "num_beats_to_record": None,  # Pas de limite de durée
            "inport_name": inport_name, 
            "replace_notes": (target_track.record_mode == 'OVERWRITE'),
            "enable_thru": True
        }
        
        # Marquer que l'enregistrement est prêt mais pas encore démarré
        self.is_recording = True
        self._stop_event.clear()
        
        mode_text = "OVERWRITE" if target_track.record_mode == 'OVERWRITE' else "KEEP"
        start_pos = self._format_beats_to_position(start_beat)
        return f"Recording prepared on track '{target_track.name}' at {start_pos} in {mode_text} mode. Waiting for first MIDI note..."
    
    def record_track(self, track_idx: Optional[int] = None, start_beat: Optional[float] = None, num_beats_to_record: Optional[float] = None, inport_name: Optional[str] = None, replace_notes: Optional[bool] = None, enable_thru: bool = True):
            """Starts recording immediately or uses prepared settings."""
            
            if self.playback_state != "stopped":
                return "Error: Please stop playback before starting a new recording."
            
            # This is a new recording session initiated from the UI or command line
            if track_idx is not None:
                # --- Parameter Validation and Setup ---
                if not 0 <= track_idx < len(self.song.tracks):
                    return "Error: Invalid track index."
                
                target_track = self.song.tracks[track_idx]
                if not isinstance(target_track, MidiTrack):
                    return "Error: Recording is only supported for MIDI tracks."
                    
                if target_track.record_mode == 'OFF':
                    return f"Error: Track '{target_track.name}' is not armed for recording."
                
                # Determine the input port if not explicitly provided
                if inport_name is None:
                    if self.default_record_port:
                        inport_name = self.default_record_port
                    else:
                        input_ports = get_input_names()
                        if not input_ports:
                            return "Error: No MIDI input ports available and no default port set."
                        inport_name = input_ports[0]  # Fallback to the first available port
                
                if start_beat is None:
                    start_beat = 0.0
                    
                # Determine if existing notes should be replaced based on the track's record mode
                should_replace_notes = target_track.record_mode == 'OVERWRITE'
                if replace_notes is not None:
                    # Allow the function call to override the track's mode
                    should_replace_notes = replace_notes
                    
                # Save these settings for a potential re-record (`record_bis`)
                self.last_record_settings = {
                    "track_index": track_idx, 
                    "start_beat": start_beat, 
                    "num_beats_to_record": num_beats_to_record,
                    "inport_name": inport_name, 
                    "replace_notes": should_replace_notes,
                    "enable_thru": enable_thru
                }

                # --- Start the Recording Thread ---
                self._stop_event.clear()
                self._start_recording_internal(
                    track_index=track_idx, 
                    start_beat=start_beat, 
                    num_beats_to_record=num_beats_to_record,
                    inport_name=inport_name, 
                    replace_notes=should_replace_notes, 
                    enable_thru=enable_thru
                )
                
                mode_text = "OVERWRITE" if should_replace_notes else "KEEP"
                start_pos = self._format_beats_to_position(start_beat)
                return f"Recording armed on track '{target_track.name}' at {start_pos} in {mode_text} mode. Waiting for first MIDI note..."

            # This block handles re-recording using previous settings ('record_bis')
            else:
                if self.last_record_settings is None:
                    return "Error: No recording settings prepared. Use 'record' with a track index first."
                
                settings = self.last_record_settings.copy()
                
                # Re-check the track's record mode in case it changed
                target_track = self.song.tracks[settings['track_index']]
                if isinstance(target_track, MidiTrack):
                    settings['replace_notes'] = (target_track.record_mode == 'OVERWRITE')

                self._stop_event.clear()
                self._start_recording_internal(**settings)
                return "Re-recording with last settings..."
        

    def set_record_mode(self, track_index: int, mode: str) -> str:
        """Définit le mode d'enregistrement pour une piste MIDI."""
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Record mode can only be set for MIDI tracks."
        
        if mode not in ['OFF', 'OVERWRITE', 'KEEP']:
            return "Error: Invalid record mode. Must be 'OFF', 'OVERWRITE', or 'KEEP'."
        
        # Sauvegarder l'ancien mode pour le log
        old_mode = track.record_mode
        
        # Désactiver les autres pistes si on active celle-ci
        if mode != 'OFF':
            for i, other_track in enumerate(self.song.tracks):
                if (isinstance(other_track, MidiTrack) and 
                    i != track_index and 
                    other_track.record_mode != 'OFF'):
                    other_track.record_mode = 'OFF'
                    print(f"DEBUG: Disabled track {i} '{other_track.name}' (was {other_track.record_mode})")
        
        track.record_mode = mode
        self.is_dirty = True
        
        mode_descriptions = {
            'OFF': 'Piste désactivée',
            'OVERWRITE': 'Écrase les notes existantes', 
            'KEEP': 'Conserve les notes existantes'
        }
        
        return f"Track '{track.name}' record mode changed from {old_mode} to {mode}: {mode_descriptions[mode]}"

    def record_bis(self, replace_notes: bool):
        """Re-records using the last saved parameters."""
        if self.playback_state != "stopped":
            return "Error: Please stop playback before starting a new recording."
        if self.last_record_settings is None:
            return "Error: No previous recording settings found. Use 'record' first."

        settings = self.last_record_settings.copy()
            
        # Vérifier le mode actuel de la piste
        track_index = settings['track_index']
        if 0 <= track_index < len(self.song.tracks):
            target_track = self.song.tracks[track_index]
            if isinstance(target_track, MidiTrack):
                # Utiliser le mode actuel de la piste
                settings['replace_notes'] = (target_track.record_mode == 'OVERWRITE')
        
        if 'enable_thru' not in settings:
            settings['enable_thru'] = True
            
        self._stop_event.clear()
        self._start_recording_internal(**settings)
        return "Re-recording with last used settings..."        

    def _calculate_song_length_in_beats(self) -> float:
        """Calculates the total length of the song in beats, considering both MIDI and audio tracks."""
        max_beats = 0.0
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
        for track in self.song.tracks:
            if isinstance(track, AudioTrack):
                try:
                    is_any_track_soloed = any(t.is_solo for t in self.song.tracks)
                    should_play = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                    if not should_play:
                        continue

                    duration_ms = self.audio_track_duration_ms.get(track.filepath)
                    if duration_ms is None:
                        with suppress_stdout_stderr():
                            segment = AudioSegment.from_file(track.filepath)
                        duration_ms = len(segment)
                        self.audio_track_duration_ms[track.filepath] = duration_ms

                    duration_beats = (duration_ms / 1000.0) * (self.song.tempo / 60.0)
                    track_end_beat = track.start_time + duration_beats
                    if track_end_beat > max_beats:
                        max_beats = track_end_beat
                except Exception as e:
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
        target_track_index = auto_track.target_track_index
        if not 0 <= target_track_index < len(self.song.tracks):
            return []
        target_track = self.song.tracks[target_track_index]
        param_map = {"vol": {"type": "midi_cc", "control": 7}, "pan": {"type": "midi_cc", "control": 10}, "vel": {"type": "velocity_multiplier"}, "prog": {"type": "program_change"}, **{f"cc{i}": {"type": "midi_cc", "control": i} for i in range(128)}}
        for i, start_point in enumerate(points):
            param_config = param_map.get(start_point.parameter.lower())
            if not param_config:
                continue
            generated_events.append({"time": start_point.start_time, "target_track_index": target_track_index, "parameter": start_point.parameter, "param_config": param_config, "value": start_point.value})
            if i + 1 >= len(points) or start_point.curve == "none":
                continue
            end_point = points[i+1]
            if start_point.parameter != end_point.parameter:
                continue
            start_time = start_point.start_time
            end_time = end_point.start_time
            start_val = start_point.value
            end_val = end_point.value
            time_diff = end_time - start_time
            if time_diff <= 0:
                continue
            granularity = 1.0 / 16.0
            num_steps = int(time_diff / granularity)
            if num_steps <= 1:
                continue
            t = np.linspace(0, 1, num_steps, endpoint=False)[1:]
            time_steps = start_time + t * time_diff
            value_range = end_val - start_val
            value_steps = None
            if start_point.curve == "linear":
                value_steps = start_val + t * value_range
            elif start_point.curve == "ease-in":
                value_steps = start_val + (t**2) * value_range
            elif start_point.curve == "ease-out":
                value_steps = start_val + (1 - (1 - t)**2) * value_range
            elif start_point.curve in ["ease-in-out", "sine"]:
                value_steps = start_val + (0.5 * (1 - np.cos(np.pi * t))) * value_range
            else:
                continue
            for step_time, step_value in zip(time_steps, value_steps):
                generated_events.append({"time": step_time, "target_track_index": target_track_index, "parameter": start_point.parameter, "param_config": param_config, "value": step_value})
        return generated_events

    def _resync_all_at_beat(self, beat: float):
        """
        Resynchronizes all tracks to a specific beat. This is used when the audible
        state of tracks changes mid-playback (e.g., via solo/mute) to prevent timing drift.
        """
        with self.jack_manager.sync_lock:
            # 1. Sync the internal playhead and event indices for all tracks
            self.jack_manager._sync_playhead_to_beat(beat)

        # Regenerate automation events to reflect the new solo/mute state
        self.jack_manager._prepare_automation_events()

        # 2. Seek all audio players to the correct time
        self.jack_manager.seek_audio_to_beat(beat)

        # 3. Prime all audible tracks with their correct state
        is_any_track_soloed = any(t.is_solo for t in self.song.tracks if hasattr(t, 'is_solo'))

        for i, track in enumerate(self.song.tracks):
            should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted

            if isinstance(track, AudioTrack):
                with self.jack_manager.process_lock:
                    active_process = next((p for p in self.jack_manager.active_audio_processes if p.track_index == i), None)
                    if active_process:
                        self.jack_manager._send_ipc_command(active_process.socket_path, {"command": ["set_property", "mute", not should_be_audible]})

            elif isinstance(track, MidiTrack):
                port = self.jack_manager.open_ports.get(track.output_port_name)
                if not port:
                    continue

                if not should_be_audible:
                    port.send(mido.Message('control_change', channel=track.channel, control=7, value=0)) # Volume to 0
                    port.send(mido.Message('control_change', channel=track.channel, control=123, value=0)) # All notes off
                else:
                    # Prime the track with its current state
                    try:
                        if track.bank_msb is not None:
                            port.send(mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb))
                        if track.bank_lsb is not None:
                            port.send(mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb))
                        port.send(mido.Message('program_change', channel=track.channel, program=track.instrument))
                        midi_volume = int(track.volume * 127)
                        port.send(mido.Message('control_change', channel=track.channel, control=7, value=midi_volume))
                        midi_pan = int((track.pan + 1.0) / 2.0 * 127)
                        port.send(mido.Message('control_change', channel=track.channel, control=10, value=midi_pan))
                    except Exception as e:
                        print(f"Warning: Could not prime MIDI track '{track.name}': {e}", file=sys.stderr)


    def play(self, start_beat: Optional[float] = None):
        print(f"DEBUG PLAY: play called with start_beat={start_beat}")
        print(f"DEBUG PLAY: loop_enabled={self.loop_enabled}, loop_start={self.loop_start_beat}, loop_end={self.loop_end_beat}")
        print(f"DEBUG PLAY: playback_state={self.playback_state}")
        print(f"DEBUG PLAY: jack_transport state={self.jack_client.transport_state if hasattr(self, 'jack_client') else 'No jack client'}")        
        """
        Starts or seeks the JACK transport.
        If start_beat is provided, it seeks the transport to that position.
        It then ensures the transport is rolling.
        """
        # 1. Ensure client is running.
        if not self.jack_manager.is_running:
            print("JACK client not active. Starting...")
            self.jack_manager.start()
            time.sleep(0.1) # Give it a moment to stabilize
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            print("Error: Could not start JACK client.")
            return

        # 2. Prime tracks with their initial state (program, volume, pan, etc.)
        self.prime_all_tracks()

        # 3. Determine target beat and reposition transport if necessary
        current_beat = 0.0
        try:
            _ , pos_struct = self.jack_manager.jack_client.transport_query_struct()
            pos_dict = jack.position2dict(pos_struct)

            if start_beat is None:
                # If no start_beat, use current transport position
                frame = pos_dict.get('frame', 0)
                samplerate = self.jack_manager.jack_client.samplerate
                beats_per_second = self.song.tempo / 60.0
                if samplerate > 0 and beats_per_second > 0:
                    current_beat = (frame / samplerate) * beats_per_second
            else:
                # If start_beat is given, use it and reposition transport
                current_beat = start_beat
                beats_per_second = self.song.tempo / 60.0
                samplerate = self.jack_manager.jack_client.samplerate
                if beats_per_second > 0 and samplerate > 0:
                    target_frame = int((current_beat / beats_per_second) * samplerate)
                    pos_struct.frame = target_frame
                    self.jack_manager.jack_client.transport_reposition_struct(pos_struct)
                    print(f"Seeking JACK transport to {self._format_beats_to_position(current_beat)}.")

        except jack.JackError as e:
            print(f"Error querying or seeking JACK transport: {e}")
            return

        # 4. Sync internal state and audio players to the determined beat
        self.jack_manager._sync_playhead_to_beat(current_beat)
        self.jack_manager.seek_audio_to_beat(current_beat)

        # 5. Start the transport rolling and un-pause audio.
        try:
            if self.jack_manager.jack_client.transport_state != jack.ROLLING:
                self.jack_manager.jack_client.transport_start()
            self.jack_manager.set_all_audio_pause_state(False)
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
                self.jack_manager.set_all_audio_pause_state(True) # Pause audio
                print("JACK transport stopped.")
                self.playback_state = "paused"
            else:
                self.jack_manager.jack_client.transport_start()
                self.jack_manager.set_all_audio_pause_state(False) # Un-pause audio
                print("JACK transport started.")
                self.playback_state = "playing"
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
        if self.jack_manager.is_running and self.jack_manager.jack_client:
            try:
                if self.jack_manager.jack_client.transport_state == jack.ROLLING:
                    self.jack_manager.jack_client.transport_stop()
                    print("JACK transport stopped.")
                    time.sleep(0.1)
            except jack.JackError as e:
                print(f"Error stopping JACK transport: {e}")
        print("Stopping JACK client...")
        self.jack_manager.stop()
        self.playback_state = "stopped"
        self._all_notes_off()
        print("Session stopped.")

    def set_loop_range(self, start_pos_str: str, end_pos_str: str) -> str:
        """Sets the loop range without starting playback."""
        start_beat = self.parse_position_to_beats(start_pos_str)
        if start_beat is None:
            return "Error: Invalid start position."
        
        end_beat = self.parse_position_to_beats(end_pos_str)
        if end_beat is None:
            return "Error: Invalid end position."
        
        if end_beat <= start_beat:
            return "Error: End position must be after the start position."
        
        self.loop_start_beat = start_beat
        self.loop_end_beat = end_beat
        self.loop_enabled = True
        self.is_dirty = True
        
        start_pos = self._format_beats_to_position(start_beat)
        end_pos = self._format_beats_to_position(end_beat)
        return f"Loop range set from {start_pos} to {end_pos}. Use 'play' to start playback."

    def restart(self):
        """Restarts playback from the beginning."""
        print("When slaved to JACK, playback must be controlled by the JACK transport master.")
        print("Use your master application to return to the start of the song.")

    def seek(self, amount_str: str) -> str:
        """Seeks the JACK transport by a relative amount of measures or beats."""
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            return "Error: JACK is not running. Cannot seek."

        try:
            if not amount_str.startswith(('+', '-')):
                amount_str = '+' + amount_str

            sign = 1 if amount_str.startswith('+') else -1
            unit = amount_str[-1].lower()
            value = int(amount_str[1:-1])

            if unit not in ['m', 'b']:
                raise ValueError("Invalid unit. Use 'm' for measures or 'b' for beats.")

            offset_beats = 0
            if unit == 'm':
                offset_beats = sign * value * self.song.time_signature_numerator
            else: # unit == 'b'
                offset_beats = sign * value

            # Get current position
            _, pos_struct = self.jack_manager.jack_client.transport_query_struct()
            pos_dict = jack.position2dict(pos_struct)
            current_frame = pos_dict.get('frame', 0)

            samplerate = self.jack_manager.jack_client.samplerate
            beats_per_second = self.song.tempo / 60.0

            if samplerate <= 0 or beats_per_second <= 0:
                return "Error: Cannot determine current position (invalid transport state)."

            current_beat = (current_frame / samplerate) * beats_per_second
            new_beat = current_beat + offset_beats
            if new_beat < 0:
                new_beat = 0.0

            # Reposition JACK transport
            target_frame = int((new_beat / beats_per_second) * samplerate)
            pos_struct.frame = target_frame
            self.jack_manager.jack_client.transport_reposition_struct(pos_struct)

            # Manually sync sequencer and audio players because transport_reposition does not trigger the timebase callback
            self.jack_manager._sync_playhead_to_beat(new_beat)
            self.jack_manager.seek_audio_to_beat(new_beat)

            return f"Seeked to position {self._format_beats_to_position(new_beat)}."

        except (ValueError, IndexError):
            return "Error: Invalid seek format. Use +/-<number><m|b> (e.g., '+1m', '-4b')."

    def send_cc_message(self, port_name: str, channel: int, control: int, value: int) -> str:
        """Sends a single CC message to a specified port."""
        port = self.open_ports.get(port_name)
        if not port:
            vp = next((p for p in self.virtual_ports if p.name == port_name), None)
            if vp:
                port = vp
        is_temp_port = False
        if not port:
            try:
                port = open_output(port_name)
                is_temp_port = True
            except Exception as e:
                return f"Error: Could not open MIDI port '{port_name}': {e}"
        if port:
            try:
                if not 0 <= channel <= 15:
                    return "Error: Channel must be between 0 and 15."
                if not 0 <= control <= 127:
                    return "Error: CC number must be between 0 and 127."
                if not 0 <= value <= 127:
                    return "Error: CC value must be between 0 and 127."
                msg = mido.Message('control_change', channel=channel, control=control, value=value)
                port.send(msg)
                time.sleep(0.01)
                return f"Sent CC message to {port_name}: Ch={channel+1}, CC={control}, Val={value}"
            except Exception as e:
                return f"Error sending CC message: {e}"
            finally:
                if is_temp_port and port:
                    port.close()
        return ""