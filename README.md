# pcevgm

A terminal debugger for PC Engine (HuC6280) VGM music files.

It parses a `.vgm` or `.vgz` file, replays the command stream into HuC6280
register state, and shows that state as you move through the song. It does not
produce audio.

## Requirements

Python 3.10 or later and pip 21.3 or newer. No third-party packages. The user
interface uses the `curses` module from the standard library.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Upgrading pip is not optional. This project carries only a `pyproject.toml`,
and installing one of those in editable mode needs pip 21.3 or newer. An older
pip fails with:

```
ERROR: File "setup.py" or "setup.cfg" not found.
Directory cannot be installed in editable mode
```

If you would rather not install at all, nothing here needs it:

```sh
PYTHONPATH=src python3 -m pcevgm song.vgz
```

## Run

```sh
python tools/make_example.py      # writes examples/scale.vgm
pcevgm examples/scale.vgm
```

Text modes:

```sh
pcevgm file.vgm --info             # header, clocks, GD3 tags
pcevgm file.vgm --dump 200         # first 200 decoded commands
pcevgm file.vgm --extract-waves    # write the wave tables to file.vgm.wavs/
pcevgm file.vgm --notes 100        # instrument table, then the first 100 notes
pcevgm file.vgm --tracker          # tracker grid to file.vgm.tracker.txt
pcevgm file.vgm --ableton          # Simpler presets, an envelope set per wave
```

## Tracker grid

`--tracker` writes the whole grid to a text file, defaulting to the input path
plus `.tracker.txt`. The dump adds a time column, and always
names the wave, which the on-screen panel drops on a narrow terminal.

A row is one driver tick. The driver writes once per video frame and spreads
one tick's writes across a few sample times, so a tick is a run of write
instants closer together than half a frame. Measured on the sample rips, 74% of
gaps between instants are 1 or 2 samples and the rest sit at 733 to 738.

Channels 4 and 5 are the only two that support noise. They get their own `n4`
and `n5` columns, so a noise hit is visible while the channel's tone column
stays empty.

The amplitude and noise columns are read from the chip. The note and wave
columns come from the detector below, so they carry its guesswork.

## Ableton Live presets

`--ableton` writes Simpler presets into an `ableton` folder inside the wave
dump: a standard set of envelopes on every wave table. Run `--extract-waves`
first, since each preset points at a `wave-NN.wav`.

```sh
pcevgm song.vgz --extract-waves
pcevgm song.vgz --ableton \
  --library ~/Music/Ableton/User\ Library \
  --sample-dir "Samples/PCE/Marine"
```

Copy the wave files into that folder under your library, then the presets
resolve without Live asking you to locate anything.

An `.adv` file is one gzipped XML document. Rather than write that XML from
nothing, the generator patches a template exported from Live 12.4.5, so the
schema is one Live is known to accept. Only the sample reference, the envelope
and the names change.

The single-cycle wave files suit Simpler directly: they are tuned to C4, which
is `RootKey 60`, so no detune is needed. The preset loops the whole 32 frames,
because a single cycle played once lasts 3.8 ms.

Every wave gets the same handful of envelopes, named so you can reach for one:

| name | attack | decay | sustain | release | what it is |
| --- | --- | --- | --- | --- | --- |
| `hold` | 0.1 | none | full | 50 | the raw oscillator, key down means tone |
| `stab` | 0.1 | 300 | silence | 20 | percussive, cut off the moment the key lifts |
| `pluck` | 0.1 | 300 | silence | 30 | percussive, with a touch of release |
| `decay` | 0.1 | 5000 | silence | 50 | a held piano key, ringing out |
| `tail` | 0.1 | 200 | -12 dB | 2000 | drops fast, then rings out |
| `swell` | 300 | 500 | -6 dB | 400 | the slow attacks, whose longest measured 440 ms |

