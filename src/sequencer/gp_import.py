
import os
from .models import Song, MidiTrack, Note, Event, AutomationTrack, AutomationPoint

def import_gp(filepath: str) -> Song:
    """
    Imports a GuitarPro file and converts it to a Sequencer Song object.
    Handles GP3, GP4, GP5, and GPX (via pyguitarpro).
    """
    import guitarpro
    try:
        gp_song = guitarpro.parse(filepath)
    except Exception as e:
        # Check for GP7/GP8 'Kxxx' header
        try:
            with open(filepath, 'rb') as f:
                header = f.read(4)
                if header == b'Kpro':
                     raise ValueError("GP7/GP8 files (.gp) are not supported by pyguitarpro. Please export them to GP5 first.")
        except ValueError as ve:
             raise ve
        except: pass
        raise ValueError(f"Error parsing GuitarPro file: {e}")

    song = Song(name=gp_song.title or os.path.basename(filepath), tempo=gp_song.tempo)

    # GuitarPro tempo is typically at the start.
    song.tempo = int(gp_song.tempo)

    for gp_track in gp_song.tracks:
        # 1. Create the MIDI track
        track_name = gp_track.name if gp_track.name else f"Track {gp_track.number}"
        midi_track = MidiTrack(name=track_name)
        midi_track.channel = max(0, min(15, gp_track.channel.number - 1))
        midi_track.instrument = max(0, min(127, gp_track.channel.instrument))

        # 2. Extract notes and bends
        bend_points = [] # list of (beat_time, normalized_value)
        current_beat_time = 0.0

        for measure in gp_track.measures:
            # Update time signature from the first measure encountered
            if measure.number == 1:
                song.time_signature_numerator = measure.header.timeSignature.numerator
                denom = measure.header.timeSignature.denominator
                # Handle denominator which can be a Duration object or int
                song.time_signature_denominator = getattr(denom, 'value', denom)

            # GuitarPro measures can have multiple voices
            for voice in measure.voices:
                voice_beat_time = current_beat_time
                for beat in voice.beats:
                    # Duration calculation
                    # beat.duration.value: 1=whole, 2=half, 4=quarter, 8=eighth, 16=16th, 32=32th
                    duration_val = beat.duration.value
                    beat_duration = 4.0 / duration_val

                    # Handle dots
                    if beat.duration.isDotted:
                        beat_duration *= 1.5

                    # Handle tuplets
                    if beat.duration.tuplet:
                        enters = getattr(beat.duration.tuplet, 'enters', getattr(beat.duration.tuplet, 'enter', 1))
                        times = beat.duration.tuplet.times
                        if enters > 0:
                             beat_duration = beat_duration * times / enters

                    if beat.notes:
                        event = Event(start_time=voice_beat_time)
                        for gp_note in beat.notes:
                            # Skip tied notes
                            if gp_note.type == guitarpro.NoteType.tie:
                                continue

                            midi_note = Note(
                                pitch=gp_note.realValue,
                                velocity=gp_note.velocity,
                                duration=beat_duration
                            )
                            event.notes.append(midi_note)

                            # Handle Bends
                            if gp_note.bend:
                                for point in gp_note.bend.points:
                                    # gp point.position is 0-60
                                    # point.value is in 1/4 tones (8 points = 1 whole tone)
                                    rel_pos = point.position / 60.0
                                    abs_beat = voice_beat_time + (rel_pos * beat_duration)
                                    # Normalization for internal pitch bend (-1.0 to 1.0, assuming 2 semitones range)
                                    # GP point.value is in quarter tones. 4 quarter tones = 2 semitones (Standard MIDI Range).
                                    norm_val = point.value / 4.0
                                    bend_points.append((abs_beat, norm_val))

                        if event.notes:
                            midi_track.add_event(event)

                    voice_beat_time += beat_duration

            # Advance measure position
            current_beat_time += (4.0 / song.time_signature_denominator) * song.time_signature_numerator

        song.tracks.append(midi_track)

        # 3. Create Automation Track for Bends if any exist
        if bend_points:
            auto_track = AutomationTrack(
                name=f"{track_name} Bends",
                target_track_index=len(song.tracks) - 1
            )
            bend_points.sort(key=lambda x: x[0])
            for b_time, b_val in bend_points:
                auto_track.add_point(AutomationPoint(
                    start_time=b_time,
                    value=b_val,
                    parameter='pitch',
                    curve='linear'
                ))
            song.tracks.append(auto_track)

    return song
