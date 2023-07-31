from ..test_registries import vegetable_registry


@vegetable_registry.register
class Potato:
    pass
