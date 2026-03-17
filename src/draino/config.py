from __future__ import annotations

from pathlib import Path

import yaml

from .models import MaintenanceConfig


def load_config(path: str | None) -> MaintenanceConfig:
    if not path:
        return MaintenanceConfig()
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text()) or {}
    return MaintenanceConfig.model_validate(data)
