from helpers.extension import Extension
from usr.plugins.a0_swarm.hooks import _stop_workers


class SwarmQuiesce(Extension):
    async def execute(self, data=None, **kwargs):
        values = (data or {}).get("kwargs", {})
        args = (data or {}).get("args", ())
        name = values.get("plugin_name", args[0] if args else "")
        enabled = values.get("enabled", args[1] if len(args) > 1 else None)
        if name == "a0_swarm" and enabled is False:
            _stop_workers()
