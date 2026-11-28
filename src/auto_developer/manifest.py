from __future__ import annotations

import json
import re
import subprocess
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_developer.audit import resolve_model_family
from auto_developer.boundary import match_path

TEAM_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_GLOB_CHARS = frozenset("*?[]")


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
    skills: list[dict[str, str]] = field(default_factory=list)
    maker: dict[str, str] | None = None
    checker: dict[str, str] | None = None
    review_docs: list[str] = field(default_factory=list)
    verify_script: str | None = None

    def __post_init__(self) -> None:
        if not self.plan and self.id:
            self.plan = f"plans/{self.id}.md"

    def to_profile_dict(self) -> dict[str, Any]:
        """チームの持ち物（profile）を JSON 出力向けの辞書として返す (TEAM-29)。"""
        return {
            "skills": [dict(s) for s in self.skills],
            "maker": dict(self.maker) if self.maker is not None else None,
            "checker": dict(self.checker) if self.checker is not None else None,
            "review_docs": list(self.review_docs),
            "verify_script": self.verify_script,
        }


def _validate_repo_rel_file_path(raw_val: Any, team_id: str, field_name: str) -> str:
    """リポジトリ最上位からの安全な相対ファイルパス（glob・絶対パス・.. 禁止）を検証する。"""
    if not isinstance(raw_val, str) or not raw_val.strip():
        raise ManifestValidationError(
            f"Team '{team_id}' field '{field_name}' must be a non-empty string"
        )
    if "\0" in raw_val:
        raise ManifestValidationError(
            f"Team '{team_id}' field '{field_name}' contains NUL byte"
        )
    norm = raw_val.strip().replace("\\", "/")
    if (
        norm.startswith("/")
        or re.match(r"^[a-zA-Z]:", norm) is not None
        or Path(norm).is_absolute()
    ):
        raise ManifestValidationError(
            f"Team '{team_id}' field '{field_name}' must be a relative path inside repository: {raw_val!r}"
        )
    while norm.startswith("./"):
        norm = norm[2:]
    if not norm or norm == "." or norm.endswith("/") or "//" in norm:
        raise ManifestValidationError(
            f"Team '{team_id}' field '{field_name}' must be a valid relative file path: {raw_val!r}"
        )
    parts = norm.split("/")
    if ".." in parts or "." in parts:
        raise ManifestValidationError(
            f"Team '{team_id}' field '{field_name}' must not contain '..' or '.': {raw_val!r}"
        )
    if any(ch in _GLOB_CHARS for ch in norm):
        raise ManifestValidationError(
            f"Team '{team_id}' field '{field_name}' must not contain glob characters: {raw_val!r}"
        )
    return norm


def _parse_team_skills(raw_val: Any, team_id: str) -> list[dict[str, str]]:
    """チームの skills 一覧を検証・正規化する (TEAM-29)。"""
    if raw_val is None:
        return []
    if not isinstance(raw_val, list):
        raise ManifestValidationError(f"Team '{team_id}' field 'skills' must be a list")
    skills: list[dict[str, str]] = []
    for idx, item in enumerate(raw_val):
        if not isinstance(item, dict):
            raise ManifestValidationError(
                f"Team '{team_id}' field 'skills[{idx}]' must be a dictionary"
            )
        name_raw = item.get("name")
        version_raw = item.get("version")
        if not isinstance(name_raw, str) or not name_raw.strip():
            raise ManifestValidationError(
                f"Team '{team_id}' field 'skills[{idx}].name' must be a non-empty string"
            )
        if not isinstance(version_raw, str) or not version_raw.strip():
            raise ManifestValidationError(
                f"Team '{team_id}' field 'skills[{idx}].version' must be a non-empty string"
            )
        skills.append({"name": name_raw.strip(), "version": version_raw.strip()})
    return skills


