from __future__ import annotations

import uuid
from typing import Any

import aiosqlite


class SqliteResumeRepository:
    def __init__(self, conn, clock_now) -> None:
        self._conn = conn
        self._now = clock_now

    def _ts(self) -> str:
        return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")

    async def add_profile(self, profile_id: str, display_name: str) -> None:
        now = self._ts()
        await self._conn.execute(
            "INSERT INTO candidate_profiles(id, display_name, created_at, updated_at) VALUES (?,?,?,?)",
            (profile_id, display_name, now, now),
        )
        await self._conn.commit()

    async def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM candidate_profiles WHERE id=?", (profile_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def add_resume_file(self, file_id: str, sha256: str, original_name: str, mime_type: str, size_bytes: int, stored_path: str) -> None:
        await self._conn.execute(
            "INSERT INTO resume_files(id, sha256, original_name, mime_type, size_bytes, stored_path, imported_at) VALUES (?,?,?,?,?,?,?)",
            (file_id, sha256, original_name, mime_type, size_bytes, stored_path, self._ts()),
        )
        await self._conn.commit()

    async def add_resume_version(
        self, version_id: str, profile_id: str, file_id: str, label: str,
        parser_id: str, parser_version: str, text_hash: str, parsed_json: str,
        parent_version_id: str | None = None, is_active: bool = False,
    ) -> None:
        await self._conn.execute(
            """INSERT INTO resume_versions(id, candidate_profile_id, resume_file_id, label, parent_version_id,
               parser_id, parser_version, text_hash, parsed_json, is_active, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (version_id, profile_id, file_id, label, parent_version_id, parser_id, parser_version,
             text_hash, parsed_json, 1 if is_active else 0, self._ts()),
        )
        await self._conn.commit()

    async def list_versions(self, profile_id: str) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM resume_versions WHERE candidate_profile_id=? ORDER BY created_at DESC", (profile_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def set_active(self, profile_id: str, version_id: str) -> None:
        await self._conn.execute(
            "UPDATE resume_versions SET is_active=0 WHERE candidate_profile_id=?", (profile_id,)
        )
        await self._conn.execute(
            "UPDATE resume_versions SET is_active=1 WHERE id=?", (version_id,)
        )
        await self._conn.execute(
            "UPDATE candidate_profiles SET active_resume_version_id=?, updated_at=? WHERE id=?",
            (version_id, self._ts(), profile_id),
        )
        await self._conn.commit()

    async def get_version(self, version_id: str) -> dict[str, Any] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM resume_versions WHERE id=?", (version_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def save_skills(self, version_id: str, skills: list[tuple[str, str, float]]) -> None:
        for sid, evidence, conf in skills:
            await self._conn.execute(
                "INSERT OR REPLACE INTO extracted_skills(id, resume_version_id, keyword_id, evidence, confidence, evidence_refs_json) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), version_id, sid, evidence, float(conf), "[]"),
            )
        await self._conn.commit()

    async def get_skills(self, version_id: str) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM extracted_skills WHERE resume_version_id=?", (version_id,))
        return [dict(r) for r in await cur.fetchall()]

    async def update_overrides(self, profile_id: str, overrides: dict[str, Any]) -> None:
        import json
        await self._conn.execute(
            "UPDATE candidate_profiles SET overrides_json=?, updated_at=? WHERE id=?",
            (json.dumps(overrides), self._ts(), profile_id),
        )
        await self._conn.commit()

    async def get_overrides(self, profile_id: str) -> dict[str, Any]:
        import json
        p = await self.get_profile(profile_id)
        if not p:
            return {}
        try:
            return json.loads(p.get("overrides_json") or "{}")
        except (ValueError, TypeError):
            return {}


class SqliteMatchRepository:
    def __init__(self, conn, clock_now) -> None:
        self._conn = conn
        self._now = clock_now

    def _ts(self) -> str:
        return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")

    async def save_result(
        self, result_id: str, job_id: str, profile_id: str, resume_version_id: str | None,
        model_id: str, model_version: str, weights: dict[str, Any], input_hash: str,
        fit_score: float, dimension_coverage: float, dimensions: list[dict[str, Any]],
        evaluated_at: str,
    ) -> None:
        import json
        await self._conn.execute(
            """INSERT INTO matching_results(id, job_id, candidate_profile_id, resume_version_id, model_id, model_version,
               weights_json, input_hash, fit_score, dimension_coverage, evaluated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (result_id, job_id, profile_id, resume_version_id, model_id, model_version,
             json.dumps(weights), input_hash, float(fit_score), float(dimension_coverage), evaluated_at),
        )
        for d in dimensions:
            await self._conn.execute(
                """INSERT INTO matching_dimension_scores(id, matching_result_id, dimension, strategy_id, strategy_version, score, available, weight, details_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), result_id, d["dimension"], d["strategy_id"], d["strategy_version"],
                 d.get("score"), 1 if d.get("available") else 0, float(d.get("weight", 0.0)), json.dumps(d.get("details", {}))),
            )
        await self._conn.commit()

    async def list_results(self, profile_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM matching_results WHERE candidate_profile_id=? ORDER BY evaluated_at DESC LIMIT ?",
            (profile_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def update_weights(self, profile_id: str, weights: dict[str, Any]) -> None:
        # stored per result; nothing global to update here
        return None
