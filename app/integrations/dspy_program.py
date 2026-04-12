"""DSPy integration points.

This module intentionally starts as an interface layer so the repository can be used
before DSPy is installed. Once DSPy is available, implement concrete modules for:

- route classification
- NodeSet selection
- evidence budget selection
- synthesis prompt optimization
"""

from dataclasses import dataclass


@dataclass
class DspyOptimizationCandidate:
    name: str
    description: str