def _parse_team_model(raw_val: Any, team_id: str, field_name: str) -> dict[str, str] | None:
    """チームの maker / checker モデル宣言を検証し、系統を解決する (TEAM-29 / DX-7)。"""
    if raw_val is None:
        return None
    if not isinstance(raw_val, dict):
        raise ManifestValidationError(
            f"Team '{team_id}' field '{field_name}' must be a dictionary"
        )
    model_raw = raw_val.get("model")
    if not isinstance(model_raw, str) or not model_raw.strip():
        raise ManifestValidationError(
            f"Team '{team_id}' field '{field_name}.model' must be a non-empty string"
        )
    model_str = model_raw.strip()

    explicit_family: str | None = None
    if "family" in raw_val and raw_val["family"] is not None:
        fam_raw = raw_val["family"]
        if not isinstance(fam_raw, str) or not fam_raw.strip():
            raise ManifestValidationError(
                f"Team '{team_id}' field '{field_name}.family' must be a non-empty string"
            )
        explicit_family = fam_raw.strip()

    try:
        resolved_family, _ = resolve_model_family(model_str, explicit_family)
    except (ValueError, TypeError) as err:
        raise ManifestValidationError(
            f"Team '{team_id}' field '{field_name}' is invalid: {err}"
        ) from err

    return {"model": model_str, "family": resolved_family}


