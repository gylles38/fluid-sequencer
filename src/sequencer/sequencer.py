from .midi_export import export_to_midi
from .midi_import import import_song
from .gp_import import import_gp
from .midi_import_project import import_midi_to_project
from .midi_export_project import export_midi_from_project
from .models import (AnyTrack, AudioTrack, AutomationTrack, AutomationPoint,
                    CCMessage, Event, MidiTrack, Note, Song, MidiMapping,
                    is_midi_track, is_audio_track)
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
    midi_learn_mode = BooleanProperty(False)
    last_learned_cc = NumericProperty(-1)
    ui_end_pos_str = StringProperty("")
    is_smoothing = BooleanProperty(False)
    song_structure_changed = NumericProperty(0)
    tempo = NumericProperty(120)
    DEFAULT_AUDIO_PLAYER_COMMAND = "mpv --really-quiet --no-video --idle --af=rubberband --audio-device=jack"

    def __init__(self, tempo: int = 120, gui_mode=False):
        super().__init__()
        self.gui_mode = gui_mode
        self.tempo = tempo
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
        self._last_recorded_auto_points = {} # (track_idx, param) -> [last_p, prev_p]
        
        # ⚠️ NOUVEAU : Cache pour éviter de recharger les fichiers audio
        self._audio_duration_cache: Dict[str, float] = {} 
        
        # Cache pour la longueur totale du morceau (dépend de l'audio)
        self._cached_song_length_beats: Optional[float] = None

        self.track_overrides: Dict[int, MidiTrack] = {}
        self.last_play_start_beat: Optional[float] = None
        self._last_transport_command_time = 0.0

        self.bind(song_structure_changed=self._update_current_routing)
        
        if self.gui_mode:
            Clock.schedule_interval(self._poll_engine_state, 1/60.0)
            Clock.schedule_interval(self._merge_recorded_events, 1/60.0)

    def _poll_engine_state(self, dt):
        if not self.jack_manager or not self.jack_manager.is_running:
            return

        # Skip sync if we recently sent a manual command (cooldown to allow engine to catch up)
        if time.perf_counter() - self._last_transport_command_time < 0.5:
            return

        # 1. Obtenir l'état directement depuis JACK (Source de vérité absolue)
        state_code, pos_dict = self.jack_manager.get_safe_transport_pos()
        if state_code is None:
            return

        # state_code ici est directement jack.ROLLING ou jack.STOPPED
        # C'est beaucoup plus fiable que _last_transport_state_rt
        engine_is_rolling = (state_code == jack.ROLLING)

        # 2. Update current beat (Votre code actuel qui fonctionne)
        current_frame = pos_dict.get('frame', 0)
        samplerate = self.jack_manager.jack_client.samplerate
        beats_per_second = self.song.tempo / 60.0        
        
        if samplerate > 0 and beats_per_second > 0:
            new_beat = (current_frame / samplerate) * beats_per_second
            if new_beat < 0: new_beat = 0.0
            
            if not math.isclose(self.current_beat, new_beat, abs_tol=0.001):
                self.current_beat = new_beat
                self.last_beat_update_time = time.perf_counter()

            # Update routing even if beat didn't change (to catch arming changes or manual seek while stopped)
            self._update_current_routing()

        # 3. Synchronisation de l'état Playback
        time_since_play = time.perf_counter() - getattr(self, '_last_play_click_time', 0)

        if not engine_is_rolling: # Si JACK est à l'arrêt
            if self.playback_state in ["playing", "recording"] and time_since_play > 0.5:
                print(f"[UI] Engine STOP detected par transport_query.")
                self.playback_state = "stopped"
                self.jack_manager.silence_all_midi_notes()
            elif self.playback_state == "paused" and current_frame == 0:
                # Si on est en pause mais que le moteur est revenu à 0 alors qu'on n'était pas au début,
                # c'est probablement un STOP externe.
                # On utilise une petite marge pour éviter les faux positifs au tout début du morceau.
                if getattr(self, 'pause_beat', 0) > 0.1:
                    print(f"[UI] Engine RESET to 0 detected while paused. Switching to stopped.")
                    self.playback_state = "stopped"
        else: # Si JACK tourne
            if self.playback_state in ["stopped", "paused"]:
                print(f"[UI] Engine ROLL detected par transport_query.")
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
        """
        Updates the current_routing_index property based on the current beat.

        This method queries the jack_manager to retrieve the input routing value
        for the current beat position. If a valid routing index is found, it updates
        the current_routing_index property. If no routing value exists for the current
        beat, the routing index is set to -1 to indicate no routing.

        Args:
            *args: Variable length argument list (typically used with property observers).

        Raises:
            None

        Side Effects:
            - Updates self.current_routing_index if the value changes
        """
        if self.jack_manager:
            # Ajoute une petite compensation (ex: 0.05 beat) pour compenser le lag de l'UI
            look_ahead_beat = self.current_beat + 0.05           
            val = self.jack_manager._get_input_routing_value(look_ahead_beat)
            new_index = int(round(val)) if val is not None else -1
            if new_index != self.current_routing_index:
                self.current_routing_index = new_index

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
        if command == "play":
            # If armed for recording, pressing play should start the recording.
            if self.is_recording and self.playback_state == 'stopped':
                # The recording thread is already waiting for the transport to start.
                # We find the start_beat from the last recording settings.
                start_beat = 0.0
                if self.last_record_settings and 'start_beat' in self.last_record_settings:
                    start_beat = self.last_record_settings['start_beat']
                self.play(start_beat=start_beat)
                return

            # If paused, resume.
            if self.playback_state == "paused":
                self.pause() # The pause method handles both pause and resume
                return

            # If already playing, do nothing.
            if self.playback_state == "playing":
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

        elif command == "pause":
            if self.playback_state in ("playing", "paused"):
                self.pause()

        elif command == "play_pause":
            if self.playback_state == "playing":
                self.pause()
            else:
                self.process_transport_command("play")

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

    def get_start_beat(self):
        """Calcule le beat de départ basé sur le texte de l'interface"""
        start_pos_str = getattr(self, 'ui_start_pos_str', "1:1") or "1:1"
        start_beat = self.parse_position_to_beats(start_pos_str)
        return start_beat if start_beat is not None else 0.0

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

                            # --- Handle MIDI Learn Mode ---
                            if self.midi_learn_mode:
                                def _learned(dt, c=control):
                                    self.last_learned_cc = -1
                                    self.last_learned_cc = c
                                Clock.schedule_once(_learned)
                                continue

                            # --- Handle Transport Controls ---
                            if value == 127:
                                if control == self.midi_config.get_transport_cc("play_pause"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("play_pause"))
                                elif control == self.midi_config.get_transport_cc("stop"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("stop"))
                                elif control == self.midi_config.get_transport_cc("record_arm"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("record"))
                                elif control == self.midi_config.get_transport_cc("rewind"):
                                    Clock.schedule_once(lambda dt: self.seek("-1m"))
                                elif control == self.midi_config.get_transport_cc("forward"):
                                    Clock.schedule_once(lambda dt: self.seek("+1m"))
                                elif control == self.midi_config.get_transport_cc("loop"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("loop"))
                                elif control == self.midi_config.get_transport_cc("panic"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("panic"))

                            # --- Handle Custom Mappings ---
                            handled = False
                            for category, mapping in self.midi_config.mappings.items():
                                if category == "transport":
                                    continue # Handled above

                                if isinstance(mapping, list):
                                    for i, cc in enumerate(mapping):
                                        if cc == control:
                                            if category == "volume_sliders":
                                                volume_value = value / 127.0
                                                Clock.schedule_once(lambda dt, ti=i, vol=volume_value: self.set_track_volume(ti, vol, api_mode=True))
                                                handled = True
                                            elif category == "track_solo_buttons":
                                                if value == 127:
                                                    Clock.schedule_once(lambda dt, ti=i: self.toggle_solo(ti))
                                                handled = True
                                            if handled: break
                                elif isinstance(mapping, dict):
                                    for param, cc in mapping.items():
                                        if cc == control:
                                            if category == "selected_track":
                                                target_idx = self.current_routing_index
                                                if target_idx != -1:
                                                    if param in ('volume', 'vol'):
                                                        Clock.schedule_once(lambda dt, ti=target_idx, v=value/127.0: self.set_track_volume(ti, v, api_mode=True))
                                                    elif param == 'solo' and value == 127:
                                                        Clock.schedule_once(lambda dt, ti=target_idx: self.toggle_solo(ti))
                                                    elif param == 'mute' and value == 127:
                                                        Clock.schedule_once(lambda dt, ti=target_idx: self.toggle_mute(ti))
                                                    elif param == 'pan':
                                                        Clock.schedule_once(lambda dt, ti=target_idx, v=(value/127.0)*2-1: self.set_track_pan(ti, str(v), api_mode=True))
                                                    elif param == 'record_arm' and value == 127:
                                                        Clock.schedule_once(lambda dt, ti=target_idx: self.set_record_mode(ti, 'OVERWRITE' if self.song.tracks[ti].record_mode == 'OFF' else 'OFF'))
                                                    elif param == 'vel':
                                                        Clock.schedule_once(lambda dt, ti=target_idx, v=value: self.set_track_velocity(ti, str(v/100.0), api_mode=True))
                                                    elif param == 'prog':
                                                        Clock.schedule_once(lambda dt, ti=target_idx, v=value: self.set_program(ti, v))
                                                    elif param.startswith('cc'):
                                                        try:
                                                            cc_num = int(param[2:])
                                                            def send_generic_cc(dt, ti=target_idx, cn=cc_num, val=value):
                                                                if 0 <= ti < len(self.song.tracks):
                                                                    track = self.song.tracks[ti]
                                                                    if is_midi_track(track) and track.output_port_name:
                                                                        self.send_cc_message(track.output_port_name, track.channel, cn, val)
                                                            Clock.schedule_once(send_generic_cc)
                                                        except ValueError: pass
                                            handled = True
                                            if handled: break
                                if handled: break

                    time.sleep(0.01)
        except Exception as e:
            print(f"\nError in transport control listener for port '{port_name}': {e}")

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

            # Start the new listener thread
            self._transport_control_stop_event.clear()
            self._transport_control_thread = threading.Thread(
                target=self._transport_control_listener_loop,
                args=(port_name,),
                daemon=True
            )
            self._transport_control_thread.start()

            return f"Default record and transport control port set to: {port_name}"
        except Exception as e:
            return f"Error setting record port: {e}"

    def start_midi_recording(self):
        """
        Starts a recording from a MIDI command or UI Record button.
        Supports dynamic routing where recording follows the MIDI input routing track.
        """
        if not self.default_record_port:
            print("Error: No MIDI input port selected for recording.")
            return

        # Use the UI's start position for consistency with play commands
        start_pos = self.ui_start_pos_str or "1:1"
        start_beat = self.parse_position_to_beats(start_pos)
        if start_beat is None:
            start_beat = 0.0 # Fallback

        # In dynamic routing mode, we don't necessarily need a single armed track at the start.
        # However, we can check if at least one track is armed or if routing is active.
        any_armed = any(isinstance(t, MidiTrack) and t.record_mode != 'OFF' for t in self.song.tracks)
        has_routing = self.song.input_routing and self.song.input_routing.points

        if not any_armed and not has_routing:
            print("Error: No track is armed and no MIDI routing is defined.")
            return

        self.record_track(
            track_idx=None, # None means follow dynamic routing
            start_beat=start_beat,
            inport_name=self.default_record_port
        )

    def _merge_recorded_events(self, dt):
        """Polls recorded events from JackManager and merges them into tracks."""
        any_added = False
        tracks_to_sort = set()
        while True:
            try:
                event_data = self.jack_manager._recorded_events_to_merge.popleft()
                track_idx = event_data['track_idx']
                if not 0 <= track_idx < len(self.song.tracks):
                    continue

                track = self.song.tracks[track_idx]

                if event_data['type'] == 'note':
                    if not is_midi_track(track):
                        continue
                    note = Note(pitch=event_data['pitch'], velocity=event_data['velocity'], duration=event_data['duration'])
                    event = Event(start_time=event_data['start_time'], notes=[note])
                    track.add_event(event)

                    # Also record to Velocity automation
                    for i, t in enumerate(self.song.tracks):
                        if isinstance(t, AutomationTrack) and t.target_track_index == track_idx:
                            # Stored as absolute MIDI velocity 0-127
                            self._add_smoothed_automation_point(t, 'vel', event_data['start_time'], float(event_data['velocity']), sort=False)
                            tracks_to_sort.add(i)
                elif event_data['type'] == 'cc':
                    # Support for direct CC automation recording
                    cc_num = event_data['control']
                    cc_val = event_data['value']

                    # Mapping of standard controllers to automation parameters
                    target_param = None
                    norm_val = float(cc_val)

                    # --- Hardware mapping check (Volume Sliders) ---
                    if cc_num == self.midi_config.get_volume_slider_cc(track_idx):
                        target_param = 'vol'
                        norm_val = cc_val / 127.0

                    # --- Standard MIDI Controller Mapping ---
                    elif cc_num == 1: target_param = 'cc1'
                    elif cc_num == 7:
                        target_param = 'vol'
                        norm_val = cc_val / 127.0
                    elif cc_num == 10:
                        target_param = 'pan'
                        norm_val = (cc_val / 127.0) * 2.0 - 1.0

                    # 1. Search for an automation track that is targeting this track
                    found_auto = False
                    for i, t in enumerate(self.song.tracks):
                        if isinstance(t, AutomationTrack) and t.target_track_index == track_idx:
                            current_target_param = target_param
                            # Also check if it matches the current active custom CC lane
                            if not current_target_param and t.active_parameter.lower() == f"cc{cc_num}":
                                current_target_param = t.active_parameter.lower()

                            if current_target_param:
                                # Optimization: don't sort inside the loop
                                self._add_smoothed_automation_point(t, current_target_param, event_data['start_time'], norm_val, sort=False)
                                t.active_parameter = current_target_param
                                found_auto = True
                                tracks_to_sort.add(i)

                    # 2. Auto-create automation track for standard parameters if not found
                    if not found_auto and target_param:
                        auto_track_idx = self._find_or_create_automation_track(track_idx, target_param)
                        if auto_track_idx is not None:
                            auto_track = self.song.tracks[auto_track_idx]
                            self._add_smoothed_automation_point(auto_track, target_param, event_data['start_time'], norm_val, sort=False)
                            tracks_to_sort.add(auto_track_idx)
                            found_auto = True

                    if not found_auto and is_midi_track(track):
                        # Fallback: record as standard CC message on the MIDI track
                        cc = CCMessage(control=cc_num, value=cc_val)
                        existing_event = next((e for e in track.events if math.isclose(e.start_time, event_data['start_time'], abs_tol=0.001)), None)
                        if existing_event:
                            existing_event.cc_messages.append(cc)
                        else:
                            event = Event(start_time=event_data['start_time'], cc_messages=[cc])
                            track.add_event(event)
                elif event_data['type'] == 'pitchwheel':
                    pitch_val = event_data['pitch']
                    # Mido pitch is 0-16383, center is 8192.
                    norm_val = (float(pitch_val) - 8192.0) / 8192.0
                    found_auto = False
                    for i, t in enumerate(self.song.tracks):
                        if isinstance(t, AutomationTrack) and t.target_track_index == track_idx:
                            self._add_smoothed_automation_point(t, 'pitch', event_data['start_time'], norm_val, sort=False)
                            tracks_to_sort.add(i)
                            found_auto = True
                    if not found_auto:
                        auto_track_idx = self._find_or_create_automation_track(track_idx, 'pitch')
                        if auto_track_idx is not None:
                            self._add_smoothed_automation_point(self.song.tracks[auto_track_idx], 'pitch', event_data['start_time'], norm_val, sort=False)
                            tracks_to_sort.add(auto_track_idx)

                elif event_data['type'] == 'program':
                    prog_val = event_data['program']
                    found_auto = False
                    for i, t in enumerate(self.song.tracks):
                        if isinstance(t, AutomationTrack) and t.target_track_index == track_idx:
                            self._add_smoothed_automation_point(t, 'prog', event_data['start_time'], float(prog_val), sort=False)
                            tracks_to_sort.add(i)
                            found_auto = True
                    if not found_auto:
                        auto_track_idx = self._find_or_create_automation_track(track_idx, 'prog')
                        if auto_track_idx is not None:
                            self._add_smoothed_automation_point(self.song.tracks[auto_track_idx], 'prog', event_data['start_time'], float(prog_val), sort=False)
                            tracks_to_sort.add(auto_track_idx)

                any_added = True
            except IndexError:
                break

        if any_added:
            # Batch sort all modified tracks
            for idx in tracks_to_sort:
                self.song.tracks[idx].points.sort(key=lambda p: p.start_time)

            self.is_dirty = True
            self._trigger_song_structure_change()

    def _add_smoothed_automation_point(self, auto_track: AutomationTrack, parameter: str, start_time: float, value: float, sort: bool = True):
        """Adds an automation point, smoothing out almost collinear points to reduce density."""
        key = (id(auto_track), parameter)
        if key not in self._last_recorded_auto_points:
            point = AutomationPoint(start_time=start_time, parameter=parameter, value=value, curve='linear')
            auto_track.add_point(point, sort=sort)
            self._last_recorded_auto_points[key] = [point, None] # [last, prev]
            return

        last_p, prev_p = self._last_recorded_auto_points[key]

        # Optimization logic:
        # If the new point is almost on the same line as the segment [prev_p, last_p],
        # we update last_p instead of adding a new point.
        if prev_p is not None:
            # Linear interpolation check
            time_gap = last_p.start_time - prev_p.start_time
            if time_gap > 0:
                # Expected value at last_p.start_time if it was on a line from prev_p to new point
                total_gap = start_time - prev_p.start_time
                if total_gap > 0:
                    ratio = time_gap / total_gap
                    expected_val = prev_p.value + ratio * (value - prev_p.value)

                    # Threshold for 'almost collinear'.
                    # For 0-127 CCs, 0.5 is a good balance.
                    # For normalized 0-1, it's 0.5 / 127 = ~0.004
                    threshold = 0.51 if parameter.startswith('cc') or parameter in ['vel', 'prog'] else 0.004

                    # Also don't allow segments longer than 4 beats without a point
                    if abs(last_p.value - expected_val) < threshold and total_gap < 4.0:
                        # Replace last point
                        last_p.start_time = start_time
                        last_p.value = value
                        return

        # Add new point and shift history
        new_p = AutomationPoint(start_time=start_time, parameter=parameter, value=value, curve='linear')
        auto_track.add_point(new_p, sort=sort)
        self._last_recorded_auto_points[key] = [new_p, last_p]

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
            if isinstance(track, AudioTrack):
                duration = self._get_audio_duration_in_beats(track)
                max_beat = max(max_beat, track.start_time + duration)
            elif isinstance(track, MidiTrack):
                for event in getattr(track, 'events', []):
                    for note in event.notes:
                        max_beat = max(max_beat, event.start_time + note.duration)
            elif isinstance(track, AutomationTrack):
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

    def panic(self):
        """Sends Panic (All Notes Off, Reset All Controllers) to all MIDI channels on all open ports."""
        if self.jack_manager:
            self.jack_manager.silence_all_midi_notes()

    def _all_notes_off(self):
        self.panic()

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
        self.tempo = tempo
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
            self.song_structure_changed += 1
            if self.jack_manager.is_running:
                self.jack_manager._prepare_automation_events()
            
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

        # Update display order: remove the index and decrement all higher indices
        if track_index in self.song.track_display_order:
            self.song.track_display_order.remove(track_index)

        new_display_order = []
        for idx in self.song.track_display_order:
            if idx > track_index:
                new_display_order.append(idx - 1)
            else:
                new_display_order.append(idx)
        self.song.track_display_order = new_display_order

        self.is_dirty = True
        self.invalidate_song_length_cache()
        
        self.song_structure_changed += 1
        if self.jack_manager.is_running:
            self.jack_manager._prepare_automation_events()
                
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

    def move_track_display_order(self, old_display_index: int, new_display_index: int):
        """Moves a track's position in the visual display order."""
        if not (0 <= old_display_index < len(self.song.track_display_order)):
            return
        if not (0 <= new_display_index < len(self.song.track_display_order)):
            return

        # Pop the track index from its old position and insert it into the new one
        track_idx = self.song.track_display_order.pop(old_display_index)
        self.song.track_display_order.insert(new_display_index, track_idx)
        self.is_dirty = True
        self.song_structure_changed += 1

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
            if self.jack_manager.is_running and track.output_port_name:
                port = self.jack_manager.open_ports.get(track.output_port_name)
                if port:
                    midi_volume = int(volume * 127)
                    port.send(mido.Message("control_change", channel=track.channel, control=7, value=midi_volume))

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
                # Read current position
                state, pos_dict = self.jack_manager.get_safe_transport_pos()
                if pos_dict is None:
                    return
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
            if self.jack_manager.is_running and track.output_port_name:
                port = self.jack_manager.open_ports.get(track.output_port_name)
                if port:
                    # Conversion de pan (-1.0 à 1.0) en valeur MIDI (0 à 127)
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
                if track.output_port_name and track.output_port_name in self.jack_manager.open_ports:
                    port = self.jack_manager.open_ports[track.output_port_name]
                    if track.is_muted:
                        # Silence all notes for this track
                        for cc in (123, 120, 121):
                            port.send(mido.Message('control_change', channel=track.channel, control=cc, value=0))
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
                        if track.output_port_name and track.output_port_name in self.jack_manager.open_ports:
                            port = self.jack_manager.open_ports[track.output_port_name]
                            port.send(mido.Message('control_change', channel=track.channel, control=123, value=0))
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

            if isinstance(track, MidiTrack) and track.output_port_name:
                should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                port = self.jack_manager.open_ports.get(track.output_port_name)

                if port:
                    if should_be_audible:
                        try:
                            output += f"  - Priming MIDI track '{track.name}' to '{port.name}' on Ch: {track.channel + 1}\n"

                            # SAFETY CUT: Kill any zombie notes before restoring volume
                            port.send(mido.Message('control_change', channel=track.channel, control=64, value=0))  # Sustain Off
                            port.send(mido.Message('control_change', channel=track.channel, control=123, value=0)) # All Notes Off
                            port.send(mido.Message('control_change', channel=track.channel, control=120, value=0)) # All Sound Off

                            # Bank and Program changes are always sent, unless automation for them exists at the start.
                            if track.bank_msb is not None and (i, 'cc0') not in primed_by_automation:
                                port.send(mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb))
                            if track.bank_lsb is not None and (i, 'cc32') not in primed_by_automation:
                                port.send(mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb))
                            if (i, 'prog') not in primed_by_automation:
                                port.send(mido.Message('program_change', channel=track.channel, program=track.instrument))

                            # Only send volume if not already handled by automation.
                            if (i, 'vol') not in primed_by_automation:
                                midi_volume = int(track.volume * 127)
                                port.send(mido.Message('control_change', channel=track.channel, control=7, value=midi_volume))

                            # Only send pan if not already handled by automation.
                            if (i, 'pan') not in primed_by_automation:
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

    def load_gp_file(self, filepath: str) -> str:
        """Loads a GuitarPro file as a new project and sets up virtual ports."""
        try:
            if self.playback_state != "stopped":
                self.stop()

            # Close and clear existing virtual ports
            self.close_virtual_ports()
            self.virtual_ports = []

            # Perform the import
            self.song = import_gp(filepath)
            self.tempo = self.song.tempo
            self.is_dirty = True
            self.last_project_basename = None
            self.invalidate_song_length_cache()
            self.last_record_settings = None

            # Create virtual ports for each MIDI track and assign them
            for track in self.song.tracks:
                if is_midi_track(track):
                    port_name = track.name
                    # Make sure the port name is unique if needed, but for now we use the track name
                    self.create_virtual_port(port_name)
                    track.output_port_name = port_name

            # Restart Jack Manager to register new ports and handle routing
            if self.jack_manager:
                self.jack_manager.stop()
                self.jack_manager.start()

                self.jack_manager._manual_routing_override = -1
                initial_idx = self.jack_manager._get_input_routing_value(0.0)
                if initial_idx is not None:
                    self.current_routing_index = initial_idx

            return f"Successfully loaded GuitarPro file from '{filepath}' and created virtual ports."
        except Exception as e:
            return f"Error loading GuitarPro file: {e}"

    def load_song(self, filepath: str) -> str:
        try:
            self.song = import_song(filepath)
            self.is_dirty = True
            self.last_project_basename = None
            self.invalidate_song_length_cache()
            self.last_record_settings = None

            # Initialize routing status for the UI
            if self.jack_manager:
                self.jack_manager._manual_routing_override = -1
                initial_idx = self.jack_manager._get_input_routing_value(0.0)
                if initial_idx is not None:
                    self.current_routing_index = initial_idx

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
            self.tempo = self.song.tempo
            self.last_record_settings = None


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
            self.last_record_settings = None

            # Initialize routing status for the UI
            if self.jack_manager:
                self.jack_manager._manual_routing_override = -1
                initial_idx = self.jack_manager._get_input_routing_value(0.0)
                if initial_idx is not None:
                    self.current_routing_index = initial_idx

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
        self.last_record_settings = None

        # Initialize routing status for the UI
        if self.jack_manager:
            self.jack_manager._manual_routing_override = -1
            initial_idx = self.jack_manager._get_input_routing_value(0.0)
            if initial_idx is not None:
                self.current_routing_index = initial_idx

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
        try:
            port = open_output(name, virtual=True)
            self.virtual_ports.append(port)
            self.is_dirty = True
            return f"Created virtual MIDI port: '{name}'"
        except Exception as e:
            return f"Error creating virtual port: {e}"

    def close_virtual_ports(self):
        """Cleanly shutdown all sequencer resources, including engine and external processes."""
        if self.jack_manager:
            self.jack_manager.stop()
            print("JACK engine stopped.")

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

        # 1. Look for an existing automation track for this specific target
        for i, track in enumerate(self.song.tracks):
            if isinstance(track, AutomationTrack) and track.target_track_index == target_track_index:
                # We reuse the same automation track for ALL parameters of a target track
                return i

        # 2. Create a new one if not found
        new_track_name = f"{target_track.name} Automation"
        print(f"\nCreating new automation track: '{new_track_name}'")
        new_track = AutomationTrack(name=new_track_name, target_track_index=target_track_index, active_parameter=parameter_name)
        self.song.add_track(new_track)
        self.is_dirty = True
        return len(self.song.tracks) - 1

    def _get_current_beat(self) -> float:
        if self.jack_manager and self.jack_manager.is_running and self.jack_manager.jack_client:
            try:
                _ , pos = self.jack_manager.get_safe_transport_pos()
                if pos is None:
                    return self.current_beat

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

    def _recording_thread_main(self, initial_track_idx, start_beat, inport_name, num_beats_to_record, enable_thru):
            # Le dictionnaire stockera: {pitch: (start_beat, velocity, track_idx)}
            open_notes = {}
            first_note_detected = False
            recording_start_beat = None
            processed_tracks = set() # Tracks encountered during this session

            try:
                with mido.open_input(inport_name) as inport:
                    print(f"Port d'entrée MIDI ouvert: {inport_name}")
                    list(inport.iter_pending())
                    
                    # Target track name for logging
                    initial_target_idx = self.jack_manager._get_input_routing_value(start_beat)
                    if initial_target_idx is not None and 0 <= initial_target_idx < len(self.song.tracks):
                        target_name = self.song.tracks[initial_target_idx].name
                    else:
                        target_name = "Dynamic Routing"

                    print(f"En attente de la première note sur '{target_name}'...")

                    pending_trigger_msg = None
                    while not self._stop_event.is_set() and not first_note_detected:
                        if not self.is_recording:
                             print("Recording armed state cancelled.")
                             return

                        if self.playback_state == 'playing':
                            first_note_detected = True
                            recording_start_beat = self._get_current_beat()
                            print(f"Enregistrement démarré à {self._format_beats_to_position(recording_start_beat)}")
                            break

                        msg = inport.poll()
                        if msg:
                            # Any MIDI activity can trigger recording (Note, CC, Pitch, Program)
                            is_trigger = False
                            if msg.type == 'note_on' and msg.velocity > 0: is_trigger = True
                            elif msg.type in ('control_change', 'pitchwheel', 'program_change'): is_trigger = True

                            if is_trigger:
                                first_note_detected = True
                                pending_trigger_msg = msg
                                self.play(start_beat=start_beat)
                                # Small delay to allow transport to start and get a reliable beat
                                time.sleep(0.05)
                                recording_start_beat = self._get_current_beat()
                                print(f"Enregistrement déclenché par {msg.type} à {self._format_beats_to_position(recording_start_beat)}")
                                break

                        time.sleep(0.01)

                    if not first_note_detected:
                        self.is_recording = False
                        return

                    while not self._stop_event.is_set():
                        current_beat = self._get_current_beat()
                        
                        # Get current target track from MIDI routing
                        target_idx = self.jack_manager._get_input_routing_value(current_beat)

                        # Handle dynamic activation for OVERWRITE mode as soon as a track becomes the target
                        if target_idx is not None and 0 <= target_idx < len(self.song.tracks):
                            if target_idx not in processed_tracks:
                                track = self.song.tracks[target_idx]
                                if is_midi_track(track):
                                    if track.record_mode == 'OVERWRITE':
                                        # Truncate from the SESSION START instead of current_beat
                                        # This ensures all "previous" notes (from start_beat) are cleared.
                                        session_end_beat = None if num_beats_to_record is None else start_beat + num_beats_to_record
                                        self._truncate_track_for_recording(target_idx, start_beat, session_end_beat)
                                processed_tracks.add(target_idx)

                        # Process triggering message if any, then pull pending
                        msgs = []
                        if pending_trigger_msg:
                            msgs.append(pending_trigger_msg)
                            pending_trigger_msg = None

                        msgs.extend(list(inport.iter_pending()))

                        for msg in msgs:
                            if msg.type == 'note_on' and msg.velocity > 0:
                                if target_idx is not None and 0 <= target_idx < len(self.song.tracks):
                                    track = self.song.tracks[target_idx]
                                    if is_midi_track(track):
                                        if msg.note not in open_notes:
                                            open_notes[msg.note] = (current_beat, msg.velocity, target_idx)
                                            # Thru
                                            if enable_thru and getattr(track, 'output_port_name', None) in self.open_ports:
                                                self.open_ports[track.output_port_name].send(msg.copy(channel=getattr(track, 'channel', 0)))

                            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                                if msg.note in open_notes:
                                    note_start, original_velocity, track_idx = open_notes.pop(msg.note)
                                    duration = current_beat - note_start
                                    
                                    if duration > 0:
                                        # Use the safe merger to avoid background thread issues and ensure UI refresh
                                        self.jack_manager._recorded_events_to_merge.append({
                                            'type': 'note',
                                            'track_idx': track_idx,
                                            'pitch': msg.note,
                                            'velocity': original_velocity,
                                            'start_time': note_start,
                                            'duration': duration
                                        })

                                    # Thru OFF
                                    if enable_thru:
                                        # Use original track_idx for Note Off to match the Note On channel
                                        t_idx = track_idx if track_idx is not None else target_idx
                                        if t_idx is not None and 0 <= t_idx < len(self.song.tracks):
                                            track = self.song.tracks[t_idx]
                                            if is_midi_track(track) and getattr(track, 'output_port_name', None) in self.open_ports:
                                                self.open_ports[track.output_port_name].send(msg.copy(channel=getattr(track, 'channel', 0)))

                            elif msg.type == 'control_change':
                                # Global check for hardware-mapped faders (Volume Sliders 0-7)
                                hw_track_idx = None
                                for i in range(len(self.song.tracks)):
                                    if msg.control == self.midi_config.get_volume_slider_cc(i):
                                        hw_track_idx = i
                                        break

                                # Use hardware track index if mapped, otherwise use current routing target
                                final_track_idx = hw_track_idx if hw_track_idx is not None else target_idx

                                if final_track_idx is not None and 0 <= final_track_idx < len(self.song.tracks):
                                    processed_tracks.add(final_track_idx)
                                    track = self.song.tracks[final_track_idx]
                                    # Record CC (Supported for both MIDI and Audio tracks via automation)
                                    self.jack_manager._recorded_events_to_merge.append({
                                        'type': 'cc',
                                        'track_idx': final_track_idx,
                                        'control': msg.control,
                                        'value': msg.value,
                                        'start_time': current_beat
                                    })
                                    # Thru (Only for MIDI tracks)
                                    if enable_thru and is_midi_track(track) and getattr(track, 'output_port_name', None) in self.open_ports:
                                        self.open_ports[track.output_port_name].send(msg.copy(channel=getattr(track, 'channel', 0)))
                            elif msg.type == 'pitchwheel':
                                if target_idx is not None and 0 <= target_idx < len(self.song.tracks):
                                    processed_tracks.add(target_idx)
                                    track = self.song.tracks[target_idx]
                                    self.jack_manager._recorded_events_to_merge.append({
                                        'type': 'pitchwheel',
                                        'track_idx': target_idx,
                                        'pitch': msg.pitch,
                                        'start_time': current_beat
                                    })
                                    if enable_thru and is_midi_track(track) and getattr(track, 'output_port_name', None) in self.open_ports:
                                        self.open_ports[track.output_port_name].send(msg.copy(channel=getattr(track, 'channel', 0)))
                            elif msg.type == 'program_change':
                                if target_idx is not None and 0 <= target_idx < len(self.song.tracks):
                                    processed_tracks.add(target_idx)
                                    track = self.song.tracks[target_idx]
                                    self.jack_manager._recorded_events_to_merge.append({
                                        'type': 'program',
                                        'track_idx': target_idx,
                                        'program': msg.program,
                                        'start_time': current_beat
                                    })
                                    if enable_thru and is_midi_track(track) and getattr(track, 'output_port_name', None) in self.open_ports:
                                        self.open_ports[track.output_port_name].send(msg.copy(channel=getattr(track, 'channel', 0)))

                        if (num_beats_to_record is not None and
                                current_beat >= (start_beat + num_beats_to_record)):
                            Clock.schedule_once(lambda dt: self._stop_playback_transport())
                            self._stop_event.set()
                            break
                        
                        # Polling often to avoid ALSA buffer overflow, but processing efficiently
                        time.sleep(0.001)

            except Exception as e:
                print(f"Erreur lors de l'enregistrement: {e}")
                import traceback
                traceback.print_exc()
            
            finally:
                current_beat = self._get_current_beat()
                for note, (note_start, original_velocity, track_idx) in open_notes.items():
                    duration = current_beat - note_start
                    if duration > 0:
                        self.jack_manager._recorded_events_to_merge.append({
                            'type': 'note',
                            'track_idx': track_idx,
                            'pitch': note,
                            'velocity': original_velocity,
                            'start_time': note_start,
                            'duration': duration
                        })

                # Perform post-recording smoothing
                def do_smoothing(dt):
                    self.is_smoothing = True
                    try:
                        # Find all automation tracks that might have been modified
                        # Using indices to avoid unhashable type error for AutomationTrack
                        processed_auto_indices = set()
                        for tidx in processed_tracks:
                            for i, t in enumerate(self.song.tracks):
                                if isinstance(t, AutomationTrack) and t.target_track_index == tidx:
                                    processed_auto_indices.add(i)

                        for auto_idx in processed_auto_indices:
                             self._smooth_track_automation(auto_idx)

                        # IMPORTANT: Flush the merge queue first
                        self._merge_recorded_events(0)

                        # IMPORTANT: Set is_recording to False BEFORE preparing events
                        # so that the suppression logic in JackManager doesn't skip them.
                        self.is_recording = False

                        if self.jack_manager:
                            self.jack_manager._prepare_automation_events()

                        # Trigger final UI refresh
                        self._trigger_song_structure_change()
                    finally:
                        self.is_smoothing = False
                        self.is_recording = False
                        self._stop_event.clear()
                        print("Enregistrement terminé.")

                Clock.schedule_once(do_smoothing)

    def _truncate_track_for_recording(self, track_idx: int, start_beat: float, end_beat: Optional[float]):
        """Helper to truncate notes on a track before/during recording (OVERWRITE mode)."""
        if not 0 <= track_idx < len(self.song.tracks):
            return
        target_track = self.song.tracks[track_idx]
        if not isinstance(target_track, MidiTrack):
            return

        end_beat_for_deletion = float("inf") if end_beat is None else end_beat

        final_events = []
        for event in target_track.events:
            event_start_time = event.start_time
            if event_start_time < start_beat:
                notes_to_keep = []
                for note in event.notes:
                    note_end_time = event_start_time + note.duration
                    if note_end_time <= start_beat:
                        notes_to_keep.append(note)
                    elif event_start_time < start_beat < note_end_time:
                        note.duration = start_beat - event_start_time
                        notes_to_keep.append(note)
                event.notes = notes_to_keep
                if event.notes or event.cc_messages:
                    final_events.append(event)
            elif start_beat <= event_start_time < end_beat_for_deletion:
                event.notes.clear()
                if event.cc_messages:
                    final_events.append(event)
            else:
                final_events.append(event)

        new_events = [e for e in final_events if e.notes or e.cc_messages]

        # UI property update must be on main thread
        def apply_truncation(dt):
            target_track.events = new_events
            self._trigger_song_structure_change()

        if threading.current_thread() is threading.main_thread():
            apply_truncation(None)
        else:
            Clock.schedule_once(apply_truncation)

    def _trigger_song_structure_change(self):
        """Triggers a UI refresh due to song structure changes."""
        self.invalidate_song_length_cache()
        self.song_structure_changed += 1

    def _smooth_track_automation(self, track_idx: int):
        """Applies Ramer-Douglas-Peucker smoothing to newly recorded automation on a track."""
        track = self.song.tracks[track_idx]
        if not isinstance(track, AutomationTrack) or not track.points:
            return

        # Separate points by parameter to smooth them independently
        points_by_param = {}
        for p in track.points:
            points_by_param.setdefault(p.parameter, []).append(p)

        new_total_points = []
        for param, points in points_by_param.items():
            if len(points) < 3:
                new_total_points.extend(points)
                continue

            # Apply RDP algorithm
            # We use a threshold relative to the parameter range
            # For 0-127 (CCs), 1.0 is ~0.8% error.
            # For 0-1 (Vol), 0.01 is 1% error.
            if param.startswith('cc') or param in ['vel', 'prog']:
                epsilon = 0.4 # Slightly less aggressive to preserve "assez proche"
            elif param in ['pitch', 'pb']:
                epsilon = 0.005 # More sensitive to fine movements
            else:
                epsilon = 0.002

            smoothed = self._rdp(points, epsilon)
            new_total_points.extend(smoothed)

        # Update track points and sort
        track.points = sorted(new_total_points, key=lambda p: p.start_time)

    def _rdp(self, points: List[AutomationPoint], epsilon: float) -> List[AutomationPoint]:
        """Simplified Ramer-Douglas-Peucker algorithm implementation."""
        if len(points) < 3:
            return points

        dmax = 0.0
        index = 0
        end = len(points) - 1

        for i in range(1, end):
            d = self._perpendicular_distance(points[i], points[0], points[end])
            if d > dmax:
                index = i
                dmax = d

        if dmax > epsilon:
            res1 = self._rdp(points[:index+1], epsilon)
            res2 = self._rdp(points[index:], epsilon)
            return res1[:-1] + res2
        else:
            return [points[0], points[end]]

    def _perpendicular_distance(self, p, start, end):
        """Calculates perpendicular distance from point p to line (start, end)."""
        # We normalize time vs value roughly for distance calculation
        # For CCs (0-127), we might want to scale beats to make them comparable.
        # But keeping it simple for now as per user request for "assez proche".
        x, y = p.start_time, p.value
        x1, y1 = start.start_time, start.value
        x2, y2 = end.start_time, end.value

        if x1 == x2:
            if y1 == y2:
                return math.sqrt((x - x1)**2 + (y - y1)**2)
            return abs(x - x1)

        # Distance from point (x,y) to line (x1,y1)-(x2,y2)
        numerator = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1)
        denominator = math.sqrt((y2 - y1)**2 + (x2 - x1)**2)
        if denominator == 0:
            return math.sqrt((x - x1)**2 + (y - y1)**2)
        return numerator / denominator

    def _start_recording_internal(self, track_index: Optional[int], start_beat: float, num_beats_to_record: Optional[float], inport_name: str, replace_notes: Optional[bool], enable_thru: bool):
            # Truncation for OVERWRITE mode
            end_beat = None if num_beats_to_record is None else start_beat + num_beats_to_record
            if track_index is not None:
                target_track = self.song.tracks[track_index]
                if is_midi_track(target_track):
                    should_replace = target_track.record_mode == 'OVERWRITE' if replace_notes is None else replace_notes
                    if should_replace:
                        self._truncate_track_for_recording(track_index, start_beat, end_beat)
            else:
                # Dynamic Routing: Pre-truncate all tracks armed for OVERWRITE at session start
                for i, track in enumerate(self.song.tracks):
                    if is_midi_track(track) and track.record_mode == 'OVERWRITE':
                        self._truncate_track_for_recording(i, start_beat, end_beat)

            self.is_recording = True
            self.recording_thread = threading.Thread(
                target=self._recording_thread_main,
                args=(track_index, start_beat, inport_name, num_beats_to_record, enable_thru)
            )
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


            # Reset smoothing state
            self._last_recorded_auto_points.clear()

            # track_idx is None means we follow dynamic MIDI Routing
            # self.last_record_settings is checked for record_bis
            # We refresh settings to ensure UI Start/End positions are always respected
            if True:
                # NEW SESSION
                if inport_name is None:
                    if self.default_record_port:
                        inport_name = self.default_record_port
                    else:
                        input_ports = get_input_names()
                        if not input_ports:
                            return "Error: No MIDI input ports available and no default port set."
                        inport_name = input_ports[0]
                
                if start_beat is None:
                    start_pos = self.ui_start_pos_str or "1:1"
                    start_beat = self.parse_position_to_beats(start_pos) or 0.0

                end_pos = self.ui_end_pos_str
                end_beat = self.parse_position_to_beats(end_pos) if end_pos else None
                if end_beat is not None and end_beat > start_beat:
                    num_beats_to_record = end_beat - start_beat

                self.last_record_settings = {
                    "track_index": track_idx,
                    "start_beat": start_beat,
                    "num_beats_to_record": num_beats_to_record,
                    "inport_name": inport_name,
                    "replace_notes": replace_notes,
                    "enable_thru": enable_thru
                }
            else:
                # RE-RECORD (record_bis or manual retry)
                # We use existing last_record_settings but track_index might still be None
                pass

            settings = self.last_record_settings.copy()
            self._stop_event.clear()
            self._start_recording_internal(**settings)

            start_pos_msg = self._format_beats_to_position(settings['start_beat'])
            if settings['track_index'] is not None:
                target_track = self.song.tracks[settings['track_index']]
                return f"Recording armed on track '{target_track.name}' at {start_pos_msg}. Waiting for first MIDI note..."
            else:
                return f"Recording armed (Dynamic Routing) at {start_pos_msg}. Waiting for first MIDI note..."
        

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
        
        # NOTE: We no longer automatically disable other tracks.
        # This allows multiple tracks to follow the MIDI routing during recording.
        
        track.record_mode = mode
        self.is_dirty = True

        self.song_structure_changed += 1
        if self.jack_manager.is_running:
            self.jack_manager._prepare_automation_events()
                
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

    '''
    def _calculate_song_length_in_beats(self) -> float:
        """Calculates the total length of the song in beats, considering both MIDI and audio tracks."""
        max_beats = 0.0
        tracks = self.song.tracks
        is_any_track_soloed = any(t.is_solo for t in tracks)

        for track in tracks:
            should_play = (track.is_solo or not is_any_track_soloed) and not track.is_muted
            if not should_play:
                continue

            if isinstance(track, MidiTrack):
                for event in track.events:
                    for note in event.notes:
                        event_end_beat = event.start_time + note.duration
                        if event_end_beat > max_beats:
                            max_beats = event_end_beat
            elif isinstance(track, AudioTrack):
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
    '''
    
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
        param_map = {"vol": {"type": "midi_cc", "control": 7}, "pan": {"type": "midi_cc", "control": 10}, "vel": {"type": "velocity_multiplier"}, "prog": {"type": "program_change"}, "pitch": {"type": "pitch_bend"}, "pb": {"type": "pitch_bend"}, **{f"cc{i}": {"type": "midi_cc", "control": i} for i in range(128)}}

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

    def _resync_all_at_beat(self, beat: float, force_play: bool = False, synchronous: bool = False):
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
            self.jack_manager.seek_audio_to_beat(beat, synchronous=synchronous)
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
                # Selective unpause is now handled by JackManager._process_callback
                print("[DIAGNOSTIC] Transport restarted.")

        except jack.JackError as e:
            print(f"Error during resynchronization: {e}", file=sys.stderr)
        finally:
            print(f"[DIAGNOSTIC] === _resync_all_at_beat END (With JACK) ===\n")

    def play(self, start_beat: Optional[float] = None):
        # Clear manual routing override when starting playback
        if self.jack_manager:
            self.jack_manager._manual_routing_override = -1

    # 1. On change l'état IMMÉDIATEMENT (Optimisme)
        self.playback_state = "playing"
        self._last_play_click_time = time.perf_counter() # Pour le poll_engine_state
        self._last_transport_command_time = time.perf_counter()
        
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            self.jack_manager.start()
            time.sleep(0.2) # Give JACK time to start and connect

        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            print("Error: Could not start JACK client.")
            return

        # Store the beat from which playback is starting
        effective_start_beat = start_beat if start_beat is not None else self.rewind_beat
        self.last_play_start_beat = effective_start_beat

        # If a start beat is provided, reposition the transport
        if start_beat is not None:
            beats_per_second = self.song.tempo / 60.0
            samplerate = self.jack_manager.jack_client.samplerate
            if beats_per_second > 0 and samplerate > 0:
                target_frame = int((start_beat / beats_per_second) * samplerate)
                _ , pos = self.jack_manager.jack_client.transport_query_struct()
                pos.frame = target_frame
                self.jack_manager.jack_client.transport_reposition_struct(pos)

        # Prime automation first and get the set of parameters it handled.
        primed_by_automation = self.jack_manager._prime_automation_at_beat(effective_start_beat)

        # Then, prime the tracks, passing in the set so it can skip what's already done.
        self.prime_all_tracks(primed_by_automation=primed_by_automation)

        # Simply tell JACK to start rolling
