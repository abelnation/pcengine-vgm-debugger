"""Command line entry point."""

from __future__ import annotations

import argparse
import sys

from . import notes as notes_module
from . import waves as waves_module
from .player import HUC6280_WRITE, build_descriptions
from .vgm import SAMPLE_RATE, VgmError, load


def _time_text(sample: int) -> str:
    seconds = sample / SAMPLE_RATE
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:05.2f}"


def _print_info(vgm) -> None:
    header = vgm.header
    print(f"file           {vgm.path}")
    print(f"version        {header.version_text}")
    print(f"data offset    0x{header.data_offset:X}")
    print(f"total samples  {header.total_samples} ({header.total_samples / SAMPLE_RATE:.2f} s)")
    if header.loop_offset:
        print(f"loop offset    0x{header.loop_offset:X} ({header.loop_samples} samples)")
    else:
        print("loop offset    none")
    print(f"commands       {len(vgm.commands)}")
    for name, clock in header.clocks.items():
        print(f"clock          {name} {clock} Hz")
    if not header.huc6280_clock:
        print("clock          HuC6280 absent: this file is probably not PC Engine")
    if vgm.gd3:
        for name in ("game", "track", "author", "system", "date", "notes"):
            value = getattr(vgm.gd3, name)
            if value:
                print(f"gd3 {name:<10} {value}")
    for warning in vgm.warnings:
        print(f"warning        {warning}")


def _print_dump(vgm, count: int) -> None:
    descriptions = build_descriptions(vgm)
    for index, command in enumerate(vgm.commands[:count]):
        raw = " ".join(f"{byte:02X}" for byte in command.operands[:8])
        print(
            f"{index:6d}  0x{command.offset:06X}  {command.sample:9d}  "
            f"{command.opcode:02X} {raw:<24}  {descriptions[index]}"
        )


def _extract_waves(vgm, args) -> int:
    waves = waves_module.extract(vgm)
    if not waves:
        print("pcevgm: the track uploads no complete wave table", file=sys.stderr)
        return 1
    report = waves_module.write_files(
        vgm,
        waves,
        args.out or waves_module.folder_for(vgm.path),
        args.preview_hz,
        args.preview_seconds,
    )
    uploads = sum(len(wave.uploads) for wave in waves)
    suffixes = " ".join(waves_module.SUFFIXES)
    print(f"{len(waves)} waves from {uploads} uploads -> {report['directory']}")
    print(f"each wave as [{suffixes}]")
    for wave, stem in zip(waves, report["stems"]):
        channels = ", ".join(str(c) for c in wave.channels)
        print(f"  {stem}  {len(wave.uploads):4d} uploads  ch {channels}")
    for name in report["stale"]:
        print(f"pcevgm: left over from an earlier run: {name}", file=sys.stderr)
    return 0


def _print_notes(vgm, count: int) -> int:
    from collections import Counter

    analysis = notes_module.analyse(vgm)
    if not analysis.notes:
        print("pcevgm: the detector found no notes", file=sys.stderr)
        return 1
    use = Counter(note.instrument for note in analysis.notes)
    print(f"notes        {len(analysis.notes)}")
    print(f"envelopes    {len(analysis.envelopes)}")
    print(f"instruments  {len(analysis.instruments)}")
    print()
    print("instrument   wave      envelope  notes  channels  amplitude steps")
    for instrument, uses in use.most_common():
        wave, envelope_id = analysis.instruments[instrument]
        shape = analysis.envelopes[envelope_id]
        body = " ".join(str(value) for value in shape[:12])
        if len(shape) > 12:
            body += " .."
        channels = ",".join(
            str(c) for c in sorted({n.channel for n in analysis.notes
                                    if n.instrument == instrument})
        )
        print(
            f"{analysis.instrument_names[instrument]:<12} {wave:<9} "
            f"{analysis.envelope_names[envelope_id]:<9} {uses:5d}  {channels:<8}  {body}"
        )
    print()
    print("ch  time      note      frames  instrument  wave      divider")
    for note in analysis.notes[:count]:
        wave, _ = analysis.instruments[note.instrument]
        print(
            f"{note.channel:2d}  {_time_text(note.start)}  "
            f"{analysis.pitch_text(note):<8}  {note.length / notes_module.FRAME:6.1f}  "
            f"{analysis.instrument_names[note.instrument]:<10}  {wave:<9} "
            f"0x{note.divider:03X}"
        )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pcevgm",
        description="Terminal debugger for PC Engine (HuC6280) VGM music files.",
    )
    parser.add_argument("file", help="a .vgm or .vgz file")
    parser.add_argument("--info", action="store_true", help="print the header, then exit")
    parser.add_argument(
        "--dump",
        type=int,
        nargs="?",
        const=64,
        metavar="N",
        help="print the first N commands, then exit (default 64)",
    )
    parser.add_argument(
        "--notes",
        type=int,
        nargs="?",
        const=64,
        metavar="N",
        help="print the instrument table and the first N notes, then exit (default 64)",
    )
    parser.add_argument(
        "--extract-waves",
        action="store_true",
        help="write each wave table the track uploads as raw bytes, then exit",
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        help="where --extract-waves writes (default: the input path plus .wavs)",
    )
    parser.add_argument(
        "--preview-hz",
        type=float,
        default=waves_module.PREVIEW_HZ,
        metavar="HZ",
        help=f"pitch of the .long.wav preview (default {waves_module.PREVIEW_HZ:g})",
    )
    parser.add_argument(
        "--preview-seconds",
        type=float,
        default=waves_module.PREVIEW_SECONDS,
        metavar="S",
        help=f"length of the .long.wav preview (default {waves_module.PREVIEW_SECONDS:g})",
    )
    args = parser.parse_args(argv)

    try:
        vgm = load(args.file)
    except (OSError, VgmError) as error:
        print(f"pcevgm: {error}", file=sys.stderr)
        return 1

    if args.info:
        _print_info(vgm)
        return 0
    if args.dump is not None:
        _print_dump(vgm, args.dump)
        return 0
    if args.notes is not None:
        return _print_notes(vgm, args.notes)
    if args.extract_waves:
        return _extract_waves(vgm, args)

    if not vgm.header.huc6280_clock:
        print("pcevgm: no HuC6280 clock in the header; the chip view will stay empty",
              file=sys.stderr)
    writes = sum(1 for c in vgm.commands if c.opcode == HUC6280_WRITE)
    if not writes:
        print("pcevgm: the stream holds no HuC6280 writes", file=sys.stderr)

    from .tui import run

    run(vgm)
    return 0