def _validate_team_model_pair(
    maker: dict[str, str] | None,
    checker: dict[str, str] | None,
    team_id: str,
) -> None:
    """maker と checker の両方が宣言されている場合、別系統かつ判定可能であることを検証する (INV-6)。"""
    if maker is None or checker is None:
        return
    m_fam = maker["family"]
    c_fam = checker["family"]
    if m_fam == "unknown" or c_fam == "unknown":
        raise ManifestValidationError(
            f"Team '{team_id}' cannot determine model family for 'maker' "
            f"({maker['model']!r} -> '{m_fam}') and 'checker' ({checker['model']!r} -> '{c_fam}')"
        )
    if maker["model"].lower() == checker["model"].lower() or m_fam == c_fam:
        raise ManifestValidationError(
            f"Team '{team_id}' 'maker' ({maker['model']!r}, family='{m_fam}') and "
            f"'checker' ({checker['model']!r}, family='{c_fam}') must belong to different model families (INV-6)"
        )


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

            skills = _parse_team_skills(t_data.get("skills"), tid)
            maker = _parse_team_model(t_data.get("maker"), tid, "maker")
            checker = _parse_team_model(t_data.get("checker"), tid, "checker")
            _validate_team_model_pair(maker, checker, tid)

            review_docs_raw = t_data.get("review_docs")
            if review_docs_raw is None:
                review_docs: list[str] = []
            elif not isinstance(review_docs_raw, list):
                raise ManifestValidationError(f"Team '{tid}' field 'review_docs' must be a list")
            else:
                review_docs = [
                    _validate_repo_rel_file_path(item, tid, f"review_docs[{idx}]")
                    for idx, item in enumerate(review_docs_raw)
                ]

            verify_script_raw = t_data.get("verify_script")
            verify_script = (
                None
                if verify_script_raw is None
                else _validate_repo_rel_file_path(verify_script_raw, tid, "verify_script")
            )

            teams.append(
                TeamConfig(
                    id=tid,
                    owns=owns,
                    depends_on=depends_on,
                    max_parallel_tasks=max_parallel_raw,
                    plan=plan_path,
                    skills=skills,
                    maker=maker,
                    checker=checker,
                    review_docs=review_docs,
                    verify_script=verify_script,
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


@dataclass(frozen=True)
class TeamContext:
    """worktree ブランチおよびコミット済み teams.json から解決された実行時チーム文脈。"""

    team_id: str
    plan_rel_path: str
    plan_path: Path
    owns_patterns: list[str]


def _normalize_rel_path(path_str: str) -> str:
    norm = path_str.replace("\\", "/").strip().lstrip("/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def _resolve_plan_rel_in_repo(repo_dir: Path, plan_arg: Path) -> tuple[str, Path]:
    resolved_repo = repo_dir.resolve()
    candidate = plan_arg if plan_arg.is_absolute() else (resolved_repo / plan_arg)
    resolved_plan = candidate.resolve()
    try:
        rel = resolved_plan.relative_to(resolved_repo).as_posix()
    except ValueError as err:
        raise ManifestValidationError(
            f"--plan '{plan_arg}' must reside inside repository '{resolved_repo}'"
        ) from err
    return rel, resolved_plan


def _get_current_branch_safe(repo_dir: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    branch = proc.stdout.strip()
    return "" if branch == "HEAD" else branch


def load_committed_teams_manifest(repo_dir: Path, *, ref: str = "HEAD") -> TeamManifest | None:
    """コミット済み Git ツリー（ref:teams.json）から TeamManifest を読み込む。作業ツリーからは読まない。"""
    rev_chk = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{tree}}"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if rev_chk.returncode != 0:
        return None

    ls_proc = subprocess.run(
        ["git", "ls-tree", ref, "--", "teams.json"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if ls_proc.returncode != 0:
        return None
    tree_entry = ls_proc.stdout.strip()
    if not tree_entry:
        return None
    if tree_entry.startswith("120000"):
        raise ManifestValidationError(
            f"Committed teams.json at '{ref}' must be a regular file, not a symlink"
        )

    show_proc = subprocess.run(
        ["git", "show", f"{ref}:teams.json"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if show_proc.returncode != 0:
        raise ManifestValidationError(
            f"Failed to read committed teams.json at '{ref}': {show_proc.stderr.strip()}"
        )

    try:
        data = json.loads(show_proc.stdout)
    except json.JSONDecodeError as err:
        raise ManifestValidationError(
            f"Invalid JSON in committed teams.json at '{ref}': {err}"
        ) from err

    return TeamManifest.from_dict(data, allow_depends_on=False)


def _build_team_context(repo_dir: Path, team: TeamConfig) -> TeamContext:
    plan_rel = _normalize_rel_path(team.plan)
    effective_owns = list(team.owns)
    if not any(match_path(plan_rel, pat) for pat in effective_owns):
        effective_owns.append(plan_rel)
    return TeamContext(
        team_id=team.id,
        plan_rel_path=plan_rel,
        plan_path=(repo_dir.resolve() / plan_rel),
        owns_patterns=effective_owns,
    )


def _validate_explicit_against_team(
    repo_dir: Path,
    team: TeamConfig,
    *,
    explicit_team_id: str | None,
    explicit_owns: list[str] | None,
    explicit_plan: Path | None,
) -> None:
    if explicit_team_id is not None and explicit_team_id != team.id:
        raise ManifestValidationError(
            f"--team-id '{explicit_team_id}' contradicts team '{team.id}'"
        )
    if explicit_plan is not None:
        plan_rel, _ = _resolve_plan_rel_in_repo(repo_dir, explicit_plan)
        if plan_rel != _normalize_rel_path(team.plan):
            raise ManifestValidationError(
                f"--plan '{plan_rel}' contradicts teams.json plan '{team.plan}' for team '{team.id}'"
            )
    if explicit_owns is not None:
        if not explicit_owns:
            raise ManifestValidationError("--owns must specify at least one non-empty pattern")
        norm_explicit = {_normalize_rel_path(p) for p in explicit_owns if p.strip()}
        norm_team = {_normalize_rel_path(p) for p in team.owns}
        norm_effective = norm_team | {_normalize_rel_path(team.plan)}
        if not norm_explicit or (norm_explicit != norm_team and norm_explicit != norm_effective):
            raise ManifestValidationError(
                f"--owns {explicit_owns!r} contradicts teams.json owns {team.owns!r} for team '{team.id}'"
            )


def resolve_team_context(
    repo_dir: Path,
    *,
    ref: str = "HEAD",
    explicit_team_id: str | None = None,
    explicit_owns: list[str] | None = None,
    explicit_plan: Path | None = None,
    allow_explicit_without_manifest: bool = False,
) -> TeamContext | None:
    """現在のブランチ team/<id> とコミット済み teams.json からチーム文脈を解決する。"""
    resolved_repo = repo_dir.resolve()
    branch = _get_current_branch_safe(resolved_repo)
    branch_team_id: str | None = None
    if branch.startswith("team/"):
        branch_team_id = branch[len("team/") :]
        if not branch_team_id or not TEAM_ID_PATTERN.match(branch_team_id):
            raise ManifestValidationError(f"Invalid team branch name: '{branch}'")

    if (
        branch_team_id is None
        and explicit_team_id is None
        and explicit_owns is None
    ):
        return None

    manifest = load_committed_teams_manifest(resolved_repo, ref=ref)

    if branch_team_id is not None:
        if manifest is None:
            if (resolved_repo / "teams.json").exists():
                raise ManifestValidationError(
                    f"Branch '{branch}' requires committed teams.json at '{ref}'; "
                    "working-tree teams.json cannot be used"
                )
            if not allow_explicit_without_manifest or (
                explicit_team_id is None and explicit_owns is None
            ):
                return None
        else:
            team = next((t for t in manifest.teams if t.id == branch_team_id), None)
            if team is None:
                raise ManifestValidationError(
                    f"Team '{branch_team_id}' from branch '{branch}' not found in committed teams.json"
                )
            _validate_explicit_against_team(
                resolved_repo,
                team,
                explicit_team_id=explicit_team_id,
                explicit_owns=explicit_owns,
                explicit_plan=explicit_plan,
            )
            return _build_team_context(resolved_repo, team)

    if manifest is not None:
        if explicit_team_id is not None:
            team = next((t for t in manifest.teams if t.id == explicit_team_id), None)
            if team is None:
                raise ManifestValidationError(
                    f"--team-id '{explicit_team_id}' not found in committed teams.json"
                )
            _validate_explicit_against_team(
                resolved_repo,
                team,
                explicit_team_id=explicit_team_id,
                explicit_owns=explicit_owns,
                explicit_plan=explicit_plan,
            )
            return _build_team_context(resolved_repo, team)

        # explicit_owns is set without explicit_team_id when committed teams.json exists
        if explicit_plan is not None:
            plan_rel, _ = _resolve_plan_rel_in_repo(resolved_repo, explicit_plan)
            matching_team = next(
                (t for t in manifest.teams if _normalize_rel_path(t.plan) == plan_rel),
                None,
            )
            if matching_team is not None:
                _validate_explicit_against_team(
                    resolved_repo,
                    matching_team,
                    explicit_team_id=None,
                    explicit_owns=explicit_owns,
                    explicit_plan=explicit_plan,
                )
                return _build_team_context(resolved_repo, matching_team)

        raise ManifestValidationError(
            "--owns without matching --team-id contradicts committed teams.json"
        )

    if not allow_explicit_without_manifest:
        return None

    if explicit_team_id is not None and not TEAM_ID_PATTERN.match(explicit_team_id):
        raise ManifestValidationError(
            f"Invalid --team-id format: '{explicit_team_id}'"
        )
    if explicit_owns is not None and not explicit_owns:
        raise ManifestValidationError("--owns must specify at least one non-empty pattern")

    plan_rel, resolved_plan = _resolve_plan_rel_in_repo(
        resolved_repo, explicit_plan or Path("PLAN.md")
    )
    owns_list = [_normalize_rel_path(p) for p in (explicit_owns or []) if p.strip()]
    if owns_list and not any(match_path(plan_rel, pat) for pat in owns_list):
        owns_list.append(plan_rel)
    return TeamContext(
        team_id=explicit_team_id or "",
        plan_rel_path=plan_rel,
        plan_path=resolved_plan,
        owns_patterns=owns_list,
    )