#        if self.jack_manager.jack_client.transport_state != jack.ROLLING:
#            self.jack_manager.jack_client.transport_start()
#            self.playback_state = "playing"
        if self.jack_manager:
                self.jack_manager.jack_client.transport_start()            

        self.current_routing_index = -1
        self._update_current_routing()

    def pause(self):
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            return

        self._last_transport_command_time = time.perf_counter()

        try:
            if self.jack_manager.jack_client.transport_state == jack.ROLLING:
                # Get current beat BEFORE stopping
                current_beat = self._get_current_beat()
                print(f"\n[DIAGNOSTIC] --- PAUSING at beat {current_beat:.6f} ---")
                self.jack_manager.jack_client.transport_stop()

                # Silence all MIDI notes to prevent hanging notes during pause
                self.jack_manager.silence_all_midi_notes()

                self.playback_state = "paused"
                # Store the precise beat for resume
                self.pause_beat = current_beat
            elif self.playback_state == "paused":
                print(f"\n[DIAGNOSTIC] --- RESUMING from beat {self.pause_beat:.6f} ---")
                self._last_play_click_time = time.perf_counter()
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

        try:
            if self.jack_manager.jack_client.transport_state == jack.ROLLING:
                self.jack_manager.jack_client.transport_stop()
                print("JACK transport stopped.")
                # Give a tiny bit of time for the engine to register the stop
                time.sleep(0.02)

            # Silence EVERYTHING immediately after stopping transport
            self.jack_manager.silence_all_midi_notes()

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
        # 1. On change l'état LOCAL immédiatement pour bloquer le polling
        self.playback_state = 'stopped'
        self._last_transport_command_time = time.perf_counter()
        
        if self.is_recording and self.recording_thread:
            print("Stopping recording...")
            self._stop_event.set()
            # Wait for the recording thread to finish its cleanup
            self.recording_thread.join(timeout=1.0)
            self.is_recording = False

            # After recording, invalidate the cache so the new length is calculated
            self.invalidate_song_length_cache()
            # Trigger a UI refresh to redraw all tracks to the new length
            self.song_structure_changed += 1

            # After the recording thread has stopped itself, we might not need to stop playback again
            # as it might have already done so. However, calling it ensures a consistent state.
            if self.playback_state != "stopped":
                 self._stop_playback_transport()
        else:
            self._stop_playback_transport()

        self.current_routing_index = -1
        
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
            _, pos_dict = self.jack_manager.get_safe_transport_pos()
            if pos_dict:
                current_frame = pos_dict.get('frame', 0)
            else:
                current_frame = 0

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
            _, pos_struct = self.jack_manager.jack_client.transport_query_struct()
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
