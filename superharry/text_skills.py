import re
import tomllib
from pathlib import Path


SKILLS_DIRECTORY = Path(__file__).with_name("skills")


def text_skill(prompt):
    parts = prompt.split(maxsplit=1)
    if not parts or not re.fullmatch(r"/[a-z][a-z0-9-]{0,47}", parts[0]):
        return None
    name = parts[0][1:]
    from .models import VmSkill
    if VmSkill.objects.filter(name=name).exists():
        raise ValueError("This skill name conflicts with a VM skill.")
    try:
        definition = (SKILLS_DIRECTORY / f"{name}.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError("Unknown text skill.") from None
    except (OSError, UnicodeError):
        raise ValueError("The text skill is unavailable.") from None
    try:
        prefix, metadata, instructions = definition.split("+++", 2)
        manifest = tomllib.loads(metadata)
        if (
            prefix.strip()
            or not isinstance(manifest.get("purpose"), str)
            or not manifest["purpose"].strip()
            or manifest.get("allowed_tools") != []
            or not instructions.strip()
        ):
            raise ValueError
    except ValueError:
        raise ValueError("The text skill is not configured for tool-free execution.") from None
    if len(parts) < 2:
        raise ValueError(f"Enter text after /{name}.")
    return instructions.strip(), parts[1]
