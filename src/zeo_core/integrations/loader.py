"""
Explicit loader for ZeoCore integrations.

This module handles discovering entry points, filtering them against an allow-list,
instantiating the integration factories, initializing them, and registering them
into a registry. It ensures no side effects occur unless explicitly requested.
"""

import logging
from importlib.metadata import EntryPoint, entry_points

from zeo_core.integrations.core.protocols import IntegrationProtocol
from zeo_core.integrations.core.registry import IntegrationRegistry
from zeo_core.integrations.core.results import IntegrationLoadReport

logger = logging.getLogger(__name__)

DEFAULT_ENTRY_GROUP = "zeo_core.integrations"


def _distribution_of(entry_point: EntryPoint) -> str:
    """``name version`` of the distribution that registered an entry point."""
    dist = getattr(entry_point, "dist", None)
    name = getattr(dist, "name", None)
    if not name:
        return "an unknown distribution"
    version = getattr(dist, "version", None)
    return f"{name} {version}" if version else str(name)


def _group_by_name(group: str) -> dict[str, list[EntryPoint]]:
    """Every entry point of the group, by name, in the order importlib reports them."""
    by_name: dict[str, list[EntryPoint]] = {}
    for ep in entry_points().select(group=group):
        by_name.setdefault(ep.name, []).append(ep)
    return by_name


def _ambiguity(name: str, candidates: list[EntryPoint]) -> str:
    """The error for a name registered more than once; never an ordering rule."""
    who = "; ".join(sorted(_distribution_of(ep) for ep in candidates))
    return (
        f"Integration '{name}' is registered by more than one distribution "
        f"({who}); uninstall all but one."
    )


def list_available_entry_points(
    group: str = DEFAULT_ENTRY_GROUP,
) -> list[dict[str, str]]:
    """
    List available integration entry points without loading them.

    A name registered by more than one distribution is listed once per
    registration and marked ``"ambiguous": "true"``; loading it is an error.

    Args:
        group: The entry point group to search.

    Returns:
        list[dict]: Metadata about available integrations.
    """
    results = []
    for name, candidates in _group_by_name(group).items():
        ambiguous = len(candidates) > 1
        for ep in candidates:
            results.append(
                {
                    "integration_id": name,
                    "value": ep.value,
                    "module": ep.module,
                    "distribution": _distribution_of(ep),
                    "ambiguous": "true" if ambiguous else "false",
                }
            )
    return results


def _load_one_entry_point(
    integration_id: str,
    entry_point: EntryPoint,
    registry: IntegrationRegistry,
    report: IntegrationLoadReport,
    strict: bool,
    initialize: bool,
) -> bool:
    """
    Load, validate, optionally initialize, and register a single integration
    from its entry point, mutating `report` in place. Extracted from
    load_enabled_entry_points to keep its own branch count under the C901
    threshold; behavior/order/messages unchanged from the original inline
    loop body.

    Returns False if the caller should ABORT the whole loop (return report
    immediately - only happens in strict mode on an initialization failure
    or an unexpected exception), True otherwise (caller continues the loop).
    """
    try:
        logger.debug(f"Loading integration: {integration_id}")
        factory = entry_point.load()

        if not callable(factory):
            error_msg = f"Entry point {integration_id} is not callable."
            report.errors.append(error_msg)
            report.success = False
            return True

        instance = factory()

        if not isinstance(instance, IntegrationProtocol):
            # Duck-typing check if isinstance fails due to import quirks
            if not (hasattr(instance, "initialize") and hasattr(instance, "name")):
                error_msg = (
                    f"Instance for {integration_id} does not satisfy "
                    f"IntegrationProtocol."
                )
                report.errors.append(error_msg)
                report.success = False
                return True

        # Report-only: the registry keys on the instance's integration_id, which
        # can differ from the entry-point name (google.mail -> googlemail). A
        # mismatch is listed, never refused, and no id is renamed here.
        actual = getattr(instance, "integration_id", integration_id)
        if actual != integration_id:
            report.id_mismatches.append(f"{integration_id} -> {actual}")

        # Initialize if requested
        if initialize:
            init_result = instance.initialize()
            if not init_result.success:
                error_msg = (
                    f"Failed to initialize {integration_id}: {init_result.error}"
                )
                report.errors.append(error_msg)
                report.success = False

                if strict:
                    logger.error(
                        f"Strict mode enabled: Aborting after "
                        f"initialization failure of {integration_id}"
                    )
                    return False

                return True

        # Register
        registry.register(instance)
        report.loaded.append(integration_id)
        return True

    except Exception as e:
        error_msg = f"Unexpected error loading {integration_id}: {str(e)}"
        report.errors.append(error_msg)
        report.success = False
        logger.exception(error_msg)

        return not strict


def load_enabled_entry_points(
    registry: IntegrationRegistry,
    enabled: list[str],
    group: str = DEFAULT_ENTRY_GROUP,
    strict: bool = True,
    initialize: bool = True,
) -> IntegrationLoadReport:
    """
    Load specific integrations by ID from entry points.

    Args:
        registry: The registry to populate.
        enabled: List of integration_ids to load. Order is preserved.
        group: Entry point group.
        strict: If True, fails if a requested integration is missing OR
            fails initialization.
        initialize: If True, calls .initialize() on the integration instance.

    Returns:
        IntegrationLoadReport: Detailed report of what happened.
    """
    report = IntegrationLoadReport(success=True)

    # Get all available entry points map: name -> every registration of it
    ep_map = _group_by_name(group)

    for integration_id in enabled:
        if len(ep_map.get(integration_id, ())) > 1:
            # Two distributions registered one name: an error in both modes, never
            # "the last one wins" (zeocore-SOW-36 slice 1).
            msg = _ambiguity(integration_id, ep_map[integration_id])
            report.errors.append(msg)
            report.success = False
            logger.error(msg)
            if strict:
                return report
            report.skipped.append(integration_id)
            continue
        if integration_id not in ep_map:
            msg = f"Integration '{integration_id}' not found in entry points."
            if strict:
                report.errors.append(msg)
                report.success = False
                logger.error(msg)
                return report
            else:
                report.warnings.append(msg)
                report.skipped.append(integration_id)
                logger.warning(msg)
                continue

        keep_going = _load_one_entry_point(
            integration_id,
            ep_map[integration_id][0],
            registry,
            report,
            strict,
            initialize,
        )
        if not keep_going:
            return report

    return report
