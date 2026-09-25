"""Dataset generation, labelling, training and evaluation.

Tooling, not runtime. Nothing under ``perception`` or ``reasoning`` may import
from here; this package imports *them*, because evaluating a detector honestly
means replaying its output through the real engine and scoring the verdicts it
actually produced.
"""
