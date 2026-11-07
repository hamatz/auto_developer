from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ManifestValidationError(ValueError):
    """TeamManifest のスキーマまたは依存関係バリデーションエラー。"""


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

        version = int(data["version"])
        project = str(data["project"])

        base_data = data.get("base", {})
        base_ref = str(base_data.get("ref", "main"))
        require_clean = bool(base_data.get("require_clean", True))

        contract_data = data.get("contracts", {})
        contracts = ContractConfig(
            path=str(contract_data.get("path", "contracts/")),
            freeze_before_execution=bool(contract_data.get("freeze_before_execution", True)),
        )

        integration_data = data.get("integration", {})
        integration = IntegrationConfig(
            strategy=str(integration_data.get("strategy", "merge-sequential")),
            require_team_evidence=bool(integration_data.get("require_team_evidence", True)),
            reverify_after_merge=bool(integration_data.get("reverify_after_merge", True)),
            update_main=bool(integration_data.get("update_main", False)),
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
            if tid in team_ids:
                raise ManifestValidationError(f"Duplicate team id: '{tid}'")
            team_ids.add(tid)

            owns = list(t_data.get("owns", []))
            depends_on = list(t_data.get("depends_on", []))
            max_parallel = int(t_data.get("max_parallel_tasks", 1))

            teams.append(TeamConfig(
                id=tid,
                owns=owns,
                depends_on=depends_on,
                max_parallel_tasks=max_parallel,
            ))

        # 依存関係の妥当性検証
        for team in teams:
            for dep in team.depends_on:
                if dep not in team_ids:
                    raise ManifestValidationError(
                        f"Team '{team.id}' depends on unknown team '{dep}'"
                    )

        # 循環依存検知
        cls._validate_dag(teams)

        return cls(
            version=version,
            project=project,
            base_ref=base_ref,
            require_clean=require_clean,
            contracts=contracts,
            teams=teams,
            integration=integration,
        )

    @classmethod
    def _validate_dag(cls, teams: list[TeamConfig]) -> None:
        """依存関係グラフに循環がないか検証する。"""
        adj: dict[str, list[str]] = {t.id: list(t.depends_on) for t in teams}
        visited: dict[str, int] = {}  # 0: visiting, 1: visited

        def visit(node: str) -> None:
            if node in visited:
                if visited[node] == 0:
                    raise ManifestValidationError(f"Circular dependency detected involving '{node}'")
                return
            visited[node] = 0
            for neighbor in adj.get(node, []):
                visit(neighbor)
            visited[node] = 1

        for team_id in adj:
            if team_id not in visited:
                visit(team_id)

    @classmethod
    def load(cls, path: Path | str) -> TeamManifest:
        """JSON または YAML 風形式からマニフェストを読み込む。"""
        p = Path(path).resolve()
        if not p.exists():
            raise ManifestValidationError(f"Manifest file not found: {p}")

        content = p.read_text(encoding="utf-8")
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 将来の拡張用 / 簡易パーサー
            raise ManifestValidationError(f"Could not parse manifest at {p}. Expected valid JSON.")

        return cls.from_dict(data)

    def get_execution_stages(self) -> list[list[str]]:
        """トポロジカルソートを行い、並行実行可能なステージ順（リストのリスト）を返す。

        各ステージ内のチームは互いに依存関係がなく、安全に並行起動できる。
        """
        in_degree: dict[str, int] = {t.id: 0 for t in self.teams}
        graph: dict[str, list[str]] = {t.id: [] for t in self.teams}

        for team in self.teams:
            in_degree[team.id] = len(team.depends_on)
            for dep in team.depends_on:
                graph[dep].append(team.id)

        stages: list[list[str]] = []
        # 依存関係のない（in_degree == 0）チームから開始
        current_stage = [tid for tid, deg in in_degree.items() if deg == 0]

        while current_stage:
            current_stage.sort()  # 決定論的な順序
            stages.append(current_stage)
            next_stage: list[str] = []
            for tid in current_stage:
                for dependent in graph[tid]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        next_stage.append(dependent)
            current_stage = next_stage

        return stages
