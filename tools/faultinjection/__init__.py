"""Synthetic fault frames and the complete PLAN §13.6 execution matrix."""

from .frames import duplicate_frame, malformed_frame, out_of_order_frame, truncated_frame
from .matrix import FaultCase, FaultResult, load_fault_matrix, write_report

__all__ = [
    "FaultCase",
    "FaultResult",
    "duplicate_frame",
    "load_fault_matrix",
    "malformed_frame",
    "out_of_order_frame",
    "truncated_frame",
    "write_report",
]
