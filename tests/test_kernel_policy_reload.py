import asyncio

from aitos.eventbus.redis_bus import EventBus
from aitos.journal.policy_registry import PolicyRegistry
from aitos.kernel.ai_kernel import AIKernel


def test_kernel_loads_persisted_policy_on_initialize(tmp_path):
    async def run():
        registry = PolicyRegistry(str(tmp_path / "active.json"), {"a": 1.0})
        registry.activate("v2", {"a": 1.0}, 0.7)
        kernel = AIKernel(EventBus(), policy_registry=registry)
        await kernel.initialize({})
        assert kernel.policy_version == "v2"
        assert kernel.fusion_min_confidence == 0.7
        assert kernel.fusion_weights == {"a": 1.0}
    asyncio.run(run())
