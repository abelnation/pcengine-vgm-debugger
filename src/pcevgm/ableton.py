"""Ableton Live Simpler presets, one per instrument.

An .adv file is a single gzipped XML document. Rather than write that XML from
nothing, the generator patches a template exported from Live, so the schema is
one Live is known to accept. Only the sample reference, the volume envelope and
the names are replaced.

The envelope is the interesting part. A HuC6280 envelope is a run of amplitude
steps written one per video frame, each step 1.5 dB, which maps onto Simpler's
ADSR directly: the fall gives the decay, the level it settles at gives the
sustain, and continuing that same fall down to Simpler's -70 dB floor gives a
release in keeping with how the instrument actually decays.
"""

from __future__ import annotations

import gzip
import math
import os
import posixpath
import xml.etree.ElementTree as ET
from collections import Counter
from statistics import median

from .huc6280 import DB_PER_STEP, WAVE_LENGTH
from .notes import FRAME, Analysis
from .waves import CYCLE_HZ, WAV_SUFFIX, cycle_rate

TEMPLATE = os.path.join(os.path.dirname(__file__), "data", "simpler.adv")
SUFFIX = ".adv"
FOLDER = "ableton"

PART = "OriginalSimpler/Player/MultiSampleMap/SampleParts/MultiSamplePart"
ENVELOPE = "OriginalSimpler/VolumeAndPan/Envelope"

# Simpler's own limits, read off the template's MidiControllerRange entries.
LEVEL_FLOOR = 0.0003162277571  # -70 dB
ATTACK_RANGE = (0.1, 20000.0)
TIME_RANGE = (1.0, 60000.0)
FLOOR_DB = 70.0  # LEVEL_FLOOR expressed in dB below the peak

SUSTAIN_LOOP = 1  # forward. Live's enum is not documented here; 0 is off.
ROOT_KEY = 60  # C4, which is what the single cycle wav is tuned to
DEFAULT_RELEASE_MS = 200.0  # for an envelope that never falls
FRAME_MS = FRAME / 44100 * 1000

# Two presets are the same sound when their times agree and their sustains
# agree. A time closer than one video frame is the same time, because that is
# how often the driver can change anything. A sustain within 1.5 dB is the
# same level, because that is one step of the chip's own amplitude register.
TOLERANCE = 0.10
SUSTAIN_TOLERANCE_DB = DB_PER_STEP
# An instrument the detector fired once is usually its own guesswork. Across
# the sample rips, instruments used twice or more carry 99% of all notes.
MIN_NOTES = 2


def _clamp(value, low, high):
    return max(low, min(high, value))


def _step_ms(analysis: Analysis, instrument: int) -> float:
    """How long one envelope step lasts, from the notes that use it."""
    spans = []
    for note in analysis.notes:
        if note.instrument == instrument and len(note.amps) > 1:
            span = note.amps[-1][0] - note.amps[0][0]
            spans.append(span / (len(note.amps) - 1))
    if not spans:
        return FRAME / 44100 * 1000
    return median(spans) / 44100 * 1000


def adsr(analysis: Analysis, instrument: int):
    """Attack, decay, sustain and release for one instrument.

    Times are milliseconds and the sustain is linear amplitude, which is what
    Simpler stores.
    """
    _, envelope_id = analysis.instruments[instrument]
    envelope = analysis.envelopes[envelope_id]
    step = _step_ms(analysis, instrument)

    peak = max(envelope)
    peak_at = envelope.index(peak)
    final = envelope[-1]

    attack = _clamp(peak_at * step, *ATTACK_RANGE)
    decay = _clamp(max(len(envelope) - 1 - peak_at, 1) * step, *TIME_RANGE)

    fallen_db = DB_PER_STEP * (peak - final)
    if final <= 0:
        # The chip cut the note, so it lets go within a frame.
        return attack, decay, LEVEL_FLOOR, _clamp(step, *TIME_RANGE)

    sustain = _clamp(10 ** (-fallen_db / 20), LEVEL_FLOOR, 1.0)
    if fallen_db <= 0:
        return attack, decay, sustain, _clamp(DEFAULT_RELEASE_MS, *TIME_RANGE)
    # Carry the observed fall on down to the floor.
    release = (FLOOR_DB - fallen_db) * decay / fallen_db
    return attack, decay, sustain, _clamp(release, *TIME_RANGE)


def sample_paths(wav_path: str, library: str = "", sample_dir: str = ""):
    """The relative and absolute sample paths a preset records.

    `sample_dir` says where the wave files sit inside the Ableton library. Only
    the wave file's own name is appended to it, because the folder the dump
    happens to live in is not the folder Live will see. Without it the preset
    carries an absolute path only, since RelativePathType 6 is meaningless
    unless the sample really is under the library.
    """
    name = os.path.basename(wav_path)
    folder = sample_dir.strip().strip("/\\").replace("\\", "/")
    relative = posixpath.join(folder, name) if folder else ""
    if library and folder:
        absolute = os.path.join(os.path.abspath(library), *folder.split("/"), name)
    else:
        absolute = os.path.abspath(wav_path)
    return relative, absolute


def _same_time(first: float, second: float, tolerance: float) -> bool:
    return abs(first - second) <= max(FRAME_MS, tolerance * max(first, second))


