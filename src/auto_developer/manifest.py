from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_developer.boundary import match_path

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


@dataclass(frozen=True)
class _GlobNFA:
    """glob パターン共通集合判定用の単純 NFA。"""

    start: int
    accept: int
    eps: dict[int, tuple[int, ...]]
    edges: dict[int, tuple[tuple[str, str, int], ...]]


def _compile_glob_nfa(pattern: str) -> tuple[_GlobNFA, set[str]]:
    """boundary._glob_to_regex と同一規則の NFA とリテラル文字集合を構築する。"""
    pat = pattern.replace("\\", "/").strip().lstrip("/")
    eps_mut: dict[int, list[int]] = {}
    edges_mut: dict[int, list[tuple[str, str, int]]] = {}
    literals: set[str] = set()

    cur = 0
    next_id = 1
    i = 0
    n = len(pat)

    while i < n:
        if pat[i : i + 3] == "**/":
            s_loop = next_id
            s_after = next_id + 1
            next_id += 2
            eps_mut.setdefault(cur, []).extend((s_after, s_loop))
            edges_mut.setdefault(s_loop, []).extend((
                ("ANY", "", s_loop),
                ("LIT", "/", s_after),
            ))
            cur = s_after
            i += 3
        elif pat[i : i + 2] == "**":
            s_loop = next_id
            s_after = next_id + 1
            next_id += 2
            eps_mut.setdefault(cur, []).append(s_loop)
            edges_mut.setdefault(s_loop, []).append(("ANY", "", s_loop))
            eps_mut.setdefault(s_loop, []).append(s_after)
            cur = s_after
            i += 2
        elif pat[i] == "*":
            s_loop = next_id
            s_after = next_id + 1
            next_id += 2
            eps_mut.setdefault(cur, []).append(s_loop)
            edges_mut.setdefault(s_loop, []).append(("NON_SLASH", "", s_loop))
            eps_mut.setdefault(s_loop, []).append(s_after)
            cur = s_after
            i += 1
        elif pat[i] == "?":
            s_after = next_id
            next_id += 1
            edges_mut.setdefault(cur, []).append(("NON_SLASH", "", s_after))
            cur = s_after
            i += 1
        else:
            ch = pat[i]
            literals.add(ch)
            s_after = next_id
            next_id += 1
            edges_mut.setdefault(cur, []).append(("LIT", ch, s_after))
            cur = s_after
            i += 1

    nfa = _GlobNFA(
        start=0,
        accept=cur,
        eps={k: tuple(v) for k, v in eps_mut.items()},
        edges={k: tuple(v) for k, v in edges_mut.items()},
    )
    return nfa, literals


def _eps_closure(nfa: _GlobNFA, initial: tuple[int, ...]) -> frozenset[int]:
    """ε遷移のみで到達可能な状態集合を返す。"""
    visited: set[int] = set(initial)
    stack = list(initial)
    while stack:
        state = stack.pop()
        for nxt in nfa.eps.get(state, ()):
            if nxt not in visited:
                visited.add(nxt)
                stack.append(nxt)
    return frozenset(visited)


def _step_nfa(nfa: _GlobNFA, state: int, ch: str) -> frozenset[int]:
    """1文字 ch による遷移先の ε閉包を返す。"""
    targets: list[int] = []
    for kind, val, dst in nfa.edges.get(state, ()):
        if (
            (kind == "LIT" and ch == val)
            or (kind == "NON_SLASH" and ch != "/")
            or kind == "ANY"
        ):
            targets.append(dst)
    if not targets:
        return frozenset()
    return _eps_closure(nfa, tuple(targets))


def patterns_overlap(pattern_a: str, pattern_b: str) -> bool:
    """2つの owns glob パターンが同一のパスに一致しうるかを厳密判定する。"""
    nfa_a, lits_a = _compile_glob_nfa(pattern_a)
    nfa_b, lits_b = _compile_glob_nfa(pattern_b)

    used = lits_a | lits_b | {"/"}
    sentinel = next(
        chr(code) for code in range(1, 128) if chr(code) not in used
    )
    alphabet = sorted(used | {sentinel})

    start_a = _eps_closure(nfa_a, (nfa_a.start,))
    start_b = _eps_closure(nfa_b, (nfa_b.start,))
    queue: deque[tuple[int, int]] = deque(
        (sa, sb) for sa in start_a for sb in start_b
    )
    visited: set[tuple[int, int]] = set(queue)

    while queue:
        sa, sb = queue.popleft()
        if sa == nfa_a.accept and sb == nfa_b.accept:
            return True
        for ch in alphabet:
            next_a = _step_nfa(nfa_a, sa, ch)
            if not next_a:
                continue
            next_b = _step_nfa(nfa_b, sb, ch)
            if not next_b:
                continue
            for nsa in next_a:
                for nsb in next_b:
                    pair = (nsa, nsb)
                    if pair not in visited:
                        visited.add(pair)
                        queue.append(pair)
    return False


