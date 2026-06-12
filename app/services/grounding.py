"""Code-level grounding gate for v2 account maps."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.account_map import AccountMap, ProductFit, UseCase
from app.services.capability_map import CapabilityMap


@dataclass(frozen=True)
class GroundingReport:
    account_map: AccountMap
    gaps_added: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    invalid_product_count: int = 0
    invalid_capability_count: int = 0
    dropped_use_case_ids: tuple[str, ...] = ()


@dataclass
class _GroundingState:
    gaps: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    invalid_product_count: int = 0
    invalid_capability_count: int = 0
    dropped_use_case_ids: list[str] = field(default_factory=list)


def ground_account_map(account_map: AccountMap, capability_map: CapabilityMap) -> GroundingReport:
    """Return a canonicalized AccountMap plus grounding diagnostics.

    This gate never invents content. Unknown product slugs are removed. If a use
    case loses all product fits, the use case is dropped and recorded as a gap.
    """

    state = _GroundingState(gaps=set(account_map.gaps))
    grounded_use_cases: list[UseCase] = []
    for use_case in account_map.recommended_use_cases:
        grounded_products = ground_products(use_case, capability_map, state)
        if not grounded_products:
            state.gaps.add(f"{use_case.id}.opswat_products")
            state.dropped_use_case_ids.append(use_case.id)
            continue
        grounded_use_cases.append(
            use_case.model_copy(update={"rank": len(grounded_use_cases) + 1, "opswat_products": grounded_products})
        )

    grounded_map = account_map.model_copy(
        update={
            "recommended_use_cases": grounded_use_cases,
            "gaps": sorted(state.gaps),
            "meta": account_map.meta.model_copy(
                update={
                    "retrieval": {
                        **account_map.meta.retrieval,
                        "grounding": {
                            "invalid_product_count": state.invalid_product_count,
                            "invalid_capability_count": state.invalid_capability_count,
                            "warning_count": len(state.warnings),
                            "dropped_use_case_ids": list(state.dropped_use_case_ids),
                        },
                    }
                }
            ),
        }
    )
    # Re-validate cross references after product/use-case changes.
    grounded_map = AccountMap.model_validate(grounded_map.model_dump())
    return GroundingReport(
        account_map=grounded_map,
        gaps_added=tuple(sorted(state.gaps - set(account_map.gaps))),
        warnings=tuple(state.warnings),
        invalid_product_count=state.invalid_product_count,
        invalid_capability_count=state.invalid_capability_count,
        dropped_use_case_ids=tuple(state.dropped_use_case_ids),
    )


def ground_products(use_case: UseCase, capability_map: CapabilityMap, state: _GroundingState) -> list[ProductFit]:
    grounded: list[ProductFit] = []
    for product_fit in use_case.opswat_products:
        product = capability_map.get(product_fit.slug)
        if product is None:
            state.invalid_product_count += 1
            state.gaps.add(f"{use_case.id}.opswat_products.{product_fit.slug}")
            state.warnings.append(f"{use_case.id}: removed unknown product slug '{product_fit.slug}'")
            continue

        invalid_capabilities = sorted(set(product_fit.capabilities_used) - product.capability_set)
        if invalid_capabilities:
            state.invalid_capability_count += len(invalid_capabilities)
            state.warnings.append(
                f"{use_case.id}/{product_fit.slug}: capabilities not in capability map: "
                + ", ".join(invalid_capabilities)
            )

        refs = product_fit.capability_evidence_refs
        valid_refs = capability_map.evidence_ref_set_for_product(product_fit.slug)
        invalid_refs = [ref for ref in refs if ref not in valid_refs]
        if invalid_refs:
            state.warnings.append(
                f"{use_case.id}/{product_fit.slug}: replaced capability evidence refs not found in capability map"
            )
            refs = []
        if not refs:
            refs = capability_map.evidence_refs_for_product(product_fit.slug, product_fit.capabilities_used)
        grounded.append(
            product_fit.model_copy(
                update={
                    "product": product.product,
                    "capability_evidence_refs": refs,
                }
            )
        )
    return grounded
