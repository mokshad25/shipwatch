"""Versioned prompts for the PR risk agent (docs/EVALS.md). One markdown file per version.

Each file holds a `# System prompt` section and a `# Final instruction` section. Kept stable (no
timestamps or ids) so providers can cache them. Change a prompt by adding a version, so evals can
compare the two; PROMPT_VERSION picks the one the worker uses.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
DEFAULT_VERSION = "v1"
_SYSTEM = "# System prompt\n"
_FINAL = "\n# Final instruction\n"


class UnknownPromptVersion(ValueError):
    pass


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str  # the agent's system prompt, in both phases
    final: str  # appended to the evidence for the structured assessment call


def versions() -> list[str]:
    return sorted(p.stem for p in PROMPTS_DIR.glob("v*.md"))


@lru_cache
def load_prompt(version: str = DEFAULT_VERSION) -> Prompt:
    path = PROMPTS_DIR / f"{version}.md"
    if not path.is_file():
        raise UnknownPromptVersion(f"no prompt {version!r}; have {', '.join(versions())}")
    text = path.read_text()
    if not text.startswith(_SYSTEM) or text.count(_FINAL) != 1:
        raise UnknownPromptVersion(f"{path.name}: needs a system prompt, then a final instruction")
    system, final = text.removeprefix(_SYSTEM).split(_FINAL)
    return Prompt(version=version, system=system.strip(), final=final.strip())
