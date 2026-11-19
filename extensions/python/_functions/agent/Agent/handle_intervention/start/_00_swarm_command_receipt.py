from helpers.extension import Extension


class SwarmCommandReceiptBeforeIntervention(Extension):
    async def execute(self, data, **kwargs):
        tool = getattr(getattr(self.agent, "loop_data", None), "current_tool", None)
        if tool is not None and getattr(tool, "_swarm_defer_intervention", False):
            # Complete the accepted command's history receipt before consuming
            # a queued intervention. Explicit task cancellation still applies.
            await self.agent.wait_if_paused()
            data["result"] = None
