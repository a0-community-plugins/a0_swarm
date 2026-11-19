from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers import checkpoints


class SwarmCachedModelResult(Extension):
    async def execute(self, data, **kwargs):
        checkpoints.cached_model(self.agent, data)
