import pedalboard as pb
from pedalboard import VST3Plugin
from .models import AudioTrack, VSTPlugin as VSTPluginModel
import numpy as np
import soundfile as sf
import os
import tempfile
from typing import List

class VSTAudioProcessor:
    def __init__(self, sequencer):
        self.sequencer = sequencer
        self.processed_audio_cache = {}  # Cache to store paths to processed audio files

    def process_track(self, track_index: int) -> str:
        """
        Processes an audio track with its VST plugin chain and returns the path to the processed audio file.
        """
        track = self.sequencer.song.tracks[track_index]
        if not isinstance(track, AudioTrack) or not track.plugins:
            return track.filepath

        # Generate a unique key for the current plugin chain state
        cache_key = self._generate_cache_key(track)
        if cache_key in self.processed_audio_cache:
            return self.processed_audio_cache[cache_key]

        try:
            with sf.SoundFile(track.filepath) as f:
                samplerate = f.samplerate
                audio = f.read(always_2d=True).T

            board = pb.Pedalboard()
            for plugin_model in track.plugins:
                vst = VST3Plugin(plugin_model.path)
                for param, value in plugin_model.parameters.items():
                    setattr(vst, param, value)
                board.append(vst)

            effected = board(audio, samplerate)

            temp_dir = tempfile.gettempdir()
            processed_filename = f"processed_{track_index}_{os.path.basename(track.filepath)}"
            processed_filepath = os.path.join(temp_dir, processed_filename)

            with sf.SoundFile(processed_filepath, 'w', samplerate, effected.shape[0]) as f:
                f.write(effected.T)

            self.processed_audio_cache[cache_key] = processed_filepath
            return processed_filepath

        except Exception as e:
            print(f"Error processing VST chain for track {track_index}: {e}")
            return track.filepath

    def _generate_cache_key(self, track: AudioTrack) -> str:
        """Generates a string key representing the state of the plugin chain."""
        parts = [track.filepath]
        for plugin in track.plugins:
            parts.append(plugin.path)
            for param, value in sorted(plugin.parameters.items()):
                parts.append(f"{param}:{value}")
        return "|".join(parts)

    def clear_cache(self):
        """Clears the cache of processed audio files."""
        for path in self.processed_audio_cache.values():
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        self.processed_audio_cache.clear()
