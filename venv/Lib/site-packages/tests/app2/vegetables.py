from ..test_registries import vegetable_registry


@vegetable_registry.register
class Ketchup:
    warning = "ketchup is not a vegetable"
