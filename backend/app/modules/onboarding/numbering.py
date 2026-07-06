"""Pure house-number generation engine (docs/modules/onboarding.md §4).

No DB, no I/O — deterministic functions over typed inputs → generated numbers.
Isolating this here makes the highest-risk logic unit-testable in isolation and
keeps the service thin. The service maps these outputs onto ``House`` rows.

BUILDING numbering (per building, floors ordered lowest→highest = ground first):
  - AUTO:        number = prefix + zeropad(seq, pad); seq restarts per floor.
                 prefix = ground_prefix if is_ground else str(level).
  - SEQUENTIAL:  one running counter from the lowest floor up. ``sequential_scope``
                 = 'per_building' (reset each tower) or 'continuous' (carry a
                 ``start_at`` seed across towers — the service supplies it).
  - MANUAL:      the admin typed every number (engine just validates/echoes).

INDIVIDUAL numbering:
  - SEQUENTIAL:  one continuous 1,2,3… across all rows from row 1 (service seeds
                 ``start_at`` per row so the sequence carries).
  - CUSTOM:      per-row ``prefix`` + count from 1 each row, no pad by default
                 ('alpha' → alpha1.., '10' → 101..110).
  - MANUAL:      admin typed every number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- Config defaults (docs §3/§4) ------------------------------------------
DEFAULT_COUNT_PAD = 2
DEFAULT_GROUND_PREFIX = "G"
DEFAULT_SEPARATOR = "-"
GROUND_LEVEL = 0  # the ground floor stores level 0 + is_ground=true

BUILDING_MODES = frozenset({"auto", "sequential", "manual"})
INDIVIDUAL_MODES = frozenset({"sequential", "custom", "manual"})
SEQUENTIAL_SCOPES = frozenset({"per_building", "continuous"})


class NumberingError(ValueError):
    """Bad numbering inputs (unknown mode, missing manual numbers, bad counts).

    Raised as a plain ValueError subclass; the service translates it into a
    typed ``ValidationError`` so the engine stays framework-free.
    """


@dataclass(frozen=True)
class FloorSpec:
    """One floor's generation inputs."""

    level: int
    is_ground: bool
    houses_count: int
    # For MANUAL: the exact numbers the admin typed for this floor, in order.
    manual_numbers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GeneratedHouse:
    """One generated house (floor-scoped position within a building)."""

    number: str
    level: int
    is_ground: bool
    position_on_floor: int  # 1-based within the floor


def _zeropad(seq: int, pad: int) -> str:
    """Zero-pad ``seq`` to at least ``pad`` digits (never truncates)."""
    return str(seq).zfill(max(pad, 0))


def _validate_count(count: int, *, what: str) -> None:
    if count < 0:
        raise NumberingError(f"{what} count cannot be negative (got {count}).")


# --- Building generation ----------------------------------------------------

def generate_building_numbers(
    floors: list[FloorSpec],
    *,
    mode: str,
    count_pad: int = DEFAULT_COUNT_PAD,
    ground_prefix: str = DEFAULT_GROUND_PREFIX,
    sequential_scope: str = "per_building",
    start_at: int = 1,
) -> list[GeneratedHouse]:
    """Generate every house number for a single building.

    ``floors`` must already be ordered lowest→highest (ground first). ``start_at``
    only matters for SEQUENTIAL/continuous (the service passes the next global
    counter); it is ignored by AUTO and per_building sequential (which start at 1).
    Returns houses in generation order (floor-major, position-minor).
    """
    if mode not in BUILDING_MODES:
        raise NumberingError(f"Unknown building numbering mode '{mode}'.")
    if sequential_scope not in SEQUENTIAL_SCOPES:
        raise NumberingError(f"Unknown sequential_scope '{sequential_scope}'.")

    out: list[GeneratedHouse] = []

    if mode == "auto":
        for fl in floors:
            _validate_count(fl.houses_count, what="floor houses")
            prefix = ground_prefix if fl.is_ground else str(fl.level)
            for pos in range(1, fl.houses_count + 1):
                out.append(
                    GeneratedHouse(
                        number=f"{prefix}{_zeropad(pos, count_pad)}",
                        level=fl.level,
                        is_ground=fl.is_ground,
                        position_on_floor=pos,
                    )
                )
        return out

    if mode == "sequential":
        counter = start_at if sequential_scope == "continuous" else 1
        for fl in floors:
            _validate_count(fl.houses_count, what="floor houses")
            for pos in range(1, fl.houses_count + 1):
                out.append(
                    GeneratedHouse(
                        number=str(counter),
                        level=fl.level,
                        is_ground=fl.is_ground,
                        position_on_floor=pos,
                    )
                )
                counter += 1
        return out

    # mode == "manual": echo the admin-typed numbers, one list per floor.
    for fl in floors:
        _validate_count(fl.houses_count, what="floor houses")
        if len(fl.manual_numbers) != fl.houses_count:
            raise NumberingError(
                "Manual numbers count must match houses_count for the floor "
                f"(level {fl.level}: expected {fl.houses_count}, "
                f"got {len(fl.manual_numbers)})."
            )
        for pos, num in enumerate(fl.manual_numbers, start=1):
            cleaned = num.strip()
            if not cleaned:
                raise NumberingError(
                    f"Empty manual house number on floor level {fl.level}."
                )
            out.append(
                GeneratedHouse(
                    number=cleaned,
                    level=fl.level,
                    is_ground=fl.is_ground,
                    position_on_floor=pos,
                )
            )
    return out