Times are milliseconds. For reference, the envelopes the sample rips actually
play, measured over the 803 instruments that sound more than once: decay runs
from 16 ms at the tenth percentile to 837 at the ninetieth and 4720 at the
longest, release from 22 to 1680, and 27% fall to silence while 73% settle and
hold. `decay` deliberately rings out past the longest of them.

`stab` and `pluck` currently differ only by 10 ms of release, which is below
hearing. Keep both if you want the file names, or pass `--envelopes` to skip
one.

`0.1` is not a rounded zero. Simpler's attack range starts there, which is what
Live writes with the knob fully down, and one wave cycle at C4 lasts 3.82 ms, so
an attack of 0.1 ms finishes inside 3% of a single cycle.

A preset is named for what it is, `wave-01 pluck.adv`, so the browser sorts
every envelope of one wave together. `--envelopes hold,pluck,tail` writes a
subset. The count is exactly waves times envelopes, which for a track with
seven waves and all six envelopes is 42 presets.

### Where the samples live

A preset records the sample twice. `RelativePath` is read against your User
Library root, which is what `RelativePathType 6` means, and `Path` is the
absolute fallback.

- `--sample-dir REL` is the folder inside the library that will hold the wave
  files. A preset appends only the wave file name to it, never the folder the
  dump happens to sit in.
- `--library PATH` is the library root. With `--sample-dir` it fixes the
  absolute path a preset falls back on.

Give neither and a preset carries the wave file's own absolute path with no
relative path, since `RelativePathType 6` means nothing unless the sample
really is under the library. Live then resolves that path or asks you once.

Two things are assumed rather than known, both worth checking on first open:
the loop mode enum is set to 1 for a forward loop, and `OriginalCrc` is written
as 0 to skip Live's sample checksum.

## Note, envelope and instrument analysis

This is preliminary. Read the numbers as estimates.

The driver family in the sample rips rarely gates a note. It leaves a channel
enabled and rewrites pitch and amplitude every video frame, so a note start has
to be inferred. The detector groups writes into instants, meaning every write
that shares one sample time, and starts a note when any of these holds:

- the channel gates on while its amplitude is above zero
- the pitch reloads by more than a semitone
- the amplitude jumps up by four steps, about 6 dB

It debounces to one frame, and ends a note at amplitude zero, at a gate off, at
a switch to noise or DDA, or at the next onset.

An envelope is the sequence of amplitudes the driver wrote during the note. A
note cut short gives a prefix of a longer envelope, so prefixes merge into the
longest envelope that contains them. An instrument is a wave table paired with
an envelope, numbered `inst-00` like the wave files. The TUI names the
instrument playing on each channel on the channel's second row.

There is no reference note list for these files, so the accuracy of the
detector is untested. Only its output is checked.

## Wave extraction

`--extract-waves` replays the stream and keeps every wave table the track
uploads. A table is 32 samples of 5 bits. The extractor captures one each time
a channel finishes a full pass of 32 writes to the wave data register. Identical
tables collapse into one entry.

Output goes to the input path plus `.wavs`, so `song.vgz` gives `song.vgz.wavs/`.
Use `--out DIR` to write somewhere else.

```
song.vgz.wavs/
  manifest.txt       where each wave is uploaded, and an ASCII plot
  wave-00.pcm        32 raw bytes, one per sample, values 0 to 31
  wave-00.hex        the same bytes as text, 16 to a line
  wave-00.wav        one cycle, uncompressed 16-bit mono, pitched at C4
  wave-00.long.wav   the same cycle repeated, to preview by ear
  wave-01.pcm
  ...
```

`wave-NN.wav` holds a single cycle. Its sample rate sets its pitch: 32 samples
at 8372 Hz is C4, so it loads into a sampler already in tune. The samples
themselves are untouched, and `--wave-hz` moves the pitch.

`wave-NN.long.wav` repeats that cycle at 440 Hz for 2 seconds at 44100 Hz, so
any audio player gives you the timbre. `--preview-hz` and `--preview-seconds`
change it.

