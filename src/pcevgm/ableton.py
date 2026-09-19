"""Ableton Live Simpler presets, a standard set of envelopes per wave table.

An .adv file is a single gzipped XML document. Rather than write that XML from
nothing, the generator patches a template exported from Live, so the schema is
one Live is known to accept. Only the sample reference, the volume envelope and
the names are replaced.

Every wave gets the same handful of envelopes. Deriving one envelope per
instrument gave hundreds of near-identical presets with numbers in their names;
a fixed set gives presets you can reach for. The values below sit on the
percentiles of the envelopes the sample rips actually play.
"""

from __future__ import annotations

import glob
import gzip
import os
import posixpath
from dataclasses import dataclass

from .huc6280 import WAVE_LENGTH
from .waves import CYCLE_HZ, LONG_WAV_SUFFIX, WAV_SUFFIX, cycle_rate

TEMPLATE = os.path.join(os.path.dirname(__file__), "data", "simpler.adv")
SUFFIX = ".adv"
FOLDER = "ableton"

PART = "OriginalSimpler/Player/MultiSampleMap/SampleParts/MultiSamplePart"
ENVELOPE = "OriginalSimpler/VolumeAndPan/Envelope"

# Simpler's own limits, read off the template's MidiControllerRange entries.
LEVEL_FLOOR = 0.0003162277571  # -70 dB, the quietest level Simpler holds
FLOOR_DB = 70.0
ATTACK_RANGE = (0.1, 20000.0)  # 0.1 ms is Simpler's way of spelling "none"
TIME_RANGE = (1.0, 60000.0)

SUSTAIN_LOOP = 1  # forward. Live's enum is not documented here; 0 is off.
ROOT_KEY = 60  # C4, which is what the single cycle wav is tuned to


@dataclass(frozen=True)
class Envelope:
    """One named shape, in the units Simpler stores: milliseconds and dB."""

    name: str
    attack: float
    decay: float
    sustain_db: float  # below the peak; -FLOOR_DB means it falls to silence
    release: float
    note: str


NONE = ATTACK_RANGE[0]
SILENT = -FLOOR_DB

STANDARD = (
    Envelope("hold", NONE, TIME_RANGE[1], 0.0, 50.0,
             "the raw oscillator, key down means tone"),
    Envelope("stab", NONE, 60.0, SILENT, 20.0,
             "near the tenth percentile decay, percussive"),
    Envelope("pluck", NONE, 180.0, SILENT, 30.0,
             "the median decay of the rips"),
    Envelope("decay", NONE, 800.0, SILENT, 50.0,
             "near the ninetieth percentile decay"),
    Envelope("tail", NONE, 200.0, -12.0, 2000.0,
             "drops fast, then rings out"),
    Envelope("swell", 300.0, 500.0, -6.0, 400.0,
             "the slow attacks, whose longest measured 440 ms"),
)
NAMES = tuple(envelope.name for envelope in STANDARD)


def values(envelope: Envelope):
    """The four numbers a preset stores, with the sustain as linear amplitude."""
    if envelope.sustain_db <= -FLOOR_DB:
        sustain = LEVEL_FLOOR
    else:
        sustain = min(1.0, 10 ** (envelope.sustain_db / 20))
    return envelope.attack, envelope.decay, sustain, envelope.release


def chosen(names=()) -> list:
    """The envelopes to write. An empty selection means all of them."""
    if not names:
        return list(STANDARD)
    wanted = [name.strip() for name in names if name.strip()]
    unknown = [name for name in wanted if name not in NAMES]
    if unknown:
        raise ValueError(f"no such envelope: {', '.join(unknown)}")
    return [envelope for envelope in STANDARD if envelope.name in wanted]


def wave_files(wave_dir: str) -> list:
    """The single cycle wave files in a dump, in order."""
    found = glob.glob(os.path.join(wave_dir, "wave-*" + WAV_SUFFIX))
    return sorted(path for path in found if not path.endswith(LONG_WAV_SUFFIX))


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


def preset_name(wav_path: str, envelope: Envelope) -> str:
    """What the preset is called, as `wave-01 pluck`."""
    return f"{os.path.basename(wav_path)[: -len(WAV_SUFFIX)]} {envelope.name}"


def _set(node, path: str, value) -> None:
    found = node.find(path)
    if found is None:
        raise KeyError(f"the template has no {path}")
    found.set("Value", f"{value:.10g}" if isinstance(value, float) else str(value))


def build(name: str, wav_path: str, settings, relative_path: str = "",
          path: str = "", template: str = TEMPLATE) -> bytes:
    """One preset, as the bytes of an .adv file.

    `wav_path` is the file on disk, read for its size and date. `path` is what
    the preset records, which differs once the samples are copied elsewhere.
    """
    import xml.etree.ElementTree as ET

    attack, decay, sustain, release = settings
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

    shape = root.find(ENVELOPE)
    _set(shape, "AttackTime/Manual", attack)
    _set(shape, "DecayTime/Manual", decay)
    _set(shape, "SustainLevel/Manual", sustain)
    _set(shape, "ReleaseTime/Manual", release)

    return gzip.compress(ET.tostring(root, encoding="UTF-8", xml_declaration=True))


def write_presets(wave_dir: str, out_dir: str, library: str = "",
                  sample_dir: str = "", names=()) -> list:
    """Every chosen envelope on every wave in a dump. Returns what was written."""
    envelopes = chosen(names)
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for wav in wave_files(wave_dir):
        relative, absolute = sample_paths(wav, library, sample_dir)
        for envelope in envelopes:
            name = preset_name(wav, envelope)
            settings = values(envelope)
            with open(os.path.join(out_dir, name + SUFFIX), "wb") as handle:
                handle.write(build(name, wav, settings, relative, absolute))
            written.append((name, envelope, settings))
    return written
