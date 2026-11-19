from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers import checkpoints


class SwarmDispatchIntent(Extension):
    def execute(self, **kwargs):
        checkpoints.invalidate(self.agent)