Both files scale the 5-bit samples around their 15.5 midpoint, so a flat table
is silence and the range 0 to 31 fills 16-bit full scale. The preview picks the
nearest wave sample for each output frame and does not interpolate, because the
chip holds each sample for a fixed time. It fades 5 ms at each end.

The command never deletes files. It names any leftover `wave-*` file from an
earlier run so you can remove them yourself.

## Screen

- Header: file name, VGM version, chip clocks, GD3 tags, transport, progress.
- Keyboard: five octaves, C1 to B5, showing what sounds right now. Each layer
  of keys is two character rows tall. A white key is two cells wide on the
  lower layer, so it holds four cells. A black key is one cell wide on the
  upper layer, over the right half of the white key below it, so it holds two.
  An active key changes background colour, one colour per channel, so the key
  keeps its shape. The digit names the channel.
  - The cells of a key are shared out between the channels sounding it. One
    channel fills the key. Two split it in half, one above the other. Four take
    a cell each. Beyond that the last cell shows `+`.
  - A pitch off the ends of the board marks the edge with `<` or `>`.
  - A channel with noise enabled has no pitch, so the board leaves it out
    entirely. The channel table reports its noise register. A channel in
    DDA mode is named beside the board instead.
  - Press `k` to hide it. It hides itself below 29 terminal rows rather than
    push a channel off the table.
- Channel table: two rows per PSG channel.
  - First row: `ST` (`ON`, `DDA` or `off`), `DIV` (the 12-bit frequency
    divider), `HZ` and `NOTE` (the divider as a pitch, with cents offset),
    `dB L` and `dB R` (attenuation per side), `AMP` and `BAL` (the raw
    amplitude and balance registers).
  - Second row: the instrument the analysis found, then the wave table and the
    envelope that instrument is made of. The wave name comes from the chip, so
    a table re-uploaded part way through a note still shows. Then a horizontal
    meter under each of `dB L`, `dB R` and `AMP`. The level meters read empty
    at -60 dB and full at 0 dB. The `AMP` meter tracks the raw 0 to 31
    register. Partial blocks give each meter character 8 steps.
  - Third row: the DDA sample while in DDA mode, and the noise register on
    channels 4 and 5.
  - `WAVE` — the 32-entry wave table, drawn as a braille line three character
    rows tall. One character covers two samples and four dot rows, so three
    rows resolve twelve levels in sixteen columns. The dots between two
    neighbouring samples are lit too, so the line stays joined.
  - `ENVELOPE` — the envelope of the instrument on the channel, one column per
    two amplitude steps, drawn the same way. The step the note has reached
    shows in reverse video. A finished envelope holds, with its cursor resting
    on the last amplitude written, until a new note replaces it; the column is
    blank only before a channel's first note. The field stops at 12 columns,
    24 steps, which holds 85% of notes whole; a longer envelope ends in `>`.
- Master line: the master balance, the selected channel and the write count.
- LFO line: register 9 split into its two fields. `enabled` is bit 7, which
  disables the LFO and resets its source channel when set. `depth` is bits 1
  and 0, which scale the source channel output by 0, 1, 16 or 256 before it
  reaches the pitch. The raw register byte stays on the line. The line dims
  unless the LFO is enabled at a non-zero depth.
- Log: the command stream around the cursor. The current command is marked `>`.
- Tracker: on the right, one row per driver tick, one column group per channel,
  then `n4` and `n5` for the two channels that support noise. `...` means
  sounding with no new note. A channel that is off, switched to noise, or at
  zero amplitude leaves its cell empty until it sounds again. The view scrolls
  with playback and marks the current row. Press `t` to hide it.
  - A cell reads note, amplitude in hex, then the wave number, the one the
    `wave-NN` files carry, at 174 terminal columns or wider. Below that it
    drops the wave number, leaving note and amplitude in the same places.
    Below 156 columns the panel hides itself.
  - A noise cell reads the noise frequency and the amplitude, both in hex. The
    frequency shows on the row the hit starts and on any row it changes.
  - A channel takes the colour of the wave table it is playing, so notes on
    one wave read as a group down the panel. Within that colour a field
    fades with its own amplitude, so an envelope still shows as a fade down
    the column. A channel with no wave, and the noise columns, fade in grey.
  - The row where a note or a noise hit starts is emboldened, along with its
    row number. The `...` and `..` placeholders always take one grey a shade
    below the bottom of the fade: visible, but under every value on the row.
  - All of the colour needs a 256 colour terminal. On fewer colours every
    sounding field falls back to one dim shade.

