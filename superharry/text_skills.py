import re
import tomllib
from pathlib import Path
import yaml


SKILLS_DIRECTORY = Path(__file__).with_name("skills")


def repository_path(root, name):
    modern = root / name / "SKILL.md"
    legacy = root / f"{name}.md"
    return modern if modern.is_file() else legacy


def repository_definitions(root):
    names = {path.stem for path in root.glob("*.md")}
    names.update(path.parent.name for path in root.glob("*/SKILL.md"))
    return {name: repository_path(root, name) for name in sorted(names)}


def parse_definition(path):
    definition = path.read_text(encoding="utf-8")
    if len(definition) > 256 * 1024:
        raise ValueError("Skill definition is too large.")
    if path.name == "SKILL.md":
        prefix, metadata, instructions = definition.split("---", 2)
        try:
            manifest = yaml.safe_load(metadata)
        except yaml.YAMLError:
            raise ValueError("Invalid skill metadata.") from None
        if not isinstance(manifest, dict) or manifest.get("name") != path.parent.name:
            raise ValueError("Invalid skill metadata.")
        purpose = manifest.get("description")
        allowed = manifest.get("allowed-tools", [])
    else:
        prefix, metadata, instructions = definition.split("+++", 2)
        manifest = tomllib.loads(metadata)
        purpose = manifest.get("purpose")
        allowed = manifest.get("allowed_tools")
    if prefix.strip() or not isinstance(purpose, str) or not purpose.strip() or not instructions.strip():
        raise ValueError("Invalid skill definition.")
    return {"purpose": purpose, "allowed_tools": allowed, "instructions": instructions.strip()}


def text_skill(prompt):
    parts = prompt.split(maxsplit=1)
    if not parts or not re.fullmatch(r"/[a-z][a-z0-9-]{0,47}", parts[0]):
        return None
    name = parts[0][1:]
    from .models import VmSkill
    if VmSkill.objects.filter(name=name).exists():
        raise ValueError("This skill name conflicts with a VM skill.")
    try:
        definition = parse_definition(repository_path(SKILLS_DIRECTORY, name))
    except FileNotFoundError:
        raise ValueError("Unknown text skill.") from None
    except (OSError, UnicodeError):
        raise ValueError("The text skill is unavailable.") from None
    try:
        if definition["allowed_tools"] != []:
            raise ValueError
    except ValueError:
        raise ValueError("The text skill is not configured for tool-free execution.") from None
    if len(parts) < 2:
        raise ValueError(f"Enter text after /{name}.")
    return definition["instructions"], parts[1]
