from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers import checkpoints


class SwarmRecoveryOwner(Extension):
    async def execute(self, data, **kwargs):
        checkpoints.control_exception(self.agent, data)
