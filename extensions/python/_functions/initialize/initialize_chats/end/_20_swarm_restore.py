from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers.recovery import restore


class SwarmRestore(Extension):
    def execute(self, data=None, **kwargs):
        task = (data or {}).get("result")
        if task is not None:
            def restored(future):
                future.result()  # A failed restore must not start recovery.
                restore()
            task.add_done_callback(restored)
