"""Operational health checks for Commander Gym storage placement.

This module validates the physical storage layer owned by issue #74. It does not
interpret run evidence, datasets, models, Deck/Pilot/Binding identity, or other
higher-level Commander Gym semantics.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shutil
import tempfile
from typing import Iterable, Mapping, Sequence

from commander_gym.storage import STORAGE_ROUTES, StorageError, StorageLayout


@dataclass(frozen=True)
class RouteHealth:
    route: str
    path: str
    exists: bool
    writable: bool
    free_bytes: int | None
    total_bytes: int | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exists and self.writable and self.error is None


@dataclass(frozen=True)
class StorageHealth:
    routes: tuple[RouteHealth, ...]
    min_free_bytes: int

    @property
    def ok(self) -> bool:
        return all(
            route.ok
            and route.free_bytes is not None
            and route.free_bytes >= self.min_free_bytes
            for route in self.routes
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "min_free_bytes": self.min_free_bytes,
            "routes": [
                {
                    **asdict(route),
                    "ok": route.ok
                    and route.free_bytes is not None
                    and route.free_bytes >= self.min_free_bytes,
                }
                for route in self.routes
            ],
        }


def _validate_min_free_bytes(value: int) -> int:
    if type(value) is not int or value < 0:
        raise StorageError("min_free_bytes must be a non-negative integer")
    return value


def _probe_route(route: str, path: Path, *, create: bool) -> RouteHealth:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        exists = path.is_dir()
        if not exists:
            return RouteHealth(
                route=route,
                path=str(path),
                exists=False,
                writable=False,
                free_bytes=None,
                total_bytes=None,
                error="directory does not exist",
            )

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".commander-gym-doctor-",
                dir=path,
                delete=True,
            ) as handle:
                handle.write(b"storage-doctor\n")
                handle.flush()
            writable = True
        except OSError as exc:
            return RouteHealth(
                route=route,
                path=str(path),
                exists=True,
                writable=False,
                free_bytes=None,
                total_bytes=None,
                error=f"write probe failed: {exc}",
            )

        usage = shutil.disk_usage(path)
        return RouteHealth(
            route=route,
            path=str(path),
            exists=True,
            writable=writable,
            free_bytes=usage.free,
            total_bytes=usage.total,
        )
    except OSError as exc:
        return RouteHealth(
            route=route,
            path=str(path),
            exists=path.exists(),
            writable=False,
            free_bytes=None,
            total_bytes=None,
            error=str(exc),
        )


def check_storage(
    layout: StorageLayout,
    *,
    min_free_bytes: int = 0,
    create: bool = False,
) -> StorageHealth:
    """Check route existence, actual writability, and free capacity.

    ``create=True`` initializes missing configured directories before probing.
    No durable artifact is retained by the write test.
    """

    if not isinstance(layout, StorageLayout):
        raise StorageError("layout must be a StorageLayout")
    threshold = _validate_min_free_bytes(min_free_bytes)
    routes = tuple(
        _probe_route(route, layout.path(route), create=create)
        for route in STORAGE_ROUTES
    )
    return StorageHealth(routes=routes, min_free_bytes=threshold)


def _parse_route_overrides(values: Iterable[str]) -> Mapping[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise StorageError("route overrides must use NAME=PATH")
        name, path = value.split("=", 1)
        if not name or not path:
            raise StorageError("route overrides must use non-empty NAME=PATH")
        if name in result:
            raise StorageError(f"duplicate storage route override: {name}")
        result[name] = path
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Commander Gym storage writability and capacity."
    )
    parser.add_argument("--root", required=True, help="Commander Gym storage root")
    parser.add_argument(
        "--route",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="override a storage route; may be supplied multiple times",
    )
    parser.add_argument(
        "--min-free-bytes",
        type=int,
        default=0,
        help="require at least this many free bytes on every configured route",
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help="create missing configured route directories before checking them",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        overrides = _parse_route_overrides(args.route)
        layout = StorageLayout.create(args.root, overrides=overrides)
        health = check_storage(
            layout,
            min_free_bytes=args.min_free_bytes,
            create=args.create,
        )
    except StorageError as exc:
        parser.error(str(exc))

    print(json.dumps(health.to_dict(), indent=2, sort_keys=True))
    return 0 if health.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
