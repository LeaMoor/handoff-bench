"""What the orchestrator gets back from `delegate` (tasks 9, 16). A renderer sees the subagent's
trace and its own calls from the call journal, never the effect journal.

To add a channel, add an entry to RENDERERS; `cells.py` already names all of them."""

from handoff_bench.agents.delegation import Renderer, subagent_words
from handoff_bench.cells import Channel

RENDERERS: dict[Channel, Renderer] = {"text": lambda child, calls: subagent_words(child)}
