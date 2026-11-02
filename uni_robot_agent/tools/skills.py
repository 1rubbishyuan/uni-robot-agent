"""Read shared and environment skills; write only environment-local experience."""
import hashlib
import os
from uuid import uuid4
from pathlib import Path

from uni_robot_agent.execution.scheduler import ToolExecutionPolicy
from uni_robot_agent.services.environment.contracts import RequestRejected
from uni_robot_agent.tools.toolkit import ToolOutput, ToolProvider, ToolSpec, bind_tools, schema

RESOURCES = Path(__file__).resolve().parents[1] / "resources"
NAMES = ("list_skills", "read_skill")


def _inside_package(path):
    return (path.is_relative_to(Path(__file__).resolve().parents[1])
            or path.is_relative_to(RESOURCES.resolve()))


def skill_root(*, writing=False):
    configured = os.environ.get("UNI_ROBOT_AGENT_SKILLS_DIR")
    root = (Path(configured).expanduser() if configured
            else RESOURCES / "skills").resolve()
    if writing and (not configured or _inside_package(root)):
        raise ValueError("Skill writing requires UNI_ROBOT_AGENT_SKILLS_DIR to point to a complete skills library outside the installed package")
    return root


def tool_policies():
    return {"list_skills": ToolExecutionPolicy(resource_key="skills", concurrency="parallel"),
            "read_skill": ToolExecutionPolicy(resource_key="skills", concurrency="parallel"),
            "write_skill": ToolExecutionPolicy(resource_key="skills", concurrency="exclusive")}


def tool_specs(skill_namespace, *, skill_writing=False):
    specs = [
        ToolSpec("list_skills",
            f"List sorted recursive Markdown paths in entries under general/ and {skill_namespace}/ in this session's skills library, including all skill subdirectories. The paths field identifies the searched scopes. Takes no arguments and does not read file contents or advance physics. Pass a returned path as the path argument to the skill-file reader declared in this session for guidance.",
            schema({}), True),
        ToolSpec("read_skill",
            f"Read a Markdown file within general/ or {skill_namespace}/ in this session's skills library and return content and sha256. Use the skill-path listing tool declared in this session to discover available paths. Does not advance physics.",
            schema({"path": {"type": "string", "minLength": 1}}, ("path",)), True)
    ]
    if skill_writing:
        specs.append(ToolSpec("write_skill",
            f"Write or update a UTF-8 Markdown skill within the {skill_namespace}/ subdirectory of this session's skills library. The shared general/ directory is read-only. Does not advance physics.",
            schema({"path": {"type": "string", "minLength": 1},
                    "content": {"type": "string", "minLength": 1}}, ("path", "content"))))
    return specs


class SkillTools(ToolProvider):
    provider_id = "skills"

    def __init__(self, episode, *, writing=False):
        self.episode = episode
        self.skill_writing = writing
        self.namespace = episode.description.metadata.get("skill_namespace", episode.description.integration)
        self.root = skill_root(writing=writing)

    def get_tools(self):
        return self._bind_tools(skill_writing=self.skill_writing)

    def get_declared_tools(self):
        return self._bind_tools(skill_writing=True)

    def _bind_tools(self, *, skill_writing):
        return bind_tools(tool_specs(self.namespace, skill_writing=skill_writing), provider_id=self.provider_id,
                          handlers={"list_skills": self.list_skills, "read_skill": self.read_skill,
                                    "write_skill": self.write_skill}, invoke=self.episode.invoke)

    def _namespace_paths(self):
        paths = {}
        for name in dict.fromkeys(("general", self.namespace)):
            namespace = Path(name)
            allowed = (self.root / namespace).resolve()
            if (namespace.is_absolute() or ".." in namespace.parts or namespace == Path(".")
                    or not allowed.is_relative_to(self.root) or allowed != self.root / namespace):
                raise RequestRejected("Skill namespace must remain inside this session's skills library")
            paths[name] = allowed
        return paths

    def _skill_path(self, path, *, writing=False):
        namespaces = self._namespace_paths()
        requested = Path(path)
        candidate = (self.root / requested).resolve()
        if writing and (requested.is_relative_to(Path("general"))
                        or not requested.is_relative_to(Path(self.namespace))):
            raise RequestRejected("Skill writing is limited to this episode's namespace; general/ is read-only")
        if (requested.is_absolute() or ".." in requested.parts or requested.suffix != ".md"
                or candidate.suffix != ".md"
                or not any(requested.is_relative_to(Path(name)) and candidate.is_relative_to(allowed)
                           for name, allowed in namespaces.items())):
            raise RequestRejected("Skill path must remain inside general/ or this episode's Markdown skill namespace")
        return candidate

    def list_skills(self):
        namespaces = self._namespace_paths()
        entries = set()
        try:
            # Do not follow directory symlinks or let file aliases cross a scope boundary.
            for namespace in namespaces.values():
                for entry in namespace.rglob("*.md"):
                    resolved = entry.resolve()
                    if (entry.is_file() and resolved.suffix == ".md" and resolved.is_relative_to(namespace)
                            and os.access(entry, os.R_OK)):
                        entries.add(entry.relative_to(self.root).as_posix())
        except OSError as error:
            raise RequestRejected(f"Cannot list skills: {error}") from error
        return ToolOutput(extra={"paths": list(namespaces), "entries": sorted(entries)})

    def read_skill(self, path):
        candidate = self._skill_path(path)
        try:
            data = candidate.read_bytes()
            content = data.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise RequestRejected(f"Cannot read skill {path!r}: {error}") from error
        return ToolOutput(extra={"path": candidate.relative_to(self.root).as_posix(), "content": content,
                                 "sha256": hashlib.sha256(data).hexdigest()})

    def write_skill(self, path, content):
        if not self.skill_writing:
            raise RequestRejected("Skill writing is disabled for this session")
        candidate = self._skill_path(path, writing=True)
        if _inside_package(candidate):
            raise RequestRejected("Bundled skills are read-only; configure a skills library outside the installed package")
        data = content.encode("utf-8")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        temporary = candidate.with_name(f".{candidate.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, candidate)
        finally:
            temporary.unlink(missing_ok=True)
        return ToolOutput(extra={"path": candidate.relative_to(self.root).as_posix(),
                                 "bytes_written": len(data), "sha256": hashlib.sha256(data).hexdigest()})
