"""Hello / health use cases."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.application.container import Container
from app.application.dto import HealthResult, HelloParams, HelloResult
from app.domain.models.common import PortError


class HelloUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def execute(self, params: HelloParams) -> HelloResult:
        if params.protocol_version != self._container.protocol_version:
            raise PortError(
                code="unsupported.version",
                message=(
                    f"UI speaks protocol {params.protocol_version}; "
                    f"worker speaks {self._container.protocol_version}"
                ),
                retryable=False,
                details={"supported_protocol_version": self._container.protocol_version},
            )
        return HelloResult(
            protocol_version=self._container.protocol_version,
            app_version=self._container.app_version,
            schema_version=self._container.schema_version,
            plugin_api_version="1",
            capabilities=[
                "system.hello",
                "system.health",
                "system.shutdown",
                "tasks.list",
                "tasks.cancel",
                "settings.get",
                "settings.update",
                "plugins.list",
                "plugins.enable",
                "plugins.disable",
                "plugins.configure",
                "plugins.get_capabilities",
                "plugins.reset_circuit",
                "diagnostics.sleep",
                "diagnostics.fetch_url",
            ],
        )


class HealthUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def execute(self) -> HealthResult:
        disk_used = 0
        if self._container.data_dir:
            usage = shutil.disk_usage(Path(self._container.data_dir))
            disk_used = usage.used

        registry = self._container.registry
        plugin_health = registry.describe() if registry is not None else []
        queue_depth = 0
        if self._container.task_runner is not None:
            queue_depth = self._container.task_runner.depth

        return HealthResult(
            status="healthy",
            app_version=self._container.app_version,
            schema_version=self._container.schema_version,
            protocol_version=self._container.protocol_version,
            data_dir=self._container.data_dir,
            disk_usage_bytes=disk_used,
            plugin_health=plugin_health,
            queue_depth=queue_depth,
        )
