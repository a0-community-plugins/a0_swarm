from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers import checkpoints


class SwarmCommandCheckpoint(Extension):
    async def execute(self, tool_name="", tool_args=None, **kwargs):
        await checkpoints.before_tool(self.agent, tool_name, tool_args or {})