The wave name on a channel's second row is the file stem `--extract-waves`
writes, so `wave-03` on channel 2 means that channel holds the table in
`wave-03.pcm`. Run the dump once and you can hear any channel's timbre through
`wave-03.long.wav`. A channel reads `wave  --` until its table matches a
complete upload, which includes the start of every track.

## Keys

| Key | Action |
| --- | --- |
| `space` | play / pause |
| `left` `right` | step one video frame (735 samples) |
| `<` `>` | step one second |
| `,` `.` | step one command |
| `p` `n` | step one HuC6280 register write |
| `g` `G` | go to start / end |
| `l` | go to the loop point |
| `[` `]` | slower / faster |
| `k` | show or hide the keyboard |
| `t` | show or hide the tracker |
| `h` | log filter: all commands or HuC6280 writes only |
| `?` | show or hide the key list |
| `q` | quit |

## Tests

```sh
python -m unittest discover -s tests -t .
```

## Layout

| Path | Contents |
| --- | --- |
| `src/pcevgm/vgm.py` | file, header, GD3 and command stream parsing |
| `src/pcevgm/huc6280.py` | PSG register model and pitch / level maths |
| `src/pcevgm/player.py` | cursor over the stream, state replay, command text |
| `src/pcevgm/waves.py` | wave table extraction and the `.pcm` / `.hex` output |
| `src/pcevgm/notes.py` | note detection, envelopes and instruments |
| `src/pcevgm/keyboard.py` | the piano keyboard view of the current pitches |
| `src/pcevgm/tracker.py` | the tracker grid and its text dump |
| `src/pcevgm/ableton.py` | Simpler preset export, with the template in `data/` |
| `src/pcevgm/tui.py` | curses screen and key handling |
| `src/pcevgm/cli.py` | argument parsing and the text modes |
| `tools/make_example.py` | builds a synthetic test file |

## Known limits

- No audio output.
- Seeking backwards replays the stream from the start.
- The volume figure uses 1.5 dB per amplitude step and 3.0 dB per balance step.
  It is a model of the hardware curve, not a measurement.
- Noise frequency shows the raw 5-bit register, not a rate in Hz.
- The LFO registers are stored and shown but not applied to channel pitch.
- The keyboard shows instantaneous pitch with no smoothing, so vibrato makes a
  key marker jump between neighbours. It rounds to the nearest semitone; the
  `NOTE` column carries the cents offset.
- A channel counts as sounding on the keyboard above -40 dB. At -60 dB the
  board fills with channels parked on divider 0 at 27 Hz.
- The wave and envelope plots use braille, `U+2800` to `U+28FF`. A font
  without braille glyphs will show boxes. Braille also halves the
  horizontal resolution: two samples share one character column.
- A channel takes three rows, so the table is 22 rows. The keyboard needs
  another six and hides itself below 35 terminal rows.
- The keyboard needs about 74 columns. The envelope plot starts at column
  82, so about 115 columns shows a long envelope whole.
- Note detection is a heuristic tuned on one game's sound driver. A driver
  that gates every note would need no heuristic; one that uses the hardware
  LFO for vibrato would defeat the pitch test.
- The TUI replays the stream three times at load: the command text, the
  analysis and the tracker grid. The largest rip here takes about 0.3 s.
- Commands for other chips are decoded for length and time only.
- Wave extraction ignores a partial pass. A track that rewrites only part of
  a table produces no new entry until the next full pass.
