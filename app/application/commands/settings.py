"""Settings get / update use cases.

User overrides are stored as a validated patch in ``app_settings``. Reads merge
the patch over code defaults so the UI always sees a complete ``AppConfig``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.application.config import SETTINGS_KEY, SETTINGS_SCHEMA_VERSION, AppConfig, deep_merge
from app.application.container import Container
from app.application.dto import SettingsPatch, SettingsResult
from app.domain.models.common import PortError


class SettingsUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    def _defaults(self) -> AppConfig:
        return AppConfig().with_data_dir(self._container.data_dir)

    async def get(self) -> SettingsResult:
        async with self._container.uow_factory() as uow:
            stored = await uow.settings.get(SETTINGS_KEY) or {}
            overrides = dict(stored)
        return self._result(overrides)

    async def get_all(self) -> SettingsResult:
        """Backwards-compatible alias used by the IPC handler."""
        return await self.get()

    async def update(self, patch_request: SettingsPatch) -> SettingsResult:
        if not isinstance(patch_request.patch, Mapping):
            raise PortError(
                code="validation.invalid_params",
                message="settings patch must be an object",
            )
        async with self._container.uow_factory() as uow:
            current = await uow.settings.get(SETTINGS_KEY) or {}
            merged = deep_merge(dict(current), dict(patch_request.patch))
            try:
                self._defaults().apply_patch(merged)
            except ValidationError as exc:
                raise PortError(
                    code="validation.invalid_params",
                    message="invalid settings patch",
                    details={"errors": exc.errors(include_url=False)},
                ) from exc
            except ValueError as exc:
                raise PortError(
                    code="permission.denied",
                    message=str(exc),
                ) from exc
            await uow.settings.put(
                SETTINGS_KEY, merged, schema_version=SETTINGS_SCHEMA_VERSION
            )
            await uow.commit()
        return self._result(merged)

    def _result(self, overrides: Mapping[str, Any]) -> SettingsResult:
        try:
            effective = self._defaults().apply_patch(overrides)
        except ValidationError as exc:
            raise PortError(
                code="validation.invalid_params",
                message="stored settings are invalid",
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        return SettingsResult(
            config=effective.effective_dict(),
            overrides=dict(overrides),
            schema_version=SETTINGS_SCHEMA_VERSION,
        )
