from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers import checkpoints


class SwarmModelCheckpoint(Extension):
    async def execute(self, call_data, **kwargs):
        await checkpoints.before_model(self.agent, call_data)