def next_sequential_start(generated: list[GeneratedHouse], start_at: int) -> int:
    """Next continuous counter after a batch (for continuous scope across towers)."""
    return start_at + len(generated)


# --- Individual generation --------------------------------------------------

@dataclass(frozen=True)
class RowSpec:
    """One row's generation inputs (individual-type society)."""

    houses_count: int
    prefix: str = ""  # CUSTOM only
    pad: int = 0  # CUSTOM only (0 = no padding)
    manual_numbers: list[str] = field(default_factory=list)  # MANUAL only


@dataclass(frozen=True)
class GeneratedIndividualHouse:
    """One generated individual house (row-scoped position)."""

    number: str
    position_in_row: int  # 1-based within the row


def generate_row_numbers(
    row: RowSpec,
    *,
    mode: str,
    start_at: int = 1,
) -> list[GeneratedIndividualHouse]:
    """Generate a single row's house numbers.

    ``start_at`` seeds SEQUENTIAL so the 1,2,3… sequence carries across rows (the
    service passes the running counter). CUSTOM restarts count at 1 per row.
    """
    if mode not in INDIVIDUAL_MODES:
        raise NumberingError(f"Unknown individual numbering mode '{mode}'.")
    _validate_count(row.houses_count, what="row houses")

    out: list[GeneratedIndividualHouse] = []

    if mode == "sequential":
        for pos in range(1, row.houses_count + 1):
            out.append(
                GeneratedIndividualHouse(
                    number=str(start_at + pos - 1), position_in_row=pos
                )
            )
        return out

    if mode == "custom":
        for pos in range(1, row.houses_count + 1):
            suffix = _zeropad(pos, row.pad) if row.pad else str(pos)
            out.append(
                GeneratedIndividualHouse(
                    number=f"{row.prefix}{suffix}", position_in_row=pos
                )
            )
        return out

    # mode == "manual"
    if len(row.manual_numbers) != row.houses_count:
        raise NumberingError(
            "Manual numbers count must match houses_count for the row "
            f"(expected {row.houses_count}, got {len(row.manual_numbers)})."
        )
    for pos, num in enumerate(row.manual_numbers, start=1):
        cleaned = num.strip()
        if not cleaned:
            raise NumberingError("Empty manual house number in row.")
        out.append(
            GeneratedIndividualHouse(number=cleaned, position_in_row=pos)
        )
    return out


# --- Display code (derived, never stored — docs §3) -------------------------

def building_display_code(
    building_name: str, number: str, *, separator: str = DEFAULT_SEPARATOR
) -> str:
    """Building house display code, e.g. ``A-201``."""
    return f"{building_name}{separator}{number}"


def individual_display_code(number: str) -> str:
    """Individual house display code — just the bare number."""
    return number


# --- Clash detection (shared helper — docs §4 overrides/batch reject) -------

def find_duplicate_numbers(numbers: list[str]) -> list[str]:
    """Return the numbers that appear more than once (order-stable, deduped)."""
    seen: set[str] = set()
    dupes: dict[str, None] = {}
    for n in numbers:
        if n in seen:
            dupes[n] = None
        seen.add(n)
    return list(dupes)
