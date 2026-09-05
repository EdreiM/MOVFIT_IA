from typing import Any

from app.adapters.evolution_api import adapt_evolution_api
from app.adapters.base import NormalizedMessageEvent
from app.adapters.generic_mapping import adapt_generic_mapping
from app.adapters.movfit_hub import adapt_movfit_hub


ADAPTERS = {
    "evolution_api_v1": adapt_evolution_api,
    "movfit_hub_v1": adapt_movfit_hub,
    "generic_mapping": None,  # needs mapping
}


def resolve_adapter(
    adapter_key: str,
    payload: dict[str, Any],
    field_mapping: dict | None = None,
    integration_config: dict | None = None,
) -> NormalizedMessageEvent:
    if adapter_key == "evolution_api_v1":
        return adapt_evolution_api(payload, integration_config)
    if adapter_key == "movfit_hub_v1":
        return adapt_movfit_hub(payload)
    if adapter_key == "generic_mapping":
        return adapt_generic_mapping(payload, field_mapping)
    # fallback
    return adapt_generic_mapping(payload, field_mapping)