def _same_level(first: float, second: float, db: float) -> bool:
    if first <= 0 or second <= 0:
        return first == second
    return abs(20 * math.log10(first / second)) <= db


def same_preset(first, second, tolerance: float = TOLERANCE) -> bool:
    """Whether two ADSR settings would make the same preset."""
    attack, decay, sustain, release = first
    other_attack, other_decay, other_sustain, other_release = second
    return (
        _same_time(attack, other_attack, tolerance)
        and _same_time(decay, other_decay, tolerance)
        and _same_time(release, other_release, tolerance)
        and _same_level(sustain, other_sustain, SUSTAIN_TOLERANCE_DB)
    )


def group_instruments(analysis: Analysis, tolerance: float = TOLERANCE,
                      min_notes: int = MIN_NOTES) -> dict:
    """Map every instrument onto the one whose preset covers it.

    An instrument leads a group only if it carries at least `min_notes` notes,
    so one-off guesses from the detector cannot spawn a preset of their own.
    They still join a group when one fits; otherwise they map to nothing.
    """
    uses = Counter(note.instrument for note in analysis.notes)
    settings = {i: adsr(analysis, i) for i in range(len(analysis.instruments))}
    order = sorted(settings, key=lambda i: (-uses[i], i))

    heads, mapping = [], {}
    for instrument in order:
        wave, _ = analysis.instruments[instrument]
        for head in heads:
            if analysis.instruments[head][0] != wave:
                continue
            if same_preset(settings[instrument], settings[head], tolerance):
                mapping[instrument] = head
                break
        else:
            if uses[instrument] >= min_notes:
                heads.append(instrument)
                mapping[instrument] = instrument
    return mapping


def _set(node, path: str, value) -> None:
    found = node.find(path)
    if found is None:
        raise KeyError(f"the template has no {path}")
    found.set("Value", f"{value:.10g}" if isinstance(value, float) else str(value))


def build(name: str, wav_path: str, values, relative_path: str = "",
          path: str = "", template: str = TEMPLATE) -> bytes:
    """One preset, as the bytes of an .adv file.

    `wav_path` is the file on disk, read for its size and date. `path` is what
    the preset records, which differs once the samples are copied elsewhere.
    """
    attack, decay, sustain, release = values
    with open(template, "rb") as handle:
        root = ET.fromstring(gzip.decompress(handle.read()))

    part = root.find(PART)
    _set(part, "Name", name)
    _set(part, "RootKey", ROOT_KEY)
    _set(part, "SampleStart", 0)
    _set(part, "SampleEnd", WAVE_LENGTH - 1)
    for loop in ("SustainLoop", "ReleaseLoop"):
        _set(part, f"{loop}/Start", 0)
        _set(part, f"{loop}/End", WAVE_LENGTH - 1)
        _set(part, f"{loop}/Mode", SUSTAIN_LOOP)

    _set(part, "SampleRef/FileRef/Path", path or os.path.abspath(wav_path))
    _set(part, "SampleRef/FileRef/RelativePath", relative_path)
    _set(part, "SampleRef/FileRef/OriginalFileSize", os.path.getsize(wav_path))
    _set(part, "SampleRef/FileRef/OriginalCrc", 0)  # zero means Live skips the check
    _set(part, "SampleRef/LastModDate", int(os.path.getmtime(wav_path)))
    _set(part, "SampleRef/DefaultDuration", WAVE_LENGTH)
    _set(part, "SampleRef/DefaultSampleRate", cycle_rate(CYCLE_HZ))

    envelope = root.find(ENVELOPE)
    _set(envelope, "AttackTime/Manual", attack)
    _set(envelope, "DecayTime/Manual", decay)
    _set(envelope, "SustainLevel/Manual", sustain)
    _set(envelope, "ReleaseTime/Manual", release)

    return gzip.compress(ET.tostring(root, encoding="UTF-8", xml_declaration=True))


def write_presets(vgm, analysis: Analysis, wave_dir: str, out_dir: str,
                  library: str = "", sample_dir: str = "",
                  tolerance: float = TOLERANCE, min_notes: int = MIN_NOTES) -> list:
    """One .adv per distinct sound. Returns what was written.

    Instruments whose attack, decay, sustain and release agree share a preset,
    named after the one that plays the most notes.
    """
    os.makedirs(out_dir, exist_ok=True)
    mapping = group_instruments(analysis, tolerance, min_notes)
    uses = Counter(note.instrument for note in analysis.notes)

    covered = {}
    for instrument, head in mapping.items():
        covered.setdefault(head, []).append(instrument)

    written = []
    for head in sorted(covered, key=lambda h: (-uses[h], h)):
        wave, envelope_id = analysis.instruments[head]
        wav = os.path.join(wave_dir, wave.strip() + WAV_SUFFIX)
        if not os.path.exists(wav):
            continue  # the note played a table that matched no complete upload
        name = analysis.instrument_names[head]
        relative, absolute = sample_paths(wav, library, sample_dir)
        values = adsr(analysis, head)
        preset = os.path.join(out_dir, f"{name}{SUFFIX}")
        with open(preset, "wb") as handle:
            handle.write(build(name, wav, values, relative, absolute))
        notes = sum(uses[i] for i in covered[head])
        written.append(
            (name, wave, analysis.envelope_names[envelope_id], values,
             len(covered[head]), notes)
        )
    return written
