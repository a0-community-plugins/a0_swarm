from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers import checkpoints


class SwarmModelReceipt(Extension):
    async def execute(self, result_data, **kwargs):
        await checkpoints.model_result(self.agent, result_data)
