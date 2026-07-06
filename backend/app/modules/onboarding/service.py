"""OnboardingService — the onboarding module's business logic + audit (docs/03 §2).

All rules live here (docs/modules/onboarding.md §4/§5): type selection, structure
mapping via the pure ``numbering`` engine, house generation in one transaction per
building/row batch, number overrides with clash reporting, the blocking-wizard
state machine, completion (flip society → active), later edits, and the
cross-module house-registry reads.

Every state change writes an audit row in the SAME session; the service NEVER
commits (``get_session`` commits once — docs/PF §12).

FROZEN CORE + WAVE STUBS: the Phase-0 lead implements type selection, the state
machine helpers, the registry reads, and the audit snapshots. The generation /
override / complete / later-edit methods are the wave sub-agents' slices — their
signatures are frozen; their bodies raise ``NotImplementedError`` until built.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.modules.onboarding import numbering
from app.modules.onboarding.models import Building, House, OnboardingProgress
from app.modules.onboarding.repository import OnboardingRepository
from app.modules.onboarding.models import Floor, Row
from app.modules.onboarding.schemas import (
    SOCIETY_TYPES,
    BuildingAddFloorsRequest,
    BuildingMapRequest,
    BuildingsCreateRequest,
    RowsCreateRequest,
)
from app.platform.audit.service import AuditService
from app.platform.models import Society


class OnboardingService:
    """Orchestrates the onboarding wizard for the caller's active society."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = OnboardingRepository(session)
        self._audit = AuditService(session)

    # --- society + progress helpers (lead-owned) ---------------------------

    def _get_society(self, society_id: int) -> Society:
        society = self._session.get(Society, society_id)
        if society is None:
            raise NotFoundError(
                "Society not found.", details={"society_id": society_id}
            )
        return society

    def _get_or_create_progress(self, society_id: int) -> OnboardingProgress:
        """Fetch the wizard progress row, creating a fresh one on first touch."""
        progress = self._repo.get_progress(society_id)
        if progress is None:
            progress = self._repo.add_progress(
                OnboardingProgress(
                    society_id=society_id,
                    current_step="type_selection",
                )
            )
        return progress

    def _require_onboarding_open(self, society: Society) -> None:
        """Reject wizard writes once onboarding is complete (society active)."""
        if society.status != "onboarding":
            raise ConflictError(
                "Onboarding is already complete for this society.",
                details={"society_id": society.id, "status": society.status},
            )

    # --- type selection (lead-owned; step 1) -------------------------------

    def select_type(
        self, society_id: int, society_type: str, *, actor_user_id: int
    ) -> Society:
        """Set ``societies.type`` (step 1). Cannot change once houses exist (spec §4)."""
        if society_type not in SOCIETY_TYPES:
            raise ValidationError(
                "Invalid society type.",
                details={"field": "type", "allowed": sorted(SOCIETY_TYPES)},
            )
        society = self._get_society(society_id)
        self._require_onboarding_open(society)

        progress = self._get_or_create_progress(society_id)

        # Changing type after structure exists would orphan houses — block it.
        if society.type is not None and society.type != society_type:
            if self._repo.list_all_houses(society_id):
                raise ConflictError(
                    "Cannot change society type after houses exist.",
                    details={"current_type": society.type},
                )

        before_type = society.type
        society.type = society_type
        progress.type_selected = society_type
        progress.current_step = "structure_mapping"
        self._session.flush()

        self._audit.record(
            action="onboarding.type_selected",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="society",
            entity_id=society_id,
            before={"type": before_type},
            after={"type": society_type},
        )
        return society

    # --- house registry (cross-module contract — docs §7; lead-owned) ------

    def list_houses(self, society_id: int) -> list[dict[str, Any]]:
        """All houses with derived display codes — the registry read other modules use."""
        houses = self._repo.list_all_houses(society_id)
        buildings = {b.id: b for b in self._repo.list_buildings(society_id)}
        return [self._house_with_display(h, buildings) for h in houses]

    def resolve_house(
        self,
        society_id: int,
        *,
        number: str,
        building_id: int | None = None,
    ) -> dict[str, Any]:
        """Resolve a house by (building, number) or by number (individual). 404 if absent."""
        if building_id is not None:
            house = self._repo.resolve_by_building_and_number(
                society_id, building_id, number
            )
        else:
            house = self._repo.resolve_by_number(society_id, number)
        if house is None:
            raise NotFoundError(
                "House not found.",
                details={"number": number, "building_id": building_id},
            )
        buildings = {b.id: b for b in self._repo.list_buildings(society_id)}
        return self._house_with_display(house, buildings)

    def _house_with_display(
        self, house: House, buildings: dict[int, Building]
    ) -> dict[str, Any]:
        if house.building_id is not None:
            building = buildings.get(house.building_id)
            separator = "-"
            if building is not None:
                separator = building.numbering_config.get("display_separator", "-")
            name = building.name if building is not None else ""
            display = numbering.building_display_code(
                name, house.number, separator=separator
            )
        else:
            display = numbering.individual_display_code(house.number)
        return {
            "id": house.id,
            "society_id": house.society_id,
            "building_id": house.building_id,
            "floor_id": house.floor_id,
            "row_id": house.row_id,
            "position_in_row": house.position_in_row,
            "number": house.number,
            "numbering_mode": house.numbering_mode,
            "number_overridden": house.number_overridden,
            "status": house.status,
            "display_code": display,
        }

    # --- audit snapshot helper (lead-owned) --------------------------------

    @staticmethod
    def _house_snapshot(house: House) -> dict[str, Any]:
        return {
            "building_id": house.building_id,
            "floor_id": house.floor_id,
            "row_id": house.row_id,
            "number": house.number,
            "numbering_mode": house.numbering_mode,
            "status": house.status,
        }

    # ======================================================================
    # Wave C: state / resume / draft (spec §4 blocking-wizard, §6 GET /state)
    # ======================================================================

    def get_state(self, society_id: int) -> dict[str, Any]:
        """Build the resume payload (``OnboardingStateOut``) from progress + structure.

        Reconstructs where the admin left off (spec §4 "Reopening the app resumes
        the wizard from ``onboarding_progress`` + already-created buildings/rows").
        Read-only apart from lazily creating the progress row on first touch.
        """
        society = self._get_society(society_id)
        progress = self._get_or_create_progress(society_id)

        buildings = self._repo.list_buildings(society_id)
        rows = self._repo.list_rows(society_id)

        return {
            "society_id": society_id,
            "type": society.type,
            "status": society.status,
            "current_step": progress.current_step,
            "current_building_index": progress.current_building_index,
            "draft": progress.draft,
            "numbering_defaults": progress.numbering_defaults,
            "buildings": buildings,
            "rows": rows,
            "next_action": self._next_action(society, progress, buildings, rows),
        }

    @staticmethod
    def _next_action(
        society: Society,
        progress: OnboardingProgress,
        buildings: list[Building],
        rows: list[Row],
    ) -> str:
        """A UI hint for the wizard's next step (derived from current state)."""
        if society.status == "active" or progress.current_step == "completed":
            return "done"
        if society.type is None:
            return "select_type"
        if society.type == "building":
            if not buildings:
                return "create_buildings"
            return "map_building"
        # individual_houses
        if not rows:
            return "create_rows"
        return "review"

    def save_draft(
        self, society_id: int, draft: dict, *, actor_user_id: int
    ) -> OnboardingProgress:
        """Persist the in-progress building/row inputs for exact resume (spec §4/§6).

        A draft is in-progress scratch (not a committed structure change), so it is
        deliberately NOT audited. ``current_step``/``current_building_index`` are
        lifted out of the draft when present so ``GET /state`` resumes precisely.
        """
        society = self._get_society(society_id)
        self._require_onboarding_open(society)
        progress = self._get_or_create_progress(society_id)

        progress.draft = draft
        step = draft.get("current_step")
        if step is not None:
            progress.current_step = step
        if "current_building_index" in draft:
            progress.current_building_index = draft["current_building_index"]
        self._session.flush()
        return progress

    # ======================================================================
    # Wave A: building flow (spec §4 building flow, §6 building endpoints)
    # ======================================================================

    def create_buildings(
        self, society_id: int, data: BuildingsCreateRequest, *, actor_user_id: int
    ) -> list[Building]:
        """Create one building per admin-typed name (spec §4 "define count + names").

        This is ALSO the later "add building" path — new buildings APPEND, their
        ``display_order`` continuing after any existing towers (never reset).
        Duplicate names (in-request or against existing) are rejected up front;
        the ``UNIQUE(society_id, name)`` index is the safety net. One
        ``onboarding.building_created`` audit row per building.
        """
        # No onboarding-open guard: "add building" is a legitimate later edit
        # allowed post-completion (spec §4/§6). Type guard is kept.
        society = self._get_society(society_id)
        if society.type != "building":
            raise ValidationError(
                "Buildings can only be created for a 'building'-type society.",
                details={"type": society.type},
            )

        existing = self._repo.list_buildings(society_id)
        existing_names = {b.name for b in existing}
        next_order = max((b.display_order for b in existing), default=0) + 1

        # Reject duplicates in-request (case-sensitive, matching the DB unique)
        # and against existing towers before touching the DB.
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in data.names:
            name = raw.strip()
            if not name:
                raise ValidationError(
                    "Building name cannot be empty.", details={"field": "names"}
                )
            if name in seen:
                raise ConflictError(
                    "Duplicate building name in request.",
                    details={"name": name},
                )
            if name in existing_names:
                raise ConflictError(
                    "A building with this name already exists.",
                    details={"name": name},
                )
            seen.add(name)
            cleaned.append(name)

        created: list[Building] = []
        for name in cleaned:
            building = self._repo.add_building(
                Building(
                    society_id=society_id,
                    name=name,
                    display_order=next_order,
                    numbering_config={},
                )
            )
            next_order += 1
            self._audit.record(
                action="onboarding.building_created",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="building",
                entity_id=building.id,
                after={"name": building.name, "display_order": building.display_order},
            )
            created.append(building)
        return created

    def map_building(
        self,
        society_id: int,
        building_id: int,
        data: BuildingMapRequest,
        *,
        actor_user_id: int,
    ) -> list[House]:
        """The core generator: floors + numbering config → houses, one transaction.

        Steps (spec §4 building flow + numbering algorithms):
          1. Load the building (society-scoped; 404 if not the caller's).
          2. Persist ``Floor`` rows; enforce exactly one ground floor, distinct
             upper levels ≥1, ground stored as level 0. Audit ``floor_added`` each.
          3. Order floors lowest→highest (ground first), build ``FloorSpec`` list,
             and call ``numbering.generate_building_numbers`` (seeding ``start_at``
             from ``max_continuous_number+1`` for continuous sequential).
          4. Clash-check the generated numbers in-batch and against existing house
             numbers in this building → ``ValidationError`` reporting offenders.
          5. Persist ``House`` rows (status='empty') and the building's config; save
             ``numbering_defaults`` for prefill-repeat; set ``current_building_index``.
          6. Audit ONE ``onboarding.houses_generated``.
        """
        # No onboarding-open guard: mapping is allowed as a later edit
        # post-completion (spec §4/§6). Type guard is kept.
        society = self._get_society(society_id)
        if society.type != "building":
            raise ValidationError(
                "Building mapping requires a 'building'-type society.",
                details={"type": society.type},
            )
        building = self._repo.get_building(society_id, building_id)
        if building is None:
            raise NotFoundError(
                "Building not found.", details={"building_id": building_id}
            )
        if self._repo.list_houses_for_building(building_id):
            raise ConflictError(
                "This building has already been mapped.",
                details={"building_id": building_id},
            )

        cfg = data.numbering_config
        if cfg.mode not in numbering.BUILDING_MODES:
            raise ValidationError(
                "Invalid building numbering mode.",
                details={
                    "field": "mode",
                    "allowed": sorted(numbering.BUILDING_MODES),
                },
            )
        if cfg.sequential_scope not in numbering.SEQUENTIAL_SCOPES:
            raise ValidationError(
                "Invalid sequential_scope.",
                details={
                    "field": "sequential_scope",
                    "allowed": sorted(numbering.SEQUENTIAL_SCOPES),
                },
            )

        # --- validate floor shape (exactly one ground; distinct upper levels) ---
        ground_seen = False
        upper_levels: set[int] = set()
        for fin in data.floors:
            if fin.is_ground:
                if ground_seen:
                    raise ValidationError(
                        "A building may have at most one ground floor.",
                        details={"field": "floors"},
                    )
                ground_seen = True
                if fin.level != numbering.GROUND_LEVEL:
                    raise ValidationError(
                        "The ground floor must have level 0.",
                        details={"level": fin.level},
                    )
            else:
                if fin.level < 1:
                    raise ValidationError(
                        "Upper floors must have level >= 1.",
                        details={"level": fin.level},
                    )
                if fin.level in upper_levels:
                    raise ValidationError(
                        "Duplicate floor level.", details={"level": fin.level}
                    )
                upper_levels.add(fin.level)

        # --- persist floors (ground first, then ascending), audit each ---------
        ordered_inputs = sorted(
            data.floors, key=lambda f: (0 if f.is_ground else 1, f.level)
        )
        # Resolve each floor's effective houses_count (per-floor override wins,
        # else the building default; both missing → 422). Spec §3.
        effective_counts = [
            self._resolve_houses_count(fin, data.default_houses_per_floor)
            for fin in ordered_inputs
        ]
        floor_rows: list[Floor] = []
        for fin, count in zip(ordered_inputs, effective_counts):
            floor = self._repo.add_floor(
                Floor(
                    society_id=society_id,
                    building_id=building_id,
                    level=fin.level,
                    is_ground=fin.is_ground,
                    label=fin.label,
                    houses_count=count,
                )
            )
            floor_rows.append(floor)
            self._audit.record(
                action="onboarding.floor_added",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="floor",
                entity_id=floor.id,
                after={
                    "building_id": building_id,
                    "level": floor.level,
                    "is_ground": floor.is_ground,
                },
            )

        # --- generate numbers via the pure engine -----------------------------
        specs = [
            numbering.FloorSpec(
                level=fin.level,
                is_ground=fin.is_ground,
                houses_count=count,
                manual_numbers=list(fin.manual_numbers),
            )
            for fin, count in zip(ordered_inputs, effective_counts)
        ]
        start_at = 1
        if cfg.mode == "sequential" and cfg.sequential_scope == "continuous":
            # Seed ONLY from prior continuous-sequential building houses so AUTO/
            # manual numbers don't pollute the running sequence (spec §4).
            start_at = self._repo.max_continuous_building_number(society_id) + 1
        try:
            generated = numbering.generate_building_numbers(
                specs,
                mode=cfg.mode,
                count_pad=cfg.count_pad,
                ground_prefix=cfg.ground_prefix,
                sequential_scope=cfg.sequential_scope,
                start_at=start_at,
            )
        except numbering.NumberingError as exc:
            raise ValidationError(str(exc), details={"field": "numbering"}) from exc

        # --- clash detection (in-batch dupes + against existing) ---------------
        new_numbers = [g.number for g in generated]
        self._reject_clashes(
            new_numbers, self._repo.building_numbers(society_id, building_id)
        )

        # --- map generated houses onto their floors + persist ------------------
        floor_by_level = {(f.is_ground, f.level): f for f in floor_rows}
        houses: list[House] = []
        for g in generated:
            floor = floor_by_level[(g.is_ground, g.level)]
            houses.append(
                self._repo.add_house(
                    House(
                        society_id=society_id,
                        building_id=building_id,
                        floor_id=floor.id,
                        row_id=None,
                        position_in_row=None,
                        number=g.number,
                        numbering_mode=cfg.mode,
                        number_overridden=False,
                        status="empty",
                        first_left_empty_on=None,
                    )
                )
            )

        # --- persist config + prefill defaults + resume cursor -----------------
        building.numbering_config = cfg.model_dump()
        progress = self._get_or_create_progress(society_id)
        progress.numbering_defaults = cfg.model_dump()
        progress.current_building_index = building.display_order
        self._session.flush()

        self._audit.record(
            action="onboarding.houses_generated",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="building",
            entity_id=building_id,
            after={"mode": cfg.mode, "count": len(houses)},
        )
        return houses

    def add_floors(
        self,
        society_id: int,
        building_id: int,
        data: "BuildingAddFloorsRequest",
        *,
        actor_user_id: int,
    ) -> list[House]:
        """Add floors to an ALREADY-mapped building, reusing its stored numbering
        config (spec §4 "add floor" later edit). One transaction.

        Only the NEW floors' houses are generated. New numbers are clash-checked
        against the building's existing numbers. For continuous sequential the
        seed carries on from prior continuous-sequential houses. Audits one
        ``onboarding.floor_added`` per new floor + one ``onboarding.houses_generated``.
        No onboarding-open guard: this is a legitimate post-completion later edit.
        """
        society = self._get_society(society_id)
        if society.type != "building":
            raise ValidationError(
                "Adding floors requires a 'building'-type society.",
                details={"type": society.type},
            )
        building = self._repo.get_building(society_id, building_id)
        if building is None:
            raise NotFoundError(
                "Building not found.", details={"building_id": building_id}
            )

        cfg = building.numbering_config or {}
        mode = cfg.get("mode")
        if mode not in numbering.BUILDING_MODES:
            raise ValidationError(
                "This building has no stored numbering config; map it first.",
                details={"building_id": building_id},
            )
        sequential_scope = cfg.get("sequential_scope", "per_building")
        count_pad = cfg.get("count_pad", numbering.DEFAULT_COUNT_PAD)
        ground_prefix = cfg.get("ground_prefix", numbering.DEFAULT_GROUND_PREFIX)

        # --- validate new floors against each other AND existing floors --------
        existing_floors = self._repo.list_floors(building_id)
        existing_ground = any(f.is_ground for f in existing_floors)
        existing_levels = {f.level for f in existing_floors if not f.is_ground}

        ground_seen = existing_ground
        upper_levels = set(existing_levels)
        for fin in data.floors:
            if fin.is_ground:
                if ground_seen:
                    raise ValidationError(
                        "A building may have at most one ground floor.",
                        details={"field": "floors"},
                    )
                ground_seen = True
                if fin.level != numbering.GROUND_LEVEL:
                    raise ValidationError(
                        "The ground floor must have level 0.",
                        details={"level": fin.level},
                    )
            else:
                if fin.level < 1:
                    raise ValidationError(
                        "Upper floors must have level >= 1.",
                        details={"level": fin.level},
                    )
                if fin.level in upper_levels:
                    raise ValidationError(
                        "Duplicate floor level.", details={"level": fin.level}
                    )
                upper_levels.add(fin.level)

        ordered_inputs = sorted(
            data.floors, key=lambda f: (0 if f.is_ground else 1, f.level)
        )
        effective_counts = [
            self._resolve_houses_count(fin, data.default_houses_per_floor)
            for fin in ordered_inputs
        ]

        # --- persist the new floors, audit each --------------------------------
        floor_rows: list[Floor] = []
        for fin, count in zip(ordered_inputs, effective_counts):
            floor = self._repo.add_floor(
                Floor(
                    society_id=society_id,
                    building_id=building_id,
                    level=fin.level,
                    is_ground=fin.is_ground,
                    label=fin.label,
                    houses_count=count,
                )
            )
            floor_rows.append(floor)
            self._audit.record(
                action="onboarding.floor_added",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="floor",
                entity_id=floor.id,
                after={
                    "building_id": building_id,
                    "level": floor.level,
                    "is_ground": floor.is_ground,
                },
            )

        # --- generate numbers for the NEW floors only --------------------------
        specs = [
            numbering.FloorSpec(
                level=fin.level,
                is_ground=fin.is_ground,
                houses_count=count,
                manual_numbers=list(fin.manual_numbers),
            )
            for fin, count in zip(ordered_inputs, effective_counts)
        ]
        start_at = 1
        if mode == "sequential" and sequential_scope == "continuous":
            start_at = self._repo.max_continuous_building_number(society_id) + 1
        try:
            generated = numbering.generate_building_numbers(
                specs,
                mode=mode,
                count_pad=count_pad,
                ground_prefix=ground_prefix,
                sequential_scope=sequential_scope,
                start_at=start_at,
            )
        except numbering.NumberingError as exc:
            raise ValidationError(str(exc), details={"field": "numbering"}) from exc

        # --- clash-check the new numbers against the building's existing ones ---
        new_numbers = [g.number for g in generated]
        self._reject_clashes(
            new_numbers, self._repo.building_numbers(society_id, building_id)
        )

        floor_by_level = {(f.is_ground, f.level): f for f in floor_rows}
        houses: list[House] = []
        for g in generated:
            floor = floor_by_level[(g.is_ground, g.level)]
            houses.append(
                self._repo.add_house(
                    House(
                        society_id=society_id,
                        building_id=building_id,
                        floor_id=floor.id,
                        row_id=None,
                        position_in_row=None,
                        number=g.number,
                        numbering_mode=mode,
                        number_overridden=False,
                        status="empty",
                        first_left_empty_on=None,
                    )
                )
            )
        self._session.flush()

        self._audit.record(
            action="onboarding.houses_generated",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="building",
            entity_id=building_id,
            after={"mode": mode, "count": len(houses)},
        )
        return houses

    def preview_building(
        self, society_id: int, building_id: int
    ) -> list[dict[str, Any]]:
        """Return the building's existing houses (with display codes). Read-only."""
        building = self._repo.get_building(society_id, building_id)
        if building is None:
            raise NotFoundError(
                "Building not found.", details={"building_id": building_id}
            )
        buildings = {b.id: b for b in self._repo.list_buildings(society_id)}
        return [
            self._house_with_display(h, buildings)
            for h in self._repo.list_houses_for_building(building_id)
        ]

    # ======================================================================
    # Wave B: individual flow (spec §4 individual numbering, §6 POST /rows)
    # ======================================================================

    def create_rows(
        self, society_id: int, data: RowsCreateRequest, *, actor_user_id: int
    ) -> list[House]:
        """Create rows + generate their houses (spec §4 individual flow).

        SEQUENTIAL threads one continuous 1,2,3… counter across ALL rows (seeded
        from ``max_continuous_number+1`` so later "add rows" calls carry on);
        CUSTOM/MANUAL restart per row. Clash-checking runs across the whole batch
        AND against existing individual numbers. One ``onboarding.houses_generated``
        audit per row batch (the row is the natural unit). One transaction.
        """
        society = self._get_society(society_id)
        self._require_onboarding_open(society)
        if society.type != "individual_houses":
            raise ValidationError(
                "Rows can only be created for an 'individual_houses'-type society.",
                details={"type": society.type},
            )

        existing_rows = self._repo.list_rows(society_id)
        existing_orders = {r.display_order for r in existing_rows}

        # Reject duplicate display_order in-request and against existing rows.
        seen_orders: set[int] = set()
        for rin in data.rows:
            if rin.numbering_config.mode not in numbering.INDIVIDUAL_MODES:
                raise ValidationError(
                    "Invalid individual numbering mode.",
                    details={
                        "field": "mode",
                        "allowed": sorted(numbering.INDIVIDUAL_MODES),
                    },
                )
            if rin.display_order in seen_orders:
                raise ConflictError(
                    "Duplicate row display_order in request.",
                    details={"display_order": rin.display_order},
                )
            if rin.display_order in existing_orders:
                raise ConflictError(
                    "A row with this display_order already exists.",
                    details={"display_order": rin.display_order},
                )
            seen_orders.add(rin.display_order)

        # Continuous sequential counter carried across every row in the batch.
        # Seeded ONLY from prior continuous-sequential individual houses (not
        # custom/manual numbers) so the 1,2,3… sequence is not polluted (spec §4).
        counter = self._repo.max_continuous_individual_number(society_id) + 1
        existing_numbers = self._repo.individual_numbers(society_id)
        # Track batch-so-far numbers for cross-row clash detection.
        batch_numbers: list[str] = []
        houses: list[House] = []

        for rin in sorted(data.rows, key=lambda r: r.display_order):
            cfg = rin.numbering_config
            row = self._repo.add_row(
                Row(
                    society_id=society_id,
                    display_order=rin.display_order,
                    label=rin.label,
                    houses_count=rin.houses_count,
                    numbering_config=cfg.model_dump(),
                )
            )

            spec = numbering.RowSpec(
                houses_count=rin.houses_count,
                prefix=cfg.prefix,
                pad=cfg.pad,
                manual_numbers=list(rin.manual_numbers),
            )
            try:
                generated = numbering.generate_row_numbers(
                    spec, mode=cfg.mode, start_at=counter
                )
            except numbering.NumberingError as exc:
                raise ValidationError(
                    str(exc), details={"field": "numbering", "row": rin.display_order}
                ) from exc

            if cfg.mode == "sequential":
                # Advance the running counter past this row so the 1,2,3… sequence
                # carries into the next row (engine seeds via ``start_at``).
                counter += len(generated)

            row_numbers = [g.number for g in generated]
            # Clash against existing + everything generated earlier in this batch.
            self._reject_clashes(
                batch_numbers + row_numbers, existing_numbers
            )
            batch_numbers.extend(row_numbers)

            for g in generated:
                houses.append(
                    self._repo.add_house(
                        House(
                            society_id=society_id,
                            building_id=None,
                            floor_id=None,
                            row_id=row.id,
                            position_in_row=g.position_in_row,
                            number=g.number,
                            # CUSTOM rows store 'manual' (models.py note) since the
                            # houses.numbering_mode domain is auto|sequential|manual.
                            numbering_mode=(
                                "sequential" if cfg.mode == "sequential" else "manual"
                            ),
                            number_overridden=False,
                            status="empty",
                            first_left_empty_on=None,
                        )
                    )
                )

            self._audit.record(
                action="onboarding.houses_generated",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="row",
                entity_id=row.id,
                after={"mode": cfg.mode, "count": len(generated)},
            )

        progress = self._get_or_create_progress(society_id)
        progress.current_step = "review"
        self._session.flush()
        return houses

    # ======================================================================
    # Wave C: override + complete (spec §4 overrides, §6 complete)
    # ======================================================================

    def override_house_number(
        self, society_id: int, house_id: int, number: str, *, actor_user_id: int
    ) -> House:
        """Override a house's number, rejecting clashes (spec §4 overrides).

        Uniqueness scope matches the partial indexes: within the same building
        (building type) or within the society (individual type), excluding this
        house itself. Sets ``number_overridden=True`` and audits old→new.
        """
        house = self._repo.get_house(society_id, house_id)
        if house is None:
            raise NotFoundError(
                "House not found.", details={"house_id": house_id}
            )
        cleaned = number.strip()
        if not cleaned:
            raise ValidationError(
                "House number cannot be empty.", details={"field": "number"}
            )

        if cleaned != house.number:
            if house.building_id is not None:
                existing = self._repo.building_numbers(society_id, house.building_id)
            else:
                existing = self._repo.individual_numbers(society_id)
            # Exclude this house's own current number from the clash set.
            existing = existing - {house.number}
            if cleaned in existing:
                raise ValidationError(
                    "House number already in use.",
                    details={"clashes": [cleaned]},
                )

        before = house.number
        house.number = cleaned
        house.number_overridden = True
        self._session.flush()

        self._audit.record(
            action="onboarding.house_number_overridden",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="house",
            entity_id=house.id,
            before={"number": before},
            after={"number": cleaned},
        )
        return house

    def complete(self, society_id: int, *, actor_user_id: int) -> Society:
        """Validate readiness then flip ``society.status`` onboarding → active (spec §4/§6).

        Readiness: a type is selected AND at least one house exists. Already-active
        societies raise ``ConflictError`` (idempotent-ish). Audits ``completed``.
        """
        society = self._get_society(society_id)
        if society.status != "onboarding":
            raise ConflictError(
                "Onboarding is already complete for this society.",
                details={"status": society.status},
            )
        if society.type is None:
            raise ValidationError(
                "Cannot complete onboarding before selecting a society type.",
                details={"missing": "type"},
            )
        if not self._repo.list_all_houses(society_id):
            raise ValidationError(
                "Cannot complete onboarding before at least one house exists.",
                details={"missing": "houses"},
            )

        before_status = society.status
        society.status = "active"
        progress = self._get_or_create_progress(society_id)
        progress.current_step = "completed"
        self._session.flush()

        self._audit.record(
            action="onboarding.completed",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="society",
            entity_id=society_id,
            before={"status": before_status},
            after={"status": society.status},
        )
        return society

    # ======================================================================
    # Wave D: later edits — post-completion allowed (spec §4 later edits)
    # ======================================================================

    def rename_building(
        self, society_id: int, building_id: int, name: str, *, actor_user_id: int
    ) -> Building:
        """Rename a building, preserving ``UNIQUE(society, name)`` (spec §4/§5)."""
        building = self._repo.get_building(society_id, building_id)
        if building is None:
            raise NotFoundError(
                "Building not found.", details={"building_id": building_id}
            )
        cleaned = name.strip()
        if not cleaned:
            raise ValidationError(
                "Building name cannot be empty.", details={"field": "name"}
            )
        if cleaned != building.name:
            for other in self._repo.list_buildings(society_id):
                if other.id != building_id and other.name == cleaned:
                    raise ConflictError(
                        "A building with this name already exists.",
                        details={"name": cleaned},
                    )

        before = building.name
        building.name = cleaned
        self._session.flush()
        self._audit.record(
            action="onboarding.building_renamed",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="building",
            entity_id=building.id,
            before={"name": before},
            after={"name": cleaned},
        )
        return building

    def delete_building(
        self, society_id: int, building_id: int, *, actor_user_id: int
    ) -> None:
        """Delete a building + cascade its floors/houses, guarded by status (spec §4).

        Guard: blocked if ANY house in the building is not ``status='empty'``.
        NOTE: the fuller dues/occupancy guard (Finance + House & Occupancy) is
        DEFERRED until those modules exist — this status-only guard is the v1 rule.
        """
        building = self._repo.get_building(society_id, building_id)
        if building is None:
            raise NotFoundError(
                "Building not found.", details={"building_id": building_id}
            )
        if self._repo.has_non_empty_houses_for_building(building_id):
            raise ConflictError(
                "Cannot delete a building with occupied houses.",
                details={"building_id": building_id},
            )

        before = {"name": building.name}
        self._repo.delete_houses_for_building(building_id)
        self._repo.delete_floors_for_building(building_id)
        self._repo.delete_building(building)
        self._session.flush()
        self._audit.record(
            action="onboarding.building_deleted",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="building",
            entity_id=building_id,
            before=before,
        )

    def delete_floor(
        self, society_id: int, floor_id: int, *, actor_user_id: int
    ) -> None:
        """Delete a floor + cascade its houses, guarded by status='empty' (spec §4).

        NOTE: the fuller dues/occupancy guard is DEFERRED (see ``delete_building``).
        """
        floor = self._repo.get_floor(society_id, floor_id)
        if floor is None:
            raise NotFoundError(
                "Floor not found.", details={"floor_id": floor_id}
            )
        if self._repo.has_non_empty_houses_for_floor(floor_id):
            raise ConflictError(
                "Cannot delete a floor with occupied houses.",
                details={"floor_id": floor_id},
            )

        before = {"building_id": floor.building_id, "level": floor.level}
        self._repo.delete_houses_for_floor(floor_id)
        self._repo.delete_floor(floor)
        self._session.flush()
        self._audit.record(
            action="onboarding.floor_deleted",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="floor",
            entity_id=floor_id,
            before=before,
        )

    def delete_house(
        self, society_id: int, house_id: int, *, actor_user_id: int
    ) -> None:
        """Delete a single house, guarded by its own status='empty' (spec §4).

        NOTE: the fuller dues/occupancy guard is DEFERRED (see ``delete_building``).
        """
        house = self._repo.get_house(society_id, house_id)
        if house is None:
            raise NotFoundError(
                "House not found.", details={"house_id": house_id}
            )
        if house.status != "empty":
            raise ConflictError(
                "Cannot delete an occupied house.",
                details={"house_id": house_id, "status": house.status},
            )

        before = self._house_snapshot(house)
        self._repo.delete_house(house)
        self._session.flush()
        self._audit.record(
            action="onboarding.house_deleted",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="house",
            entity_id=house_id,
            before=before,
        )

    # --- floor helpers -----------------------------------------------------

    @staticmethod
    def _resolve_houses_count(fin: Any, default: int | None) -> int:
        """Effective houses_count for a floor: per-floor override wins, else the
        building default; if BOTH are None → ``ValidationError`` naming the floor
        (spec §3)."""
        count = fin.houses_count if fin.houses_count is not None else default
        if count is None:
            raise ValidationError(
                "A floor needs houses_count or a building default_houses_per_floor.",
                details={
                    "field": "houses_count",
                    "level": fin.level,
                    "is_ground": fin.is_ground,
                },
            )
        return count

    # --- clash helper ------------------------------------------------------

    def _reject_clashes(
        self, new_numbers: list[str], existing: set[str]
    ) -> None:
        """Reject a batch on any in-batch dupe or collision with ``existing`` numbers.

        Reports the offending numbers in ``details['clashes']`` (spec §4 "batch
        rejected on clash, offending numbers reported"). In-batch dupes are found
        via ``numbering.find_duplicate_numbers``.
        """
        offenders: list[str] = list(numbering.find_duplicate_numbers(new_numbers))
        for n in new_numbers:
            if n in existing and n not in offenders:
                offenders.append(n)
        if offenders:
            raise ValidationError(
                "House number clash — batch rejected.",
                details={"clashes": offenders},
            )
