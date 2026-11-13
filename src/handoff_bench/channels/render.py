"""What the orchestrator gets back from `delegate` (tasks 9, 16). A renderer sees the subagent's
trace, its own calls from the call journal and whether its report was lost; never the effects.

To add a channel, add an entry to RENDERERS; `cells.py` already names all of them."""

from handoff_bench.agents.delegation import Renderer, words
from handoff_bench.cells import Channel

RENDERERS: dict[Channel, Renderer] = {"text": words}
