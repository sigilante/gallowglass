"""
Pin store — local directory of seed files indexed by PinId.

Provides save/resolve for content-addressed PLAN values:

    store = PinStore('/path/to/pins')
    store.save(plan_value)          # writes {pin_id}.seed
    val = store.resolve(pin_id)     # loads and caches

Real lazy loading happens in planvm; this is the Python harness
equivalent for testing the pin-store mechanism.
"""

from __future__ import annotations

import os
from typing import Any

from bootstrap.pin import compute_pin_id
from dev.harness.seed import save_seed, load_seed


class PinStoreError(Exception):
    """Raised when a pin cannot be resolved."""


class PinStore:
    """Directory-backed pin store with in-memory cache."""

    def __init__(self, store_dir: str):
        self.store_dir = store_dir
        self._cache: dict[str, Any] = {}

    def save(self, plan_value) -> str:
        """Save a PLAN value to the store. Returns its PinId."""
        pin_id = compute_pin_id(plan_value)
        path = os.path.join(self.store_dir, f'{pin_id}.seed')
        if not os.path.exists(path):
            os.makedirs(self.store_dir, exist_ok=True)
            with open(path, 'wb') as f:
                f.write(save_seed(plan_value))
        self._cache[pin_id] = plan_value
        return pin_id

    def resolve(self, pin_id: str) -> Any:
        """Resolve a PinId to its PLAN value. Raises PinStoreError if missing."""
        if pin_id in self._cache:
            return self._cache[pin_id]
        path = os.path.join(self.store_dir, f'{pin_id}.seed')
        if not os.path.exists(path):
            raise PinStoreError(
                f"pin {pin_id[:16]}... not found in {self.store_dir}"
            )
        with open(path, 'rb') as f:
            data = f.read()
        val = load_seed(data)
        self._cache[pin_id] = val
        return val

    def has(self, pin_id: str) -> bool:
        """Check if a pin exists in the store."""
        if pin_id in self._cache:
            return True
        path = os.path.join(self.store_dir, f'{pin_id}.seed')
        return os.path.exists(path)
