from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TEAM_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ManifestValidationError(ValueError):
    """TeamManifest のスキーマまたは依存関係バリデーションエラー。"""


def _parse_bool(val: Any, field_name: str, default: bool) -> bool:
    """厳格なブール値検証。文字列 'false' 等の型混同を拒否する。"""
    if val is None:
        return default
    if not isinstance(val, bool):
        raise ManifestValidationError(
            f"Field '{field_name}' must be a boolean, got {type(val).__name__}: {val!r}"
        )
    return val


@dataclass
class TeamConfig:
    id: str
    owns: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    max_parallel_tasks: int = 1


@dataclass
class ContractConfig:
    path: str = "contracts/"
    freeze_before_execution: bool = True


@dataclass
class IntegrationConfig:
    strategy: str = "merge-sequential"
    require_team_evidence: bool = True
    reverify_after_merge: bool = True
    update_main: bool = False


@dataclass
class TeamManifest:
    version: int
    project: str
    base_ref: str = "main"
    require_clean: bool = True
    contracts: ContractConfig = field(default_factory=ContractConfig)
    teams: list[TeamConfig] = field(default_factory=list)
    integration: IntegrationConfig = field(default_factory=IntegrationConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamManifest:
        if not isinstance(data, dict):
            raise ManifestValidationError("Manifest data must be a dictionary")

        if "version" not in data or "project" not in data:
            raise ManifestValidationError("Missing required fields: 'version' and 'project'")

        if not isinstance(data["version"], int) or isinstance(data["version"], bool) or data["version"] < 1:
            raise ManifestValidationError(f"Field 'version' must be a positive integer: {data['version']!r}")

        version = int(data["version"])
        project = str(data["project"])

        base_data = data.get("base", {})
        base_ref = str(base_data.get("ref", "main"))
        require_clean = _parse_bool(base_data.get("require_clean"), "base.require_clean", True)

        contract_data = data.get("contracts", {})
        contracts = ContractConfig(
            path=str(contract_data.get("path", "contracts/")),
            freeze_before_execution=_parse_bool(
                contract_data.get("freeze_before_execution"), "contracts.freeze_before_execution", True
            ),
        )

        integration_data = data.get("integration", {})
        integration = IntegrationConfig(
            strategy=str(integration_data.get("strategy", "merge-sequential")),
            require_team_evidence=_parse_bool(
                integration_data.get("require_team_evidence"), "integration.require_team_evidence", True
            ),
            reverify_after_merge=_parse_bool(
                integration_data.get("reverify_after_merge"), "integration.reverify_after_merge", True
            ),
            update_main=_parse_bool(integration_data.get("update_main"), "integration.update_main", False),
        )

        teams_raw = data.get("teams", [])
        if not isinstance(teams_raw, list):
            raise ManifestValidationError("'teams' field must be a list")

        teams: list[TeamConfig] = []
        team_ids: set[str] = set()

        for t_data in teams_raw:
            if not isinstance(t_data, dict):
                raise ManifestValidationError("Each team entry must be a dictionary")
            tid = str(t_data.get("id", ""))
            if not tid:
                raise ManifestValidationError("Team entry missing 'id'")
            if not TEAM_ID_PATTERN.match(tid):
                raise ManifestValidationError(
                    f"Invalid team id format: '{tid}'. "
                    "Must start with lowercase letter, contain only [a-z0-9_-], and be 1-64 chars."
                )
            if tid in team_ids:
                raise ManifestValidationError(f"Duplicate team id: '{tid}'")
            team_ids.add(tid)

            owns_raw = t_data.get("owns")
            if not isinstance(owns_raw, list) or len(owns_raw) == 0:
                raise ManifestValidationError(f"Team '{tid}' must specify a non-empty 'owns' list")
            owns: list[str] = []
            for p in owns_raw:
                if not isinstance(p, str) or not p.strip():
                    raise ManifestValidationError(f"Team '{tid}' has invalid pattern in 'owns': {p!r}")
                owns.append(p.strip())

            depends_on_raw = t_data.get("depends_on", [])
            if not isinstance(depends_on_raw, list):
                raise ManifestValidationError(f"Team '{tid}' field 'depends_on' must be a list")
            depends_on = [str(d) for d in depends_on_raw]

            max_parallel_raw = t_data.get("max_parallel_tasks", 1)
            if (
                not isinstance(max_parallel_raw, int)
                or isinstance(max_parallel_raw, bool)
                or max_parallel_raw < 1
            ):
                raise ManifestValidationError(
                    f"Team '{tid}' max_parallel_tasks must be a positive integer, got: {max_parallel_raw!r}"
                )

            teams.append(
                TeamConfig(
                    id=tid,
                    owns=owns,
                    depends_on=depends_on,
                    max_parallel_tasks=max_parallel_raw,
                )
            )

        manifest = cls(
            version=version,
            project=project,
            base_ref=base_ref,
            require_clean=require_clean,
            contracts=contracts,
            teams=teams,
            integration=integration,
        )
        manifest._validate_dependencies()
        return manifest

    def _validate_dependencies(self) -> None:
        """未知の依存関係および循環依存を検証する。"""
        known_ids = {t.id for t in self.teams}
        for team in self.teams:
            for dep in team.depends_on:
                if dep not in known_ids:
                    raise ManifestValidationError(
                        f"Team '{team.id}' depends on unknown team '{dep}'"
                    )

        # 循環依存の検出 (トポロジカルソートで閉路をチェック)
        self.get_execution_stages()

    def get_execution_stages(self) -> list[list[str]]:
        """依存関係DAGを解決し、並列実行可能なチームIDのステージリストを返す。"""
        in_degree: dict[str, int] = {t.id: 0 for t in self.teams}
        graph: dict[str, list[str]] = {t.id: [] for t in self.teams}

        for team in self.teams:
            for dep in team.depends_on:
                graph[dep].append(team.id)
                in_degree[team.id] += 1

        stages: list[list[str]] = []
        current_layer = [tid for tid, deg in in_degree.items() if deg == 0]

        visited_count = 0
        while current_layer:
            current_layer.sort()  # 決定論的な順序を保証
            stages.append(current_layer)
            visited_count += len(current_layer)

            next_layer: list[str] = []
            for node in current_layer:
                for neighbor in graph[node]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_layer.append(neighbor)
            current_layer = next_layer

        if visited_count < len(self.teams):
            raise ManifestValidationError("Circular dependency detected in team dependencies")

        return stages

    @classmethod
    def load(cls, manifest_path: Path) -> TeamManifest:
        """JSON マニフェストファイルを安全に読み込む。"""
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

        try:
            content = manifest_path.read_text(encoding="utf-8")
            data = json.loads(content)
        except json.JSONDecodeError as err:
            raise ManifestValidationError(f"Invalid JSON in manifest file: {err}") from err

        return cls.from_dict(data)
