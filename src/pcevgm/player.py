"""Replay of a VGM command stream into HuC6280 register state."""

from __future__ import annotations

from typing import Optional

from .huc6280 import DEFAULT_CLOCK, HuC6280State, describe_write
from .vgm import Command, VgmFile, describe_command

HUC6280_WRITE = 0xB9


class Timeline:
    """A cursor over the command stream plus the chip state at that point.

    Seeking backwards replays the stream from the start. Command streams are
    small, so this stays fast enough for interactive use.
    """

    def __init__(self, vgm: VgmFile) -> None:
        self.vgm = vgm
        self.commands = vgm.commands
        self.clock = vgm.header.huc6280_clock or DEFAULT_CLOCK
        self.state = HuC6280State(self.clock)
        self.index = 0  # next command to run
        self.sample = 0
        self.write_indices = [
            i for i, c in enumerate(self.commands) if c.opcode == HUC6280_WRITE
        ]
        last = self.commands[-1] if self.commands else None
        stream_end = last.sample + last.wait if last else 0
        self.end_sample = vgm.header.total_samples or stream_end

    @property
    def loop_sample(self) -> Optional[int]:
        if self.vgm.loop_index is None:
            return None
        return self.commands[self.vgm.loop_index].sample

    def reset(self) -> None:
        self.state = HuC6280State(self.clock)
        self.index = 0
        self.sample = 0

    def _apply(self, command: Command) -> None:
        if command.opcode == HUC6280_WRITE and len(command.operands) == 2:
            self.state.write(command.operands[0], command.operands[1])

    def seek_sample(self, sample: int) -> None:
        sample = max(0, min(int(sample), self.end_sample))
        if sample < self.sample:
            self.reset()
        while self.index < len(self.commands) and self.commands[self.index].sample <= sample:
            self._apply(self.commands[self.index])
            self.index += 1
        self.sample = sample

    def goto_index(self, index: int) -> None:
        index = max(0, min(index, len(self.commands)))
        if index < self.index:
            self.reset()
        while self.index < index:
            self._apply(self.commands[self.index])
            self.index += 1
        if index == 0:
            self.sample = 0
        else:
            previous = self.commands[index - 1]
            self.sample = previous.sample + previous.wait

    def step_command(self, count: int = 1) -> None:
        self.goto_index(self.index + count)

    def step_write(self, direction: int) -> None:
        """Move to the next or previous HuC6280 register write."""
        pool = self.write_indices
        if not pool:
            return
        if direction > 0:
            target = next((i for i in pool if i >= self.index), pool[-1])
            self.goto_index(target + 1)
        else:
            earlier = [i for i in pool if i < max(0, self.index - 1)]
            self.goto_index((earlier[-1] + 1) if earlier else 0)


def build_descriptions(vgm: VgmFile) -> list:
    """One text line per command, decoded with the chip state at that point."""
    state = HuC6280State(vgm.header.huc6280_clock or DEFAULT_CLOCK)
    lines = []
    for command in vgm.commands:
        if command.opcode == HUC6280_WRITE and len(command.operands) == 2:
            register, value = command.operands[0], command.operands[1]
            lines.append(describe_write(register, value, state.selected))
            state.write(register, value)
        else:
            lines.append(describe_command(command))
    return lines
