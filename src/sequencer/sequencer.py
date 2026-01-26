from .midi_export import export_to_midi
from .midi_import import import_song
from .midi_import_project import import_midi_to_project
from .midi_export_project import export_midi_from_project
from .models import (AnyTrack, AudioTrack, AutomationTrack, AutomationPoint,
                    CCMessage, Event, MidiTrack, Note, Song, MidiMapping,
                    is_midi_track, is_audio_track, is_automation_track)
from .config import MidiConfig
from .config_manager import ConfigManager
from .terminal_input import cancellable_input, UserInputCancelled
from .jack_manager import JackManager
from .serialization import CustomSongEncoder, song_decoder
from pydub import AudioSegment
from copy import deepcopy
from dataclasses import asdict, is_dataclass, fields
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

from kivy.properties import NumericProperty, StringProperty, BooleanProperty, ObjectProperty
from kivy.event import EventDispatcher
from kivy.clock import Clock

class Sequencer(EventDispatcher):
    current_beat = NumericProperty(0)
    last_beat_update_time = NumericProperty(0)
    current_routing_index = NumericProperty(-1)
    playback_state = StringProperty("stopped")
    is_recording = BooleanProperty(False)
    ui_end_pos_str = StringProperty("")
    song_structure_changed = NumericProperty(0)
    live_notes = ObjectProperty({}) # Dict[int, List[int]] track_idx -> list of notes
    DEFAULT_AUDIO_PLAYER_COMMAND = "mpv --really-quiet --no-video --idle --af=rubberband --audio-device=jack"

    def __init__(self, tempo: int = 120, gui_mode=False):
        super().__init__()
        self.gui_mode = gui_mode
        self.song = Song(name="New Song", tempo=tempo)
        self.config_manager = ConfigManager()
        self.midi_config = MidiConfig("config/midi_mappings.json")
        self.jack_manager = JackManager(self)
        self.midi_listener_thread = None
        self._midi_listener_stop_event = threading.Event()
        self._transport_control_thread = None
        self._transport_control_stop_event = threading.Event()
        self.control_port_name: Optional[str] = None
        self.open_ports = {}
        self.virtual_ports = []
        self.temporary_ports = []
        self.audio_player_command: str = self.DEFAULT_AUDIO_PLAYER_COMMAND
        self.carla_process: Optional[subprocess.Popen] = None

        # New properties to hold the start/end positions from the UI
        self.ui_start_pos_str = "1:1"
        self.ui_end_pos_str = ""

        self.pause_beat = 0.0
        self.rewind_beat = 0.0
        self.recording_thread = None
        self._recording_merge_event = None
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
        
        # ⚠️ NOUVEAU : Cache pour éviter de recharger les fichiers audio
        self._audio_duration_cache: Dict[str, float] = {} 
        
        # Cache pour la longueur totale du morceau (dépend de l'audio)
        self._cached_song_length_beats: Optional[float] = None

        self.track_overrides: Dict[int, MidiTrack] = {}
        self.last_play_start_beat: Optional[float] = None

        self.bind(song_structure_changed=self._update_current_routing)

        if self.gui_mode:
            Clock.schedule_interval(self._poll_engine_state, 1/30.0)

    def _poll_engine_state(self, dt):
        """Polls the JACK engine for state changes and updates Kivy properties."""
        if not self.jack_manager or not self.jack_manager.is_running:
            return

        # 1. Update current beat
        new_beat = self.jack_manager._last_beat_rt
        if not math.isclose(self.current_beat, new_beat, abs_tol=0.001):
            self.current_beat = new_beat
            self.last_beat_update_time = time.perf_counter()
            self._update_current_routing()

        # 2. Update playback state from engine
        # Use authoritative engine state to drive UI
        engine_state = self.jack_manager._last_transport_state_rt
        if engine_state == jack.STOPPED and self.playback_state != "stopped":
            self.playback_state = "stopped"
            # Silence notes if engine stopped unexpectedly
            self.jack_manager.silence_all_midi_notes()
        elif engine_state == jack.ROLLING and self.playback_state == "stopped":
            # Engine started elsewhere or slaved?
            self.playback_state = "playing"

        # 3. Process Pending Commands from RT (MIDI controller)
        if self.jack_manager._pending_play_pause:
            self.jack_manager._pending_play_pause = False
            self.process_transport_command("play_pause")

        if self.jack_manager._pending_stop:
            self.jack_manager._pending_stop = False
            self.process_transport_command("stop")

        if self.jack_manager._pending_record:
            self.jack_manager._pending_record = False
            self.process_transport_command("record")

        while self.jack_manager._pending_mappings:
            try:
                mapping, value = self.jack_manager._pending_mappings.popleft()
                self._apply_midi_mapping_action(mapping, value)
            except IndexError: break

    def _update_current_routing(self, *args):
        """Updates the current_routing_index property based on the current beat."""
        if self.jack_manager:
            idx = self.jack_manager._get_input_routing_value(self.current_beat)
            if idx is not None:
                if self.current_routing_index != idx:
                    self.current_routing_index = idx
            else:
                if self.current_routing_index != -1:
                    self.current_routing_index = -1

    def _start_carla_process(self, carla_project_path: Optional[str] = None):
        """
        Starts the Carla process. If a valid project path is provided, it opens
        that project. Otherwise, it starts an empty Carla instance.
        It always stops a previous instance before starting a new one.
        """
        self._stop_carla_process()

        command = ["carla"]
        if carla_project_path and os.path.exists(carla_project_path):
            print(f"Starting Carla with project: {carla_project_path}")
            command.append(carla_project_path)
        else:
            print("Starting a new empty Carla instance.")

        try:
            self.carla_process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            print("Error: 'carla' command not found. Please ensure Carla is installed and in your system's PATH.", file=sys.stderr)
            self.carla_process = None
        except Exception as e:
            print(f"An error occurred while starting Carla: {e}", file=sys.stderr)
            self.carla_process = None

    def _stop_carla_process(self):
        """Stops the managed Carla process if it is running."""
        if self.carla_process and self.carla_process.poll() is None:
            print("Stopping Carla process...")
            self.carla_process.terminate()
            try:
                self.carla_process.wait(timeout=5.0)
                print("Carla process stopped.")
            except subprocess.TimeoutExpired:
                print("Carla did not terminate gracefully. Forcing shutdown.", file=sys.stderr)
                self.carla_process.kill()
            self.carla_process = None

    def process_transport_command(self, command: str):
        """
        Centralized method to handle all transport commands (play, pause, stop, record)
        from both the UI and MIDI controllers to ensure consistent behavior.
        """
        if command == "play_pause":
            # If armed for recording, pressing play should start the recording.
            if self.is_recording and self.playback_state == 'stopped':
                # The recording thread is already waiting for the transport to start.
                # We find the start_beat from the last recording settings.
                start_beat = 0.0
                if self.last_record_settings and 'start_beat' in self.last_record_settings:
                    start_beat = self.last_record_settings['start_beat']
                self.play(start_beat=start_beat)
                return

            # If already playing, do nothing. If paused, resume.
            if self.playback_state == "playing":
                self.pause()
                return
            if self.playback_state == "paused":
                self.pause() # The pause method handles both pause and resume
                return

            # --- Start new playback ---
            # Set the rewind position from the UI, but don't use it for starting playback yet.
            start_pos_for_rewind = self.ui_start_pos_str or "1:1"
            rewind_beat = self.parse_position_to_beats(start_pos_for_rewind)
            if rewind_beat is not None:
                self.rewind_beat = rewind_beat
            else:
                print(f"Warning: Invalid rewind position '{start_pos_for_rewind}', defaulting to 0.")
                self.rewind_beat = 0.0

            # --- MODIFIED: Prioritize the UI start position text field ---
            # The start beat is now determined by the UI's 'start_pos' field,
            # which is also updated by clicking on the ruler.
            start_pos_str = self.ui_start_pos_str or "1:1"
            start_beat = self.parse_position_to_beats(start_pos_str)
            if start_beat is None:
                print(f"Warning: Invalid start position '{start_pos_str}', defaulting to 0.")
                start_beat = 0.0

            end_pos = self.ui_end_pos_str
            if self.loop_enabled:
                self.set_loop_range(self.ui_start_pos_str, end_pos)
                self.play(start_beat=start_beat)
            else:
                end_beat = self.parse_position_to_beats(end_pos) if end_pos else None
                self.play_range_enabled = True
                self.play_range_start_beat = start_beat
                self.play_range_end_beat = end_beat if end_beat is not None else self.get_song_length_in_beats()
                self.play(start_beat=start_beat)

        elif command == "stop":
            # If armed for recording but not yet playing, "stop" should just cancel the armed state.
            if self.is_recording and self.playback_state == 'stopped':
                self.is_recording = False
                print("Recording armed state cancelled.")
            else:
                self.stop()

        elif command == "record":
            if self.is_recording:
                self.stop()
            else:
                self.start_midi_recording() # Assumes a track is armed

    def invalidate_caches(self):
            """Invalide tous les caches qui dépendent de la structure du morceau ou des données audio."""
            self._audio_duration_cache.clear()
            self._cached_song_length_beats = None
            # N'oubliez pas d'appeler cette fonction chaque fois que le tempo, le chemin d'un fichier audio, 
            # ou un événement de piste est modifié (ajout/suppression).

    def _transport_control_listener_loop(self, port_name: str):
        """
        A dedicated thread that listens for transport control MIDI messages based on the loaded configuration.
        """
        try:
            with mido.open_input(port_name) as inport:
                while not self._transport_control_stop_event.is_set():
                    for msg in inport.iter_pending():
                        if msg.type == 'control_change':
                            control = msg.control
                            value = msg.value

                            # --- Handle Transport Controls ---
                            if value == 127:
                                if control == self.midi_config.get_transport_cc("play_pause"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("play_pause"))
                                elif control == self.midi_config.get_transport_cc("stop"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("stop"))
                                elif control == self.midi_config.get_transport_cc("record_arm"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("record"))

                            # --- Handle Volume Sliders & Solo Buttons ---
                            for i in range(len(self.song.tracks)):
                                # Volume
                                if control == self.midi_config.get_volume_slider_cc(i):
                                    volume_value = value / 127.0
                                    Clock.schedule_once(lambda dt, ti=i, vol=volume_value: self.set_track_volume(ti, vol, api_mode=True))
                                    break # Found a match, no need to check other tracks for this CC

                                # Solo
                                if control == self.midi_config.get_track_solo_button_cc(i):
                                    track = self.song.tracks[i]
                                    is_solo = getattr(track, 'is_solo', False)
                                    if (value == 127 and not is_solo) or (value == 0 and is_solo):
                                        Clock.schedule_once(lambda dt, ti=i: self.toggle_solo(ti))
                                    break # Found a match

                    time.sleep(0.01)
        except Exception as e:
            print(f"\nError in transport control listener for port '{port_name}': {e}")

    def reload_midi_mappings(self, filepath: str) -> str:
        """Loads a new MIDI mapping file and restarts the listener if necessary."""
        self.midi_config.load_mappings(filepath)

        # If a transport control port is active, restart it to apply the new mappings
        if self.default_record_port and self._transport_control_thread and self._transport_control_thread.is_alive():
            print("Restarting MIDI transport control listener to apply new mappings...")
            return self.set_default_record_port(self.default_record_port)

        return f"MIDI mappings loaded from {filepath}. No transport listener was active."

    def set_default_record_port(self, port_name: str) -> str:
        """
        Sets the default MIDI input port for recording and transport controls.
        Manages the lifecycle of the transport control listener thread.
        """
        try:
            input_ports = get_input_names()
            if port_name not in input_ports:
                return f"Error: MIDI input port '{port_name}' not found."

            # Stop any existing listener before starting a new one
            if self._transport_control_thread and self._transport_control_thread.is_alive():
                self._transport_control_stop_event.set()
                self._transport_control_thread.join(timeout=1.0)

            self.default_record_port = port_name
            self.is_dirty = True

            # Start the new listener thread (only if JACK is not running)
            if not self.jack_manager.is_running:
                self._transport_control_stop_event.clear()
                self._transport_control_thread = threading.Thread(
                    target=self._transport_control_listener_loop,
                    args=(port_name,),
                    daemon=True
                )
                self._transport_control_thread.start()
            else:
                print(f"Note: JACK is active. Controls should be connected to 'Clavier' port.")

            return f"Default record and transport control port set to: {port_name}"
        except Exception as e:
            return f"Error setting record port: {e}"

    def start_midi_recording(self):
        """
        Starts a recording from a MIDI command. Finds the armed track and starts recording.
        """
        # If JACK is not running, try to start it (as it's our primary MIDI bridge)
        if not self.jack_manager.is_running:
            print("Starting JACK manager for recording...")
            self.jack_manager.start()
            time.sleep(0.2)

        if not self.default_record_port and not self.jack_manager.is_running:
            print("Error: No MIDI input port selected for recording and JACK is not running.")
            return

        armed_track_index = None
        for i, track in enumerate(self.song.tracks):
            if is_midi_track(track) and getattr(track, 'record_mode', 'OFF') != 'OFF':
                if armed_track_index is not None:
                    print("Error: Multiple tracks are armed for recording. Please arm only one.")
                    return
                armed_track_index = i

        if armed_track_index is None:
            print("Error: No track is armed for recording.")
            return

        # Use the UI's start position for consistency with play commands
        start_pos = self.ui_start_pos_str or "1:1"
        start_beat = self.parse_position_to_beats(start_pos)
        if start_beat is None:
            start_beat = 0.0 # Fallback

        self.record_track(
            track_idx=armed_track_index,
            start_beat=start_beat,
            inport_name=self.default_record_port
        )

    def _merge_recorded_events(self, dt):
        """Polls recorded events from JackManager and merges them into tracks."""
        any_added = False
        while True:
            try:
                event_data = self.jack_manager._recorded_events_to_merge.popleft()
                track_idx = event_data['track_idx']
                if not 0 <= track_idx < len(self.song.tracks):
                    continue

                track = self.song.tracks[track_idx]
                if not is_midi_track(track):
                    continue

                if event_data['type'] == 'note':
                    note = Note(pitch=event_data['pitch'], velocity=event_data['velocity'], duration=event_data['duration'])
                    event = Event(start_time=event_data['start_time'], notes=[note])
                    track.add_event(event)
                elif event_data['type'] == 'cc':
                    cc = CCMessage(control=event_data['control'], value=event_data['value'])
                    # Find or create event at this time
                    existing_event = next((e for e in track.events if math.isclose(e.start_time, event_data['start_time'], abs_tol=0.001)), None)
                    if existing_event:
                        existing_event.cc_messages.append(cc)
                    else:
                        event = Event(start_time=event_data['start_time'], cc_messages=[cc])
                        track.add_event(event)

                any_added = True
            except IndexError:
                break

        if any_added:
            self.is_dirty = True
            self.invalidate_song_length_cache()
            self.song_structure_changed += 1

    def get_default_record_port(self) -> Optional[str]:
        """Retourne le port d'enregistrement par défaut"""
        return self.default_record_port

    def get_armed_track_index(self) -> Optional[int]:
        """Returns the index of the currently armed MIDI track, or None if no track is armed."""
        for i, track in enumerate(self.song.tracks):
            if is_midi_track(track) and getattr(track, 'record_mode', 'OFF') != 'OFF':
                return i
        return None

    def get_input_routing_track(self) -> AutomationTrack:
        """Returns or creates the global MIDI input routing automation track (stored in song.input_routing)."""
        if self.song.input_routing:
            return self.song.input_routing

        # Create it if not found
        track = AutomationTrack(name="Input Routing", target_track_index=-1) # -1 means Global

        # Find first MIDI track index for default routing
        first_midi_idx = 0
        for i, t in enumerate(self.song.tracks):
            if getattr(t, 'is_midi', False):
                first_midi_idx = i
                break

        track.add_point(AutomationPoint(start_time=0.0, value=float(first_midi_idx), parameter='input_routing', curve='none'))

        self.song.input_routing = track
        self.is_dirty = True
        return track

    def invalidate_song_length_cache(self):
        """Invalidates the cached song length."""
        self._cached_song_length_beats = None

    def _get_audio_duration_in_beats(self, track: AudioTrack) -> float:
        """
        Calcule la durée du fichier audio en beats, en utilisant un cache pour éviter le lag.
        """
        if track.duration_beats is not None:
            return track.duration_beats

        if not track.filepath or not os.path.exists(track.filepath):
            return self.song.time_signature_numerator * 4 

        try:
            audio = AudioSegment.from_file(track.filepath) 
            duration_ms = len(audio)
            
            duration_beats = (duration_ms * self.song.tempo) / 60000.0
            
            print("--- DEBUG AUDIO CONVERSION ---")
            print(f"  - Fichier: {os.path.basename(track.filepath)}")
            print(f"  - Tempo du morceau (BPM): {self.song.tempo}")
            print(f"  - Durée Audio (ms): {duration_ms}")
            print(f"  - Durée Audio (sec): {duration_ms / 1000.0}")
            print(f"  - Résultat (Beats): {duration_beats}")
            print("------------------------------")
            
            track.duration_beats = duration_beats
            
            return duration_beats
        except Exception as e:
            print(f"Error loading or processing audio file {track.filepath}: {e}")
            track.duration_beats = 0.0
            return 0.0

    def get_song_length_in_beats(self) -> float:
        """Calcule la durée totale en beats, incluant le réglage manuel de l'UI."""
        # 1. Utiliser le cache si disponible et qu'on n'enregistre pas
        if not self.is_recording and self._cached_song_length_beats is not None:
            return self._cached_song_length_beats

        # 2. Calculer la longueur "naturelle" (basée sur les notes/audio)
        max_beat = 0.0
        for track in self.song.tracks:
            if is_audio_track(track):
                duration = self._get_audio_duration_in_beats(track)
                max_beat = max(max_beat, track.start_time + duration)
            elif is_midi_track(track):
                for event in getattr(track, 'events', []):
                    for note in event.notes:
                        max_beat = max(max_beat, event.start_time + note.duration)
            elif is_automation_track(track):
                if track.points:
                    max_beat = max(max_beat, max(p.start_time for p in track.points))

        # 3. Intégrer la position de fin manuelle saisie dans l'UI
        if self.ui_end_pos_str:
            manual_beats = self.parse_position_to_beats(self.ui_end_pos_str)
            if manual_beats is not None:
                max_beat = max(max_beat, manual_beats)

        # 4. Appliquer un minimum de 4 mesures et arrondir à la mesure supérieure
        beats_per_measure = self.song.time_signature_numerator or 4
        min_length = float(beats_per_measure * 4)
        
        total = max(max_beat, min_length)
        rounded_length = math.ceil((total + 0.0001) / beats_per_measure) * beats_per_measure
        
        if not self.is_recording:
            self._cached_song_length_beats = rounded_length
            
        return float(rounded_length)

    def set_song_length_from_ui(self, position_str: str):
        """Définit manuellement la fin du morceau et rafraîchit l'UI"""
        self.ui_end_pos_str = position_str
        self.invalidate_song_length_cache()
        # On déclenche l'événement pour que Kivy mette à jour les widgets
        self.song_structure_changed += 1

    def _all_notes_off(self):
        if self.jack_manager.is_running:
            self.jack_manager.silence_all_midi_notes()
            return

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
        if self.jack_manager.is_running:
            self.jack_manager.update_audio_tracks_speed()
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

            # Ensure the JACK engine is running and ports are registered
            if not self.jack_manager.is_running:
                self.jack_manager.start()
            else:
                self.jack_manager.ensure_track_ports()
                self.jack_manager.refresh_automation()

            self.song_structure_changed += 1
            return {"status": "success", "message": f"MIDI track '{name}' added."}
        elif track_type == 'audio':
            if not filepath:
                return {"status": "error", "message": "Error: Filepath is required for audio tracks."}
            try:
                segment = AudioSegment.from_file(filepath)
                self.audio_track_duration_ms[filepath] = len(segment)
            except FileNotFoundError:
                return {"status": "error", "message": f"Error: Audio file not found at '{filepath}'"}
            except Exception as e:
                return {"status": "error", "message": f"Error opening audio file: {e}"}
            track = AudioTrack(name=name, filepath=filepath, native_tempo=self.song.tempo)
            self.song.add_track(track)
            self.is_dirty = True
            self.invalidate_song_length_cache()

            # If the sequencer is already running, launch the player for the new track immediately.
            if self.jack_manager.is_running:
                new_track_index = len(self.song.tracks) - 1
                self.jack_manager.launch_player_for_track(track, new_track_index)

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
        if curve not in AutomationPoint.VALID_CURVES:
            return f"Error: Invalid curve type '{curve}'. Must be one of {AutomationPoint.VALID_CURVES}"
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

        if self.jack_manager.is_running:
            self.jack_manager.refresh_automation()

        self.song_structure_changed += 1
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

        # Strip quotes if port_name is a quoted string from the command line
        if port_name.startswith('"') and port_name.endswith('"'):
            port_name = port_name[1:-1]

        old_port_name = track.output_port_name
        track.output_port_name = port_name
        self.is_dirty = True

        if self.jack_manager.is_running:
            self.jack_manager.open_midi_port(port_name)
            if old_port_name and old_port_name != port_name:
                self.jack_manager.close_midi_port(old_port_name)

        return f"Assigned port '{port_name}' to track '{track.name}'."

    def unassign_port(self, track_index: int) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Port un-assignment is currently only supported for MIDI tracks."
        if track.output_port_name:
            port_name = track.output_port_name
            output = f"Un-assigned port from track '{track.name}'."
            track.output_port_name = None
            self.is_dirty = True

            if self.jack_manager.is_running:
                self.jack_manager.close_midi_port(port_name)

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
            if self.jack_manager.is_running:
                midi_volume = int(volume * 127)
                self.jack_manager.send_midi_to_track(track_index, mido.Message("control_change", channel=track.channel, control=7, value=midi_volume))

        return {"status": "success", "message": f"Volume for track '{track.name}' set to {volume:.2f}."}

    def _resync_jack_transport(self):
        """
        Forces a full resynchronization of the JACK transport if it is currently rolling.
        This is used after state changes like mute/solo/pan to prevent desync.
        """
        if not self.jack_manager.is_running:
            return

        debug = getattr(self, "debug_enabled", True)
        was_rolling = self.jack_manager.jack_client and self.jack_manager.jack_client.transport_state == jack.ROLLING

        if was_rolling:
            if debug:
                print("[DEBUG] Forcing JACK transport resync...")
            try:
                jc = self.jack_manager.jack_client
                # Read current position
                state, pos_struct = jc.transport_query_struct()
                pos_dict = jack.position2dict(pos_struct)
                frame = pos_dict.get('frame', 0)
                samplerate = jc.samplerate
                beats_per_second = self.song.tempo / 60.0
                current_beat = (frame / samplerate) * beats_per_second if samplerate > 0 and beats_per_second > 0 else self.jack_manager.last_beat

                # 1. Briefly stop the transport
                jc.transport_stop()
                time.sleep(0.02)

                # 2. Reposition playhead and audio tracks
                self.jack_manager._sync_playhead_to_beat(current_beat)
                self.jack_manager.seek_audio_to_beat(current_beat)
                self.jack_manager.last_beat = current_beat

                # 3. Restart JACK
                jc.transport_start()

                if debug:
                    print(f"[DEBUG] Transport resynced → beat={current_beat:.6f}")
            except Exception as e:
                print(f"[DEBUG] JACK resync failed: {e}", file=sys.stderr)

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

        # === UPDATE PISTE AUDIO (mpv IPC) ===
        if isinstance(track, AudioTrack):
            if self.jack_manager.is_running:
                with self.jack_manager.process_lock:
                    for ap in self.jack_manager.active_audio_processes:
                        if ap.track_index == track_index:
                            # Pan value from -1.0 (L) to 1.0 (R)
                            # Using lavfi pan filter. c0 is left, c1 is right.
                            gain_l = min(1.0, 1.0 - pan)
                            gain_r = min(1.0, 1.0 + pan)
                            pan_filter = f"lavfi=[pan=stereo|c0={gain_l:.2f}*c0|c1={gain_r:.2f}*c1]"
                            command = {"command": ["set_property", "af", pan_filter]}
                            self.jack_manager._send_ipc_command(ap.socket_path, command)
                            break
        # === UPDATE PISTE MIDI (CC #10) ===
        elif isinstance(track, MidiTrack):
            if self.jack_manager.is_running:
                # Conversion de pan (-1.0 à 1.0) en valeur MIDI (0 à 127)
                midi_pan = int((pan + 1.0) / 2.0 * 127)
                self.jack_manager.send_midi_to_track(track_index, mido.Message("control_change", channel=track.channel, control=10, value=midi_pan))
        
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
        self.is_dirty = True
        status = "Muted" if track.is_muted else "Unmuted"

        # --- Update in real-time if JACK is running ---
        if self.jack_manager.is_running:
            is_rolling = self.jack_manager.jack_client.transport_state == jack.ROLLING
            is_any_track_soloed = any(t.is_solo for t in self.song.tracks if hasattr(t, 'is_solo'))
            should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted

            # --- Audio Track: Lightweight IPC command ---
            if isinstance(track, AudioTrack):
                with self.jack_manager.process_lock:
                    ap = next((p for p in self.jack_manager.active_audio_processes if p.track_index == track_index), None)
                    if ap:
                        self.jack_manager._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", not should_be_audible]})
                # Audio tracks do not require a transport resync for mute/solo

            # --- MIDI Track: Silence notes and conditionally resync ---
            elif isinstance(track, MidiTrack):
                if track.is_muted:
                    # Silence all notes for this track
                    for cc in (123, 120, 121):
                        self.jack_manager.send_midi_to_track(track_index, mido.Message('control_change', channel=track.channel, control=cc, value=0))
                    keys_to_remove = [key for key in self.jack_manager._active_notes.keys() if key[0] == track_index]
                    for key in keys_to_remove:
                        del self.jack_manager._active_notes[key]

                # MIDI tracks DO require a resync to prevent timing drift if muted/unmuted during playback
                if is_rolling:
                    self._resync_jack_transport()

            # Automation events depend on mute/solo state, so they must be regenerated
            self.jack_manager._prepare_automation_events()

        return {"status": "success", "message": f"Track '{track.name}' is now {status}."}

    def toggle_solo(self, track_index: int):
        tracks = self.song.tracks
        if not 0 <= track_index < len(tracks):
            return {"status": "error", "message": "Error: Invalid track index."}

        target_track = tracks[track_index]
        is_being_soloed = not target_track.is_solo
        target_track.is_solo = is_being_soloed
        self.is_dirty = True
        output = ""

        if is_being_soloed:
            for i, other_track in enumerate(tracks):
                if i != track_index and hasattr(other_track, 'is_solo') and other_track.is_solo:
                    other_track.is_solo = False
                    output += f"Track '{other_track.name}' is now Un-soloed.\n"
        status = "Solo" if target_track.is_solo else "Un-soloed"

        # --- Real-time update if JACK is running ---
        if self.jack_manager.is_running:
            is_rolling = self.jack_manager.jack_client.transport_state == jack.ROLLING
            is_any_track_soloed = any(t.is_solo for t in tracks if hasattr(t, 'is_solo'))

            # Update all audio track mute states via lightweight IPC
            with self.jack_manager.process_lock:
                for i, track in enumerate(tracks):
                    if isinstance(track, AudioTrack):
                        should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                        ap = next((p for p in self.jack_manager.active_audio_processes if p.track_index == i), None)
                        if ap:
                            self.jack_manager._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", not should_be_audible]})

            # For MIDI tracks, silence those that are no longer audible
            for i, track in enumerate(tracks):
                if isinstance(track, MidiTrack):
                    should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                    if not should_be_audible:
                        self.jack_manager.send_midi_to_track(i, mido.Message('control_change', channel=track.channel, control=123, value=0))
                        keys_to_remove = [key for key in self.jack_manager._active_notes.keys() if key[0] == i]
                        for key in keys_to_remove:
                            del self.jack_manager._active_notes[key]

            # Regenerate automation events as their audibility may have changed
            self.jack_manager._prepare_automation_events()

            # A solo change is a major state change that requires resynchronization to avoid drift
            if is_rolling:
                self._resync_jack_transport()

        output += f"Track '{target_track.name}' is now {status}."
        return {"status": "success", "message": output}

    def prime_all_tracks(self, primed_by_automation: set = None) -> str:
        """
        Sends the current state (program, volume, pan, etc.) for all assigned MIDI tracks.
        Skips parameters that have already been set by an automation event.
        """
        if not self.jack_manager.is_running:
            return "Warning: prime_all_tracks called but JACK manager is not running. State will not be sent."

        if primed_by_automation is None:
            primed_by_automation = set()

        output = "Priming all MIDI tracks with initial state...\n"
        tracks = self.song.tracks
        is_any_track_soloed = any(t.is_solo for t in tracks if hasattr(t, 'is_solo'))

        for i, track in enumerate(tracks):
            if self.is_recording and self.last_record_settings and i == self.last_record_settings.get('track_index'):
                continue

            if isinstance(track, MidiTrack):
                should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted

                if should_be_audible:
                    try:
                        output += f"  - Priming MIDI track '{track.name}' on Ch: {track.channel + 1}\n"
                        # Bank and Program changes are always sent, unless automation for them exists at the start.
                        if track.bank_msb is not None and (i, 'cc0') not in primed_by_automation:
                            self.jack_manager.send_midi_to_track(i, mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb))
                        if track.bank_lsb is not None and (i, 'cc32') not in primed_by_automation:
                            self.jack_manager.send_midi_to_track(i, mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb))
                        if (i, 'prog') not in primed_by_automation:
                            self.jack_manager.send_midi_to_track(i, mido.Message('program_change', channel=track.channel, program=track.instrument))

                        # Only send volume if not already handled by automation.
                        if (i, 'vol') not in primed_by_automation:
                            midi_volume = int(track.volume * 127)
                            self.jack_manager.send_midi_to_track(i, mido.Message('control_change', channel=track.channel, control=7, value=midi_volume))

                        # Only send pan if not already handled by automation.
                        if (i, 'pan') not in primed_by_automation:
                            midi_pan = int((track.pan + 1.0) / 2.0 * 127)
                            self.jack_manager.send_midi_to_track(i, mido.Message('control_change', channel=track.channel, control=10, value=midi_pan))

                    except Exception as e:
                        output += f"  - Could not send state to track '{track.name}': {e}\n"
                else:
                    # Silence the track if it's not supposed to be audible
                    self.jack_manager.send_midi_to_track(i, mido.Message('control_change', channel=track.channel, control=7, value=0)) # Volume to 0
                    self.jack_manager.send_midi_to_track(i, mido.Message('control_change', channel=track.channel, control=123, value=0)) # All notes off
        return output

    def set_control_port(self, port_name: str) -> str:
        """Sets the MIDI input port for control messages and starts listening."""
        if self.midi_listener_thread and self.midi_listener_thread.is_alive():
            return "A control port is already active. Please unset it first."
        self.control_port_name = port_name

        if not self.jack_manager.is_running:
            self._midi_listener_stop_event.clear()
            self.midi_listener_thread = threading.Thread(target=self._midi_listener_loop, args=(port_name,))
            self.midi_listener_thread.daemon = True
            self.midi_listener_thread.start()
            return f"Listening for control messages on '{port_name}'."
        else:
            return f"Note: JACK is active. Control messages should be sent to the 'Clavier' port."

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

    def import_midi_file(self, filepath: str) -> str:
        """Imports a MIDI file into the current project."""
        result = import_midi_to_project(self, filepath)
        return result.get("message", "Import process finished.")

    def export_midi_tracks(self, track_indices: List[int], filepath: str) -> str:
        """Exports selected MIDI tracks to a MIDI file."""
        result = export_midi_from_project(self, track_indices, filepath)
        return result.get("message", "Export process finished.")

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

            # --- Backward Compatibility: Move Input Routing track from tracks list to input_routing field ---
            routing_track = None
            for i, track in enumerate(self.song.tracks):
                if getattr(track, 'target_track_index', None) == -1 or getattr(track, 'name', '') == "Input Routing":
                    routing_track = self.song.tracks.pop(i)
                    break

            if routing_track and not self.song.input_routing:
                self.song.input_routing = routing_track


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

            # --- Stop existing Carla instance and start a new one ---
            # This will load the project's carla file if it exists,
            # or an empty instance if it does not.
            self._start_carla_process(self.song.carla_project_path)
            print("Waiting for Carla to initialize...")
            time.sleep(3)  # Wait for Carla and its plugins to be ready

            # --- Restart Jack Manager to apply new project settings ---
            self.jack_manager.stop()
            self.jack_manager.start()
            time.sleep(0.5) # Give Jack time to register ports

            # --- Restore JACK connections with aj-snapshot ---
            if self.song.aj_snapshot_path and os.path.exists(self.song.aj_snapshot_path):
                try:
                    print(f"Restoring JACK connections from {self.song.aj_snapshot_path}...")
                    
                    # Option 1: Utiliser -p (poll) pour attendre que les ports apparaissent
                    # aj-snapshot attendra que les clients mentionnés dans le fichier soient présents
                    subprocess.run(["aj-snapshot", "-rj", "-p", "2", self.song.aj_snapshot_path], check=True)
                    
                except subprocess.CalledProcessError as e:
                    # Si cela échoue encore, on fait une deuxième tentative après un délai plus long
                    print("First attempt failed, retrying in 5 seconds...")
                    time.sleep(5)
                    try:
                        subprocess.run(["aj-snapshot", "-rj", self.song.aj_snapshot_path], check=True)
                    except Exception as e2:
                        print(f"Final error restoring aj-snapshot: {e2}", file=sys.stderr)
            else:
                # --- Auto-connect MIDI tracks based on project data (fallback) ---
                for track in self.song.tracks:
                    if isinstance(track, MidiTrack) and track.output_port_name and track.input_port_name:
                        self.jack_manager.auto_connect_dynamic(track.output_port_name, track.input_port_name)

            return f"Successfully loaded project from '{project_filepath}'"
        except FileNotFoundError:
            return f"Error: Project file not found at '{project_filepath}'"
        except Exception as e:
            return f"Error loading project file: {e}"

    def save_jack_connections(self, filepath: str) -> dict:
        """Saves the current JACK connections using aj-snapshot."""
        if not filepath:
            return {"status": "error", "message": "Filepath cannot be empty."}

        try:
            # --- FIX: Ensure the target directory exists before saving ---
            directory = os.path.dirname(filepath)
            if directory:
                os.makedirs(directory, exist_ok=True)

            command = ["aj-snapshot", filepath]
            print(f"Executing: {' '.join(command)}")
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode == 0:
                output = result.stdout.strip() or result.stderr.strip()
                print(f"aj-snapshot output: {output}")
                return {
                    "status": "success",
                    "message": f"JACK connections saved to {os.path.basename(filepath)}."
                }
            else:
                error_message = result.stderr.strip()
                print(f"aj-snapshot command failed with exit code {result.returncode}: {error_message}", file=sys.stderr)
                return {
                    "status": "error",
                    "message": f"aj-snapshot failed: {error_message}"
                }

        except FileNotFoundError:
            print("Error: 'aj-snapshot' command not found.", file=sys.stderr)
            return {
                "status": "error",
                "message": "Error: 'aj-snapshot' command not found. Please ensure it is installed and in your system's PATH."
            }
        except Exception as e:
            print(f"An unexpected error occurred while running aj-snapshot: {e}", file=sys.stderr)
            return {
                "status": "error",
                "message": f"An unexpected error occurred: {e}"
            }

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

        # --- Restart Jack Manager and Carla for the new empty project ---
        self.jack_manager.stop()
        self.jack_manager.start()
        self._start_carla_process() # Start an empty instance

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
        if self.jack_manager.is_running:
            try:
                # Register a native JACK port instead
                port = self.jack_manager.jack_client.midi_outports.register(name)
                # Store it in open_ports for reference
                self.jack_manager.open_ports[name] = port
                self.is_dirty = True
                return f"Created native JACK MIDI port: '{name}'"
            except Exception as e:
                return f"Error creating JACK port: {e}"

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
        self._stop_carla_process()
        """Assure la fermeture propre des processus audio mpv à la sortie du séquenceur."""
        try:
            self.jack_manager._shutdown_audio_processes()
            print("✅ All mpv processes terminated.")
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")
   
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
                # Détermine la zone à effacer en fonction de la durée de l'enregistrement
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
                        if event.notes or event.cc_messages:
                            final_events.append(event)

                    # Cas 2: L'événement commence DANS la zone à effacer.
                    # On supprime ses notes mais on garde les messages CC éventuels.
                    elif start_beat <= event_start_time < end_beat_for_deletion:
                        event.notes.clear()
                        if event.cc_messages:
                            final_events.append(event)
                    
                    # Cas 3: L'événement commence APRÈS la zone d'effacement.
                    else:
                        final_events.append(event)

                # Nettoyage final : on enlève les événements devenus complètement vides.
                target_track.events = [e for e in final_events if e.notes or e.cc_messages]
                self.invalidate_song_length_cache()
                self.song_structure_changed += 1

                start_pos_msg = self._format_beats_to_position(start_beat)
                if end_beat_for_deletion == float('inf'):
                    print(f"Notes existantes effacées/tronquées à partir de {start_pos_msg}.")
                else:
                    end_pos_msg = self._format_beats_to_position(end_beat_for_deletion)
                    print(f"Notes existantes effacées/tronquées dans la plage {start_pos_msg} à {end_pos_msg}.")

            self.is_recording = True
            self._recording_merge_event = Clock.schedule_interval(self._merge_recorded_events, 0.1)
            print(f"Enregistrement démarré via JACK (track hopping activé).")

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

            if not self.jack_manager.is_running or not self.jack_manager.jack_client:
                print("Starting JACK manager for recording...")
                self.jack_manager.start()
                time.sleep(0.2)

            
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
                    # If no start_beat is given, use the UI's start position for consistency
                    start_pos = self.ui_start_pos_str or "1:1"
                    start_beat = self.parse_position_to_beats(start_pos)
                    if start_beat is None:
                        start_beat = 0.0 # Fallback in case of invalid format

                # --- NEW LOGIC: Determine recording duration from UI end position ---
                end_pos = self.ui_end_pos_str
                end_beat = self.parse_position_to_beats(end_pos) if end_pos else None

                # If an end beat is defined and valid, calculate the number of beats to record.
                # Otherwise, num_beats_to_record remains as passed (likely None for infinite).
                if end_beat is not None and end_beat > start_beat:
                    num_beats_to_record = end_beat - start_beat

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
        
        if self.jack_manager.is_running:
            self.jack_manager.refresh_automation()

        self.song_structure_changed += 1

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
        tracks = self.song.tracks
        is_any_track_soloed = any(t.is_solo for t in tracks)

        for track in tracks:
            should_play = (getattr(track, 'is_solo', False) or not is_any_track_soloed) and not getattr(track, 'is_muted', False)
            if not should_play:
                continue

            if is_midi_track(track):
                for event in track.events:
                    for note in event.notes:
                        event_end_beat = event.start_time + note.duration
                        if event_end_beat > max_beats:
                            max_beats = event_end_beat
            elif is_audio_track(track):
                try:
                    duration_ms = self.audio_track_duration_ms.get(track.filepath)
                    if duration_ms is None:
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
        if not auto_track.points:
            return []

        target_track_index = auto_track.target_track_index
        if not 0 <= target_track_index < len(self.song.tracks):
            return []
        target_track = self.song.tracks[target_track_index]
        param_map = {"vol": {"type": "midi_cc", "control": 7}, "pan": {"type": "midi_cc", "control": 10}, "vel": {"type": "velocity_multiplier"}, "prog": {"type": "program_change"}, **{f"cc{i}": {"type": "midi_cc", "control": i} for i in range(128)}}

        points_by_parameter: Dict[str, List[AutomationPoint]] = {}
        for p in auto_track.points:
            points_by_parameter.setdefault(p.parameter, []).append(p)

        for parameter, param_points in points_by_parameter.items():
            param_points.sort(key=lambda p: p.start_time)
            param_config = param_map.get(parameter.lower())
            if not param_config:
                continue

            # First, add all the raw points to the event list. This ensures they are always present.
            for p in param_points:
                generated_events.append({
                    "time": p.start_time,
                    "target_track_index": target_track_index,
                    "parameter": p.parameter,
                    "param_config": param_config,
                    "value": p.value
                })

            # Now, iterate through the segments between points to generate the curves.
            for i in range(len(param_points) - 1):
                start_point = param_points[i]
                end_point = param_points[i+1]

                if start_point.curve == "none":
                    continue

                start_time = start_point.start_time
                end_time = end_point.start_time
                start_val = start_point.value
                end_val = end_point.value
                time_diff = end_time - start_time

                if time_diff <= 0:
                    continue

                granularity = 1.0 / 16.0  # Generate events for every 16th note
                num_steps = int(time_diff / granularity)
                if num_steps <= 1:
                    continue

                # Generate time steps and value steps based on the curve type
                t = np.linspace(0, 1, num_steps, endpoint=False)[1:]  # Exclude t=0
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
                    continue  # Unsupported curve type

                # Add the generated intermediate events
                if value_steps is not None:
                    for step_time, step_value in zip(time_steps, value_steps):
                        generated_events.append({
                            "time": step_time,
                            "target_track_index": target_track_index,
                            "parameter": start_point.parameter,
                            "param_config": param_config,
                            "value": step_value
                        })

        generated_events.sort(key=lambda e: e['time'])
        return generated_events

    def _resync_all_at_beat(self, beat: float, force_play: bool = False):
        """
        Resynchronizes all tracks to a specific beat.
        If JACK is running, it repositions the master transport. Otherwise, it just
        updates the internal sequencer state.
        If `force_play` is True, it will start the transport even if it wasn't rolling before.
        """
        print(f"\n[DIAGNOSTIC] === _resync_all_at_beat START (target_beat={beat:.6f}) ===")

        # --- Step 1: Update internal state (always) ---
        # This is the crucial part for the headless test to work.
        print("[DIAGNOSTIC] Syncing internal playhead...")
        self.jack_manager._sync_playhead_to_beat(beat)
        if self.gui_mode:
            self.current_beat = beat # Update the Kivy property for the UI

        # --- Step 2: Handle JACK and external processes (if running) ---
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            print("[DIAGNOSTIC] JACK not running. Skipping transport and audio sync.")
            print(f"[DIAGNOSTIC] === _resync_all_at_beat END (No JACK) ===\n")
            return

        try:
            # 1. Mémoriser si le transport était en cours de lecture
            was_rolling = self.jack_manager.jack_client.transport_state == jack.ROLLING
            print(f"[DIAGNOSTIC] was_rolling: {was_rolling}")

            # 2. Arrêter le transport pour garantir un état de base propre
            if was_rolling:
                self.jack_manager.jack_client.transport_stop()

            # --- NOUVELLE LOGIQUE ---
            # Forcer l'état de pause sur tous les lecteurs audio, peu importe leur état précédent.
            # C'est la clé pour garantir qu'ils sont prêts à recevoir une commande de recherche (seek).
            print("[DIAGNOSTIC] Forcing pause on all audio players to ensure a known state.")
            self.jack_manager.set_all_audio_pause_state(True)
            time.sleep(0.05)  # Un court délai crucial pour laisser aux processus mpv le temps de traiter la commande de pause.
            # -------------------------

            # 3. Repositionner le transport JACK à la position exacte du beat
            beats_per_second = self.song.tempo / 60.0
            samplerate = self.jack_manager.jack_client.samplerate
            if beats_per_second > 0 and samplerate > 0:
                target_frame = int((beat / beats_per_second) * samplerate)
                _ , pos = self.jack_manager.jack_client.transport_query_struct()
                original_frame = pos.frame
                pos.frame = target_frame
                self.jack_manager.jack_client.transport_reposition_struct(pos)
                print(f"[DIAGNOSTIC] Repositioning JACK transport from frame {original_frame} to {target_frame} (beat {beat:.6f})")

            # 4. Synchroniser les lecteurs externes avec la nouvelle position (l'état interne est déjà à jour)
            print("[DIAGNOSTIC] Seeking audio tracks (synchronously)...")
            self.jack_manager.seek_audio_to_beat(beat, synchronous=True)
            print("[DIAGNOSTIC] Audio track seek complete.")

            # 5. Régénérer les événements d'automation pour refléter le nouvel état (solo/mute)
            self.jack_manager._prepare_automation_events()

            # 6. Envoyer l'état actuel (volume, pan, etc.) à toutes les pistes audibles
            primed_by_automation = self.jack_manager._prime_automation_at_beat(beat)
            self.prime_all_tracks(primed_by_automation=primed_by_automation)

            # 7. CORRECTION : Mettre à jour l'état mute/solo des pistes audio
            is_any_track_soloed = any(t.is_solo for t in self.song.tracks if hasattr(t, 'is_solo'))
            with self.jack_manager.process_lock:
                for i, track in enumerate(self.song.tracks):
                    if isinstance(track, AudioTrack):
                        should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                        ap = next((p for p in self.jack_manager.active_audio_processes if p.track_index == i), None)
                        if ap:
                            self.jack_manager._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", not should_be_audible]})

            # 8. Redémarrer le transport s'il était en cours de lecture ou si forcé
            if was_rolling or force_play:
                print("[DIAGNOSTIC] Restarting transport...")
                self.jack_manager.jack_client.transport_start()
                self.jack_manager.set_all_audio_pause_state(False)
                print("[DIAGNOSTIC] Transport restarted.")

        except jack.JackError as e:
            print(f"Error during resynchronization: {e}", file=sys.stderr)
        finally:
            print(f"[DIAGNOSTIC] === _resync_all_at_beat END (With JACK) ===\n")

    def play(self, start_beat: Optional[float] = None):
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            self.jack_manager.start()
            time.sleep(0.2) # Give JACK time to start and connect

        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            print("Error: Could not start JACK client.")
            return

        # Store the beat from which playback is starting
        effective_start_beat = start_beat if start_beat is not None else self.rewind_beat
        self.last_play_start_beat = effective_start_beat


        # Ensure engine cache is up to date before starting
        self.jack_manager.refresh_automation()

        # If a start beat is provided, reposition the transport
        if start_beat is not None:
            beats_per_second = self.song.tempo / 60.0
            samplerate = self.jack_manager.jack_client.samplerate
            if beats_per_second > 0 and samplerate > 0:
                target_frame = int((start_beat / beats_per_second) * samplerate)
                _ , pos = self.jack_manager.jack_client.transport_query_struct()
                pos.frame = target_frame
                self.jack_manager.jack_client.transport_reposition_struct(pos)
                self.jack_manager._repositioning_pending = True

        # Prime automation first and get the set of parameters it handled.
        primed_by_automation = self.jack_manager._prime_automation_at_beat(effective_start_beat)

        # Then, prime the tracks, passing in the set so it can skip what's already done.
        self.prime_all_tracks(primed_by_automation=primed_by_automation)

        # Simply tell JACK to start rolling
        if self.jack_manager.jack_client.transport_state != jack.ROLLING:
            self.jack_manager.jack_client.transport_start()
            self.playback_state = "playing"


    def pause(self):
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            return

        try:
            if self.jack_manager.jack_client.transport_state == jack.ROLLING:
                # Get current beat BEFORE stopping
                current_beat = self._get_current_beat()
                print(f"\n[DIAGNOSTIC] --- PAUSING at beat {current_beat:.6f} ---")
                self.jack_manager.jack_client.transport_stop()
                self.playback_state = "paused"
                # Store the precise beat for resume
                self.pause_beat = current_beat
            elif self.playback_state == "paused":
                print(f"\n[DIAGNOSTIC] --- RESUMING from beat {self.pause_beat:.6f} ---")
                # Resync all tracks to the last beat and resume
                self._resync_all_at_beat(self.pause_beat, force_play=True)
                self.playback_state = "playing"

        except jack.JackError as e:
            print(f"Error controlling JACK transport: {e}")

    def _stop_playback_transport(self):
        """Stops the JACK transport and resets the playhead. Safe to call from any thread."""
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            self.playback_state = "stopped"
            return

        if self.jack_manager.jack_client.transport_state == jack.ROLLING:
            self.jack_manager.silence_all_midi_notes()
            time.sleep(0.01)

        try:
            if self.jack_manager.jack_client.transport_state == jack.ROLLING:
                self.jack_manager.jack_client.transport_stop()
                print("JACK transport stopped.")

            beats_per_second = self.song.tempo / 60.0
            samplerate = self.jack_manager.jack_client.samplerate
            if beats_per_second > 0 and samplerate > 0:
                target_frame = int((self.rewind_beat / beats_per_second) * samplerate)
                _, pos = self.jack_manager.jack_client.transport_query_struct()
                pos.frame = target_frame
                self.jack_manager.jack_client.transport_reposition_struct(pos)
                self.jack_manager._sync_playhead_to_beat(self.rewind_beat)
                self.jack_manager.seek_audio_to_beat(self.rewind_beat)
                self.jack_manager.set_all_audio_pause_state(True)
        except jack.JackError as e:
            print(f"Error controlling JACK transport: {e}")

        self.playback_state = "stopped"
        self.jack_manager.silence_all_midi_notes()

        if self.gui_mode:
            self.current_beat = self.rewind_beat

        print("Sequencer stopped.")

    def stop(self):
        """Stops recording and/or playback."""
        if self.is_recording:
            print("Stopping recording...")
            if self._recording_merge_event:
                self._recording_merge_event.cancel()
                self._recording_merge_event = None

            # Final merge of recorded events
            self._merge_recorded_events(0)

            self.is_recording = False

            # After recording, invalidate the cache so the new length is calculated
            self.invalidate_song_length_cache()
            # Trigger a UI refresh to redraw all tracks to the new length
            self.song_structure_changed += 1

            if self.playback_state != "stopped":
                 self._stop_playback_transport()
        else:
            self._stop_playback_transport()

        # LA LIGNE SUIVANTE EST LA CAUSE DU PROBLÈME ET A ÉTÉ VOLONTAIREMENT SUPPRIMÉE :
        # self.jack_manager.stop()

    def get_measure_beats(self):
        """Calcule la position de chaque barre de mesure en beats."""
        if not self.song:
            return []

        beats_per_measure = self.song.time_signature_numerator

        total_beats = self.get_song_length_in_beats()

        measure_beats = []
        current_beat = 0
        while current_beat < total_beats:
            measure_beats.append(current_beat)
            current_beat += beats_per_measure

        return measure_beats

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

            if self.gui_mode:
                self.current_beat = new_beat

            return f"Seeked to position {self._format_beats_to_position(new_beat)}."

        except (ValueError, IndexError):
            return "Error: Invalid seek format. Use +/-<number><m|b> (e.g., '+1m', '-4b')."

    def send_cc_message(self, port_name: str, channel: int, control: int, value: int) -> str:
        """Sends a single CC message to a specified port."""
        if not 0 <= channel <= 15:
            return "Error: Channel must be between 1 and 16."
        if not 0 <= control <= 127:
            return "Error: CC number must be between 0 and 127."
        if not 0 <= value <= 127:
            return "Error: CC value must be between 0 and 127."

        msg = mido.Message('control_change', channel=channel, control=control, value=value)

        # If JACK is running, we use the JackManager to queue the message
        if self.jack_manager.is_running:
            # Try to find which track or virtual port this name refers to
            target = None
            if port_name == self.song.metronome_port_name:
                target = -1
            else:
                for i, track in enumerate(self.song.tracks):
                    if is_midi_track(track) and track.output_port_name == port_name:
                        target = track
                        break

            if target is not None:
                self.jack_manager._queue_midi_message(target, msg)
                return f"Queued CC message to {port_name} (JACK): Ch={channel+1}, CC={control}, Val={value}"
            else:
                # If no matching track found, it might be an external port name.
                # In native JACK mode, we don't support sending to random external ports
                # easily without registering a temporary port.
                return f"Error: Port '{port_name}' not found or not registered in JACK mode."

        # Fallback to mido (ALSA) mode
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
                port.send(msg)
                time.sleep(0.01)
                return f"Sent CC message to {port_name}: Ch={channel+1}, CC={control}, Val={value}"
            except Exception as e:
                return f"Error sending CC message: {e}"
            finally:
                if is_temp_port and port:
                    port.close()
        return f"Error: Could not find or open port '{port_name}'"
