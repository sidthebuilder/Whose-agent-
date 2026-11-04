"""The Switch room template and the agents' instructions must agree with the code they describe."""
import re

import pytest

from whoseagent import paths
from whoseagent.cli import build_parser

yaml = pytest.importorskip("yaml")
TEMPLATE = paths.ROOT / "switch" / "whose-agent.template.yaml"
BUILTINS = {"$creator", "$creator_email", "$date", "$timestamp", "$agents_dir", "agent"}
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@pytest.fixture(scope="module")
def doc():
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def strings(node, skip=("pattern",)):
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in skip:
                yield from strings(v, skip)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v, skip)
    elif isinstance(node, str):
        yield node


def fill(text, doc):
    return re.sub(r"\{([a-z_]+)\}", lambda m: str(doc["params"].get(m.group(1), {}).get("default", m.group(0))), text)


def test_every_placeholder_is_a_param_or_a_builtin(doc):
    used = {m for s in strings(doc) for m in re.findall(r"\{([^{}\s]+)\}", s)}
    assert used - set(doc["params"]) - BUILTINS == set()


def test_agents_room_and_kickoff_agree(doc):
    names = [a["name"] for a in doc["agents"]]
    assert doc["room"]["agents"] == names
    for n in names:
        assert NAME.match(fill(n, doc)), n
        assert "@" + n in doc["kickoff"]
    for a in doc["agents"]:
        assert a["provider"] and a["location"] and a["directory"] and a["instructions"]


def test_each_agent_folder_exists_with_its_instructions(doc):
    for a in doc["agents"]:
        folder = paths.ROOT / a["directory"].replace("{repo_dir}/", "")
        assert (folder / "AGENTS.md").exists() and (folder / "CLAUDE.md").exists()
        assert (folder / ".." / ".." / "wa.py").resolve() == (paths.ROOT / "wa.py").resolve()


def test_every_command_the_agents_are_told_to_run_exists(doc):
    sub = next(a for a in build_parser()._actions if a.dest == "cmd")
    texts = list(strings(doc)) + [p.read_text(encoding="utf-8") for p in (paths.ROOT / "agents").glob("*/AGENTS.md")]
    told = {m for t in texts for m in re.findall(r"wa\.py (\w+)", t)}
    assert told and told <= set(sub.choices), told - set(sub.choices)
