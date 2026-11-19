from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers import checkpoints


class SwarmWarmStart(Extension):
    async def execute(self, **kwargs):
        checkpoints.restore_loop(self.agent)
