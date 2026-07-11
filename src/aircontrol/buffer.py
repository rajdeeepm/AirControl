from __future__ import annotations

from collections import deque

from aircontrol.trajectory import LandmarkFrame, Trajectory


class RollingFrameBuffer:
    def __init__(self, capacity: int) -> None:
        self._frames: deque[LandmarkFrame] = deque(maxlen=capacity)

    def append(self, frame: LandmarkFrame) -> None:
        self._frames.append(frame)

    def __len__(self) -> int:
        return len(self._frames)

    def last(self, n: int) -> Trajectory:
        frames = tuple(self._frames)
        selected = frames[-n:] if n > 0 else ()
        return Trajectory(frames=selected, handedness=self._handedness())

    def since(self, timestamp: float) -> Trajectory:
        frames = tuple(frame for frame in self._frames if frame.timestamp >= timestamp)
        return Trajectory(frames=frames, handedness=self._handedness())

    def clear(self) -> None:
        self._frames.clear()

    def _handedness(self) -> str:
        if not self._frames:
            return "Unknown"
        return self._frames[-1].handedness