@dataclass
class TeamConfig:
    id: str
    owns: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    max_parallel_tasks: int = 1
    plan: str = ""

    def __post_init__(self) -> None:
        if not self.plan and self.id:
            self.plan = f"plans/{self.id}.md"


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
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        allow_depends_on: bool = True,
    ) -> TeamManifest:
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
        seen_plans: dict[str, str] = {}

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

            plan_raw = t_data.get("plan")
            if plan_raw is None:
                plan_path = f"plans/{tid}.md"
            elif allow_depends_on:
                plan_path = str(plan_raw).strip() or f"plans/{tid}.md"
            else:
                if not isinstance(plan_raw, str) or not plan_raw.strip():
                    raise ManifestValidationError(
                        f"Team '{tid}' field 'plan' must be a non-empty string"
                    )
                plan_path = plan_raw.strip().replace("\\", "/")
                while plan_path.startswith("./"):
                    plan_path = plan_path[2:]
                if (
                    not plan_path
                    or plan_path.startswith("/")
                    or ".." in Path(plan_path).parts
                ):
                    raise ManifestValidationError(
                        f"Team '{tid}' field 'plan' must be a relative path inside repository: "
                        f"{plan_raw!r}"
                    )

            if not allow_depends_on:
                if plan_path in seen_plans:
                    raise ManifestValidationError(
                        f"Duplicate team plan '{plan_path}' shared by "
                        f"'{seen_plans[plan_path]}' and '{tid}'"
                    )
                seen_plans[plan_path] = tid

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
                    plan=plan_path,
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
        if allow_depends_on:
            manifest._validate_dependencies()
        else:
            manifest._validate_ownership_boundaries()
            manifest.validate_independent_teams()
        return manifest

    def _validate_ownership_boundaries(self) -> None:
        """チーム間の owns 重複と、他チーム owns 内への plan 配置を拒否する。"""
        for i, team_a in enumerate(self.teams):
            for team_b in self.teams[i + 1 :]:
                for pat_a in team_a.owns:
                    for pat_b in team_b.owns:
                        if patterns_overlap(pat_a, pat_b):
                            raise ManifestValidationError(
                                f"Overlapping 'owns' patterns between team '{team_a.id}' "
                                f"({pat_a!r}) and team '{team_b.id}' ({pat_b!r})"
                            )

        for team in self.teams:
            for other in self.teams:
                if team.id == other.id:
                    continue
                for pat in other.owns:
                    if match_path(team.plan, pat):
                        raise ManifestValidationError(
                            f"Team '{team.id}' plan '{team.plan}' is included in "
                            f"team '{other.id}' owns ({pat!r})"
                        )

    def validate_independent_teams(self) -> None:
        """v0.9.0 向け: depends_on が空でないチームを拒否する。"""
        for team in self.teams:
            if team.depends_on:
                raise ManifestValidationError(
                    f"Team '{team.id}' specifies non-empty 'depends_on' ({team.depends_on}); "
                    "v0.9.0 only supports independent teams"
                )

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
    def load(cls, manifest_path: Path, *, allow_depends_on: bool = True) -> TeamManifest:
        """JSON マニフェストファイルを安全に読み込む。"""
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

        try:
            content = manifest_path.read_text(encoding="utf-8")
            data = json.loads(content)
        except json.JSONDecodeError as err:
            raise ManifestValidationError(f"Invalid JSON in manifest file: {err}") from err

        return cls.from_dict(data, allow_depends_on=allow_depends_on)


def load_teams_manifest(
    repo_dir: Path,
    *,
    manifest_path: Path | None = None,
) -> TeamManifest:
    """リポジトリ直下の teams.json を v0.9.0 規約（独立チーム）で読み込む。"""
    target = manifest_path or (repo_dir / "teams.json")
    return TeamManifest.load(target, allow_depends_on=False)
