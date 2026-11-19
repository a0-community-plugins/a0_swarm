from __future__ import annotations

import importlib
from pathlib import Path
import shutil
import sys


PLUGIN_PACKAGE_PREFIX = "usr.plugins.a0_swarm"
PLUGIN_ROOT = Path(__file__).resolve().parent


def install() -> None:
    """Finish install/update cleanup without a separate Execute step."""
    _stop_workers()
    _sweep_owned_workspaces()
    _clear_plugin_modules()
    _clear_plugin_bytecode()


def pre_update() -> None:
    _stop_workers()
    _sweep_owned_workspaces()
    _clear_plugin_modules()
    _clear_plugin_bytecode()


def uninstall() -> None:
    _stop_workers()
    _sweep_owned_workspaces()
    _clear_plugin_modules()
    _clear_plugin_bytecode()


def _stop_workers() -> None:
    from usr.plugins.a0_swarm.helpers.execution import Coordinator
    from usr.plugins.a0_swarm.helpers.registry import SwarmRegistry
    owner = getattr(sys, "_a0_swarm_coordinator", None) or Coordinator._instance
    if owner is not None:
        owner.close()
        Coordinator._instance = None
    else:
        registry = getattr(sys, "_a0_swarm_registry", None) or SwarmRegistry._instance
        if registry is not None and hasattr(registry, "store"):
            registry.store.close()
    vars(sys).pop("_a0_swarm_coordinator", None)
    vars(sys).pop("_a0_swarm_registry", None)
    SwarmRegistry._instance = None


def _sweep_owned_workspaces() -> None:
    # Durable custody must survive update and uninstall, including dirty files.
    return


def _clear_plugin_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == PLUGIN_PACKAGE_PREFIX or module_name.startswith(
            f"{PLUGIN_PACKAGE_PREFIX}."
        ):
            sys.modules.pop(module_name, None)
    importlib.invalidate_caches()


def _clear_plugin_bytecode() -> None:
    for cache_dir in PLUGIN_ROOT.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
