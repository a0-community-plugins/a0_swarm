from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers.recovery import tick


class SwarmRecovery(Extension):
    async def execute(self, **kwargs):
        tick()
