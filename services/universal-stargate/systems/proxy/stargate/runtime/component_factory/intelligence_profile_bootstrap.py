"""Intelligence profile store initialization and cloud-catalog refresh.

This module owns the lifecycle of the IntelligenceProfileStore:

- Instantiation and loading of curated on-disk profiles (if the
  intelligence_profiles/ subdirectory exists under the config dir)
- One-time derivation of profiles from the cloud proxy catalog via derive_bulk
- Subscription to CLOUD_PROXY_CATALOG_UPDATED events for automatic refresh
  of the derived set when the upstream catalog changes

It depends on profile_transformation_bootstrap only for the shared
_get_config_dir helper. All cloud-client access is performed through the
federation_integration attached to the proxy (no direct construction here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ...proxy import StargateProxy

from .profile_transformation_bootstrap import _get_config_dir

logger = get_logger(__name__)


async def initialize_intelligence_profiles(proxy: StargateProxy) -> None:
    """Initialize the intelligence profile store and derive cloud profiles.

    Creates the IntelligenceProfileStore, loads any curated YAML profiles
    from the config directory, then (if a cloud client is reachable via the
    federation forwarder) fetches the enriched cloud catalog and derives
    intelligence profiles for every model. Finally subscribes to catalog
    updates so the derived set stays fresh without a restart.

    The store is attached to the proxy as ``intelligence_profile_store``.
    """
    from intelligence_profiles import IntelligenceProfileStore

    store = IntelligenceProfileStore()

    config_dir = _get_config_dir(proxy.config)
    curated_dir = config_dir / "intelligence_profiles"
    if curated_dir.is_dir():
        store.load_curated(curated_dir)

    cloud_client = _get_cloud_proxy_client(proxy)
    if cloud_client is not None:
        try:
            catalog_data = await cloud_client.get_models()
            models = catalog_data.get("models", [])
            if models:
                from systems.profiles.intelligence.deriver import derive_bulk

                profiles = derive_bulk(models)
                store.set_derived_bulk(profiles)
                logger.info(
                    "Intelligence profiles: %d derived from cloud catalog",
                    len(profiles),
                )
        except Exception as e:
            logger.exception(
                "Failed to derive intelligence profiles from cloud catalog: %s", e
            )

    proxy.intelligence_profile_store = store
    logger.info("Intelligence profile store initialized (%d profiles)", store.count)

    _subscribe_profile_refresh_on_catalog_change(proxy)


def _get_cloud_proxy_client(proxy: StargateProxy) -> object | None:
    """Extract CloudProxyClient from federation integration if available."""
    fed = getattr(proxy, "federation_integration", None)
    if fed is None:
        return None
    forwarder = getattr(fed, "forwarder", None)
    if forwarder is None:
        return None
    client = getattr(forwarder, "cloud_forwarder", None)
    if client is None or not hasattr(client, "get_models"):
        return None
    return client


def _subscribe_profile_refresh_on_catalog_change(proxy: StargateProxy) -> None:
    """Refresh derived profiles when the cloud proxy catalog changes."""
    if proxy.event_bus is None:
        return

    from src.scheduling.events import CLOUD_PROXY_CATALOG_UPDATED

    async def on_catalog_updated(event) -> None:
        store = proxy.intelligence_profile_store
        if store is None:
            return
        client = _get_cloud_proxy_client(proxy)
        if client is None:
            return
        try:
            catalog_data = await client.get_models()
            models = catalog_data.get("models", [])
            if models:
                from systems.profiles.intelligence.deriver import derive_bulk

                profiles = derive_bulk(models)
                store.set_derived_bulk(profiles)
                logger.info(
                    "Intelligence profiles refreshed: %d from catalog update",
                    len(profiles),
                )
        except Exception as e:
            logger.exception("Failed to refresh intelligence profiles: %s", e)

    proxy.event_bus.subscribe_async(CLOUD_PROXY_CATALOG_UPDATED, on_catalog_updated)
    logger.info("Subscribed to catalog updates for intelligence profile refresh")
