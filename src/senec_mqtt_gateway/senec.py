"""Client for the local lala.cgi endpoint of SENEC.Home V2 and V3 systems.

The request sequence and headers are derived from pysenec_ha of ha-senec-v3
(https://github.com/marq24/ha-senec-v3), Copyright 2023 Matthias Marquardt,
licensed under the Apache License 2.0. Rewritten as a standalone client without
Home Assistant dependencies.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from .decode import decode_tree
from .schema import DEFAULT_PLAN, ReadPlan, merge_sections

_LOGGER = logging.getLogger(__name__)

MODE_NORMAL = "normal"
MODE_CHARGE = "charge"
BATTERY_MODES = (MODE_NORMAL, MODE_CHARGE)

_READ_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Connection": "keep-alive",
}
_MODE_FORMS = {
    MODE_NORMAL: {"ENERGY": {"SAFE_CHARGE_PROHIBIT": "u8_01"}},
    MODE_CHARGE: {"ENERGY": {"SAFE_CHARGE_FORCE": "u8_01"}},
}
# Firmware with NPU 2411 expects this request first and answers it with cookies.
_SESSION_FORM = {"FACTORY": {"SYS_TYPE": "", "COUNTRY": "", "DEVICE_ID": ""}}
_INFO_FORM = {
    "FACTORY": {"SYS_TYPE": ""},
    "WIZARD": {"APPLICATION_VERSION": ""},
    "SYS_UPDATE": {"NPU_IMAGE_VERSION": ""},
}


class SenecError(Exception):
    """The SENEC could not be reached or returned an unusable response."""


@dataclass(frozen=True)
class DeviceInfo:
    model: str | None = None
    version: str | None = None


def create_session(timeout: float = 20.0) -> aiohttp.ClientSession:
    """Create an HTTP session suitable for a SENEC.

    The device uses a self-signed certificate and is addressed by IP, so TLS
    verification is disabled and cookies must be accepted for IP hosts.
    """
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=False),
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        timeout=aiohttp.ClientTimeout(total=timeout),
    )


class SenecClient:
    """Serialises all access to the device, which handles parallel requests badly."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        retry_delay: float = 1.0,
        verify_delay: float = 2.0,
        form_delay: float = 0.5,
        plan: ReadPlan = DEFAULT_PLAN,
    ) -> None:
        self.plan = plan
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._url = f"{self._base_url}/lala.cgi"
        self._retry_delay = retry_delay
        self._verify_delay = verify_delay
        self._form_delay = form_delay
        self._lock = asyncio.Lock()

    async def _post(self, form: dict[str, Any], extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
        body = json.dumps(form, separators=(",", ":"))
        headers = {**_READ_HEADERS, **(extra_headers or {})}
        async with self._lock:
            try:
                async with self._session.post(self._url, data=body, headers=headers) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                raise SenecError(f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SenecError("response is not a JSON object")
        return decode_tree(payload)

    async def start_session(self) -> None:
        """Run the initial request of newer firmware; older firmware does not need it."""
        try:
            await self._post(_SESSION_FORM)
        except SenecError as exc:
            _LOGGER.debug("Session request failed: %s", exc)

    async def read_device_info(self) -> DeviceInfo:
        """Return model and firmware, or empty values if they are unavailable."""
        try:
            data = await self._post(_INFO_FORM)
        except SenecError as exc:
            _LOGGER.debug("Reading device info failed: %s", exc)
            return DeviceInfo()

        sys_type = data.get("FACTORY", {}).get("SYS_TYPE")
        app = data.get("WIZARD", {}).get("APPLICATION_VERSION")
        npu = data.get("SYS_UPDATE", {}).get("NPU_IMAGE_VERSION")
        versions = [f"{label} {value}" for label, value in (("NPU", npu), ("App", app)) if value is not None]
        return DeviceInfo(
            model=f"SYS_TYPE {sys_type}" if sys_type is not None else None,
            version=" / ".join(versions) or None,
        )

    async def read_values(self) -> dict[str, Any]:
        """Read all published values, retrying once."""
        forms = self.plan.poll_forms
        try:
            data = await self._read_forms(forms)
        except SenecError:
            await asyncio.sleep(self._retry_delay)
            data = await self._read_forms(forms)
        missing = {section for form in forms for section in form} - data.keys()
        if missing:
            raise SenecError(f"response is missing {', '.join(sorted(missing))}")
        return data

    async def read_details(self) -> dict[str, Any]:
        """Read the slower values."""
        return await self._read_forms(self.plan.detail_forms)

    async def _read_forms(self, forms: tuple[dict[str, dict[str, str]], ...]) -> dict[str, Any]:
        """Send small requests one after the other, with a pause between them."""
        responses = []
        for number, form in enumerate(forms):
            if number:
                await asyncio.sleep(self._form_delay)
            responses.append(await self._post(form))
        return merge_sections(*responses)

    async def set_battery_mode(self, mode: str) -> None:
        """Switch between normal operation and forced charging."""
        await self._post(
            _MODE_FORMS[mode],
            extra_headers={
                "Origin": self._base_url,
                "Referer": f"{self._base_url}/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        await asyncio.sleep(self._verify_delay)
        data = await self._post({"ENERGY": {"SAFE_CHARGE_RUNNING": ""}})
        running = data.get("ENERGY", {}).get("SAFE_CHARGE_RUNNING")
        if running != (1 if mode == MODE_CHARGE else 0):
            # Can be legitimate, e.g. a full battery does not start forced charging.
            _LOGGER.warning("Battery mode %s requested, SAFE_CHARGE_RUNNING is %s", mode, running)
