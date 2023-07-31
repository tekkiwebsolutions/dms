from persisting_theory import Registry, meta_registry


class AwesomePeopleRegistry(Registry):
    look_into = "awesome_people"


awesome_people = AwesomePeopleRegistry()
meta_registry.register(awesome_people, name="awesome_people")


class VegetableRegistry(Registry):
    look_into = "vegetables"


vegetable_registry = VegetableRegistry()
meta_registry.register(vegetable_registry, name="vegetable_registry")
