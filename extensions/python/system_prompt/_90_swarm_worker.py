from helpers.extension import Extension
from usr.plugins.a0_swarm.helpers import checkpoints


class SwarmWorkerRole(Extension):
    async def execute(self, system_prompt, **kwargs):
        owner = checkpoints.owned(self.agent)
        if owner:
            _, entry = owner
            system_prompt.append(
                f"Current execution role: local A0 Swarm worker {entry.agent_name}. "
                "You are fulfilling the delegated assignment in your own worker context. "
                "Generic main-agent wording describes your local agent loop; your role in this run is a worker. "
                "You may use swarm_execute for finite local commands and swarm_message for run communication. "
                "Do not start nested orchestration. Reuse recorded results and respond when the assignment is complete."
            )
