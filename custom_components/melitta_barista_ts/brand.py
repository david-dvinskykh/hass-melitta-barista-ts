"""Serve the machine's brand artwork from the integration itself.

The tile on the integrations and devices pages is fetched by the frontend
from ``brands.home-assistant.io``, addressed by domain. An integration
cannot redirect that, and until the domain is listed in Home Assistant's
shared brands repository the tile is a placeholder.

What an integration *can* do is serve the artwork over its own HTTP path and
ask the frontend to load a small module that swaps the image in the page.
That is what this does, and only when the option asking for it is on.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

BRAND_URL: Final = f"/{DOMAIN}_brand"
MODULE_URL: Final = f"{BRAND_URL}/brand-icon.js"

_STATIC_REGISTERED: Final = f"{DOMAIN}_brand_static"


async def async_serve_brand(hass: HomeAssistant) -> None:
    """Publish the artwork and have the frontend load the swap module."""
    if not hass.data.get(_STATIC_REGISTERED):
        # Static paths cannot be unregistered, so this happens once per run
        # whatever the entries do afterwards. Serving four PNGs and a module
        # costs nothing; the frontend only loads them while the option is on.
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    BRAND_URL, str(Path(__file__).parent / "brand"), cache_headers=True
                )
            ]
        )
        hass.data[_STATIC_REGISTERED] = True

    add_extra_js_url(hass, MODULE_URL)
    _LOGGER.debug("Serving the brand artwork from %s", BRAND_URL)


@callback
def async_stop_serving_brand(hass: HomeAssistant) -> None:
    """Stop the frontend from loading the swap module."""
    remove_extra_js_url(hass, MODULE_URL)
