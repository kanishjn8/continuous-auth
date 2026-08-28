"""Deterministic, content-free synthetic fixtures for the whole pipeline."""

from tools.synthetic.config import SyntheticConfig, load_synthetic_config
from tools.synthetic.generator import ScenarioBundle, generate_scenario

__all__ = [
    "ScenarioBundle",
    "SyntheticConfig",
    "generate_scenario",
    "load_synthetic_config",
]
