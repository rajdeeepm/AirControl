# tests/test_buffer.py
from __future__ import annotations
from aircontrol.domain import Point3D
from aircontrol.trajectory import LandmarkFrame
from aircontrol.buffer import RollingFrameBuffer

def _frame(t):
    return LandmarkFrame(landmarks=tuple(Point3D(0, 0, 0) for _ in range(21)), handedness="Right", timestamp=t)

def test_capacity_evicts_oldest():
    buf = RollingFrameBuffer(capacity=3)
    for i in range(5):
        buf.append(_frame(float(i)))
    assert len(buf) == 3
    assert [f.timestamp for f in buf.last(3).frames] == [2.0, 3.0, 4.0]

def test_since_filters_by_timestamp():
    buf = RollingFrameBuffer(capacity=10)
    for i in range(5):
        buf.append(_frame(float(i)))
    assert [f.timestamp for f in buf.since(2.0).frames] == [2.0, 3.0, 4.0]

def test_empty_returns_unknown_trajectory():
    buf = RollingFrameBuffer(capacity=3)
    traj = buf.last(3)
    assert traj.frames == () and traj.handedness == "Unknown"
