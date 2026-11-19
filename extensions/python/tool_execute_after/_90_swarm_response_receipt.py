from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers import checkpoints


class SwarmResponseReceipt(Extension):
    async def execute(self, response=None, tool_name="", **kwargs):
        await checkpoints.after_tool(self.agent, response, tool_name)
