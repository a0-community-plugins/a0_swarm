from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers.recovery import restore
import sys


class SwarmRestore(Extension):
    def execute(self, data=None, **kwargs):
        task = (data or {}).get("result")
        if task is not None:
            sys._a0_swarm_chats_restoring = True
            def restored(future):
                future.result()  # A failed restore must not start recovery.
                restore()
                sys._a0_swarm_chats_restoring = False
            task.add_done_callback(restored)
