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

    async def get_resume_file_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM resume_files WHERE sha256=?", (sha256,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_versions(self, profile_id: str) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            """SELECT v.*, f.original_name AS original_name, f.stored_path AS stored_path
               FROM resume_versions v JOIN resume_files f ON f.id = v.resume_file_id
               WHERE v.candidate_profile_id=? ORDER BY v.created_at DESC""",
            (profile_id,),
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

    async def save_skills(
        self, version_id: str, skills: list[dict[str, Any]]
    ) -> None:
        # Replace the version's skill set: re-saving must not leave skills from
        # a previous extraction behind (the in-memory twin replaces wholesale).
        import json

        await self._conn.execute(
            "DELETE FROM extracted_skills WHERE resume_version_id=?", (version_id,)
        )
        for skill in skills:
            await self._conn.execute(
                """INSERT OR REPLACE INTO extracted_skills(
                     id, resume_version_id, keyword_id, evidence, confidence,
                     duration_years, recency_years, evidence_refs_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    version_id,
                    skill["keyword_id"],
                    skill["evidence"],
                    float(str(skill.get("confidence") or 0.0)),
                    skill.get("duration_years"),
                    skill.get("recency_years"),
                    json.dumps(skill.get("evidence_refs") or []),
                ),
            )
        await self._conn.commit()

    async def get_skills(self, version_id: str) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            """SELECT * FROM extracted_skills WHERE resume_version_id=? ORDER BY keyword_id""",
            (version_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def save_experience(
        self, version_id: str, entries: list[dict[str, Any]]
    ) -> None:
        import json

        await self._conn.execute(
            "DELETE FROM experience_records WHERE resume_version_id=?", (version_id,)
        )
        for entry in entries:
            await self._conn.execute(
                """INSERT INTO experience_records(
                     id, resume_version_id, employer, title, start_date, end_date,
                     is_current, bullets_json, confidence)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    version_id,
                    entry.get("employer") or None,
                    entry.get("title") or None,
                    entry.get("start") or None,
                    entry.get("end") or None,
                    1
                    if str(entry.get("end") or "").strip().lower()
                    in {"present", "current", "now", "today"}
                    else 0,
                    json.dumps(entry.get("bullets") or ""),
                    float(str(entry.get("confidence") or 0.0)),
                ),
            )
        await self._conn.commit()

    async def get_experience(self, version_id: str) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM experience_records WHERE resume_version_id=? ORDER BY rowid",
            (version_id,),
        )
        return [_experience_row(dict(row)) for row in await cur.fetchall()]

    async def save_education(
        self, version_id: str, entries: list[dict[str, Any]]
    ) -> None:
        await self._conn.execute(
            "DELETE FROM education_records WHERE resume_version_id=?", (version_id,)
        )
        for entry in entries:
            await self._conn.execute(
                """INSERT INTO education_records(
                     id, resume_version_id, institution, degree, field,
                     start_date, end_date, confidence)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    version_id,
                    entry.get("institution") or None,
                    entry.get("degree") or None,
                    entry.get("field") or None,
                    entry.get("start") or None,
                    entry.get("end") or None,
                    float(str(entry.get("confidence") or 0.0)),
                ),
            )
        await self._conn.commit()

    async def get_education(self, version_id: str) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM education_records WHERE resume_version_id=? ORDER BY rowid",
            (version_id,),
        )
        return [dict(row) for row in await cur.fetchall()]

    async def save_profile_items(
        self, version_id: str, kind: str, items: list[dict[str, Any]]
    ) -> None:
        import json

        await self._conn.execute(
            "DELETE FROM profile_items WHERE resume_version_id=? AND kind=?",
            (version_id, kind),
        )
        for item in items:
            await self._conn.execute(
                """INSERT INTO profile_items(id, resume_version_id, kind, name, details_json)
                   VALUES (?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    version_id,
                    kind,
                    item.get("name") or "",
                    json.dumps({"details": item.get("details") or ""}),
                ),
            )
        await self._conn.commit()

    async def get_profile_items(
        self, version_id: str, kind: str
    ) -> list[dict[str, Any]]:
        import json

        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM profile_items WHERE resume_version_id=? AND kind=? ORDER BY rowid",
            (version_id, kind),
        )
        rows = []
        for row in await cur.fetchall():
            data = dict(row)
            try:
                details = json.loads(data.pop("details_json", "{}") or "{}")
            except ValueError:
                details = {}
            data["details"] = details.get("details", "") if isinstance(details, dict) else ""
            rows.append(data)
        return rows

    async def update_overrides(self, profile_id: str, overrides: dict[str, Any]) -> None:
        import json
        await self._conn.execute(
            "UPDATE candidate_profiles SET overrides_json=?, updated_at=? WHERE id=?",
            (json.dumps(overrides), self._ts(), profile_id),
        )
        await self._conn.commit()

    async def update_contact(self, profile_id: str, contact: dict[str, Any]) -> None:
        import json
        await self._conn.execute(
            "UPDATE candidate_profiles SET contact_json=?, updated_at=? WHERE id=?",
            (json.dumps(contact), self._ts(), profile_id),
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

    async def get_resume_file(self, file_id: str) -> dict[str, Any] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM resume_files WHERE id=?", (file_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_skill_status(self, profile_id: str, keyword_id: str, status: str) -> None:
        await self._conn.execute(
            """INSERT INTO user_skill_status(candidate_profile_id, keyword_id, status, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(candidate_profile_id, keyword_id)
               DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at""",
            (profile_id, keyword_id, status, self._ts()),
        )
        await self._conn.commit()

    async def get_skill_status(self, profile_id: str) -> dict[str, str]:
        cur = await self._conn.execute(
            "SELECT keyword_id, status FROM user_skill_status WHERE candidate_profile_id=?",
            (profile_id,),
        )
        return {str(row[0]): str(row[1]) for row in await cur.fetchall()}


def _experience_row(row: dict[str, Any]) -> dict[str, Any]:
    import json

    data = dict(row)
    try:
        bullets = json.loads(data.pop("bullets_json", "[]") or "[]")
    except ValueError:
        bullets = []
    data["bullets"] = bullets if isinstance(bullets, list) else []
    data["is_current"] = bool(data.get("is_current"))
    return data


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
        evaluated_at: str, explanations: list[dict[str, Any]] | None = None,
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
        for explanation in explanations or []:
            await self._conn.execute(
                """INSERT INTO matching_explanations(id, matching_result_id, reason_code, message, evidence_json)
                   VALUES (?,?,?,?,?)""",
                (str(uuid.uuid4()), result_id, explanation.get("reason_code", ""),
                 explanation.get("message", ""), json.dumps(explanation.get("evidence", []))),
            )
        await self._conn.commit()

    async def list_results(self, profile_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            """SELECT r.*, j.title AS job_title, j.canonical_url AS job_url,
                      c.canonical_name AS company_name
               FROM matching_results r
               JOIN jobs j ON j.id = r.job_id
               LEFT JOIN companies c ON c.id = j.company_id
               WHERE r.candidate_profile_id=?
               ORDER BY r.fit_score DESC, r.evaluated_at DESC LIMIT ?""",
            (profile_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def find_result(
        self, profile_id: str, job_id: str, input_hash: str
    ) -> dict[str, Any] | None:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            """SELECT * FROM matching_results
               WHERE candidate_profile_id=? AND job_id=? AND input_hash=?
               ORDER BY evaluated_at DESC LIMIT 1""",
            (profile_id, job_id, input_hash),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_results(self, profile_id: str, job_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM matching_results WHERE candidate_profile_id=? AND job_id=?",
            (profile_id, job_id),
        )

    async def list_dimensions(self, result_id: str) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM matching_dimension_scores WHERE matching_result_id=? ORDER BY rowid",
            (result_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_explanations(self, result_id: str) -> list[dict[str, Any]]:
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM matching_explanations WHERE matching_result_id=? ORDER BY rowid",
            (result_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_weights(self, profile_id: str) -> dict[str, Any] | None:
        import json
        cur = await self._conn.execute(
            """SELECT weights_json FROM scoring_configurations
               WHERE candidate_profile_id=? AND is_active=1 LIMIT 1""",
            (profile_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        data = json.loads(row[0] or "{}")
        return data if isinstance(data, dict) else None

    async def set_weights(self, profile_id: str, weights: dict[str, Any]) -> None:
        import json
        now = self._ts()
        await self._conn.execute(
            "UPDATE scoring_configurations SET is_active=0 WHERE candidate_profile_id=?",
            (profile_id,),
        )
        await self._conn.execute(
            """INSERT INTO scoring_configurations(id, candidate_profile_id, name, weights_json,
                 is_active, created_at, updated_at)
               VALUES (?,?,?,?,1,?,?)
               ON CONFLICT(id) DO UPDATE SET weights_json=excluded.weights_json,
                 is_active=1, updated_at=excluded.updated_at""",
            (f"scfg_{profile_id}", profile_id, "Custom weights", json.dumps(weights), now, now),
        )
        await self._conn.commit()
