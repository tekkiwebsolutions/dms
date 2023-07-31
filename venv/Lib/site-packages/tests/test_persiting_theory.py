import pytest

from . import test_registries
import persisting_theory

persisting_theory.meta_registry.look_into = "test_registries"

TEST_APPS = (
    "tests.app1",
    "tests.app2",
)


def test_can_infer_name_from_class_function_and_instance():
    registry = persisting_theory.Registry()

    def something():
        pass

    class MyClass:
        pass

    assert registry.get_object_name(something) == "something"
    assert registry.get_object_name(MyClass) == "MyClass"

    with pytest.raises(ValueError):
        assert registry.get_object_name(MyClass()) == "MyClass"


def test_can_register_data_to_registry():

    data = "something"
    registry = persisting_theory.Registry()
    registry.register(data, name="key")

    assert len(registry) == 1
    assert registry.get("key") == data


def test_can_restric_registered_data():
    class RestrictedRegistry(persisting_theory.Registry):
        def validate(self, obj):
            """Only accept integer values"""
            return isinstance(obj, int)

    registry = RestrictedRegistry()

    registry.register(12, name="twelve")
    with pytest.raises(ValueError):
        registry.register("not an int", name="not an int")


def test_can_register_class_and_function_via_decorator():
    registry = persisting_theory.Registry()

    @registry.register
    class ToRegister:
        pass

    assert registry.get("ToRegister") == ToRegister

    @registry.register
    def something():
        pass

    assert registry.get("something") == something


def test_can_register_via_decorator_using_custom_name():
    registry = persisting_theory.Registry()

    @registry.register(name="custom_name")
    def something():
        pass

    assert registry.get("custom_name") == something


def test_meta_registry_can_autodiscovering_registries_and_trigger_their_autodiscover_method():

    registry = persisting_theory.meta_registry
    registry.autodiscover(apps=TEST_APPS)

    assert len(registry) == 2
    assert registry.get("awesome_people") == test_registries.awesome_people
    assert registry.get("vegetable_registry") == test_registries.vegetable_registry

    registry = test_registries.awesome_people
    assert len(registry) == 2
    assert registry.get("AlainDamasio", None) is not None
    assert registry.get("FrederikPeeters", None) is not None

    registry = test_registries.vegetable_registry
    assert len(registry) == 2
    assert registry.get("Potato", None) is not None
    assert registry.get("Ketchup", None) is not None


def test_registry_can_autodiscover():

    registry = test_registries.awesome_people
    registry.autodiscover(apps=TEST_APPS)

    assert len(registry) == 2
    assert registry.get("AlainDamasio", None) is not None
    assert registry.get("FrederikPeeters", None) is not None

    registry.clear()


def test_autodiscover_raises_an_error_if_there_is_an_error_in_imported_module():
    with pytest.raises(NameError):
        registry = test_registries.awesome_people
        registry.autodiscover(apps=("tests.buggy_app",))


def test_can_manipulate_data_before_registering():
    class ModifyData(persisting_theory.Registry):
        def prepare_data(self, data):
            return "hello " + data

    r = ModifyData()

    r.register("agate", name="agate")
    r.register("roger", name="roger")

    assert r.get("agate") == "hello agate"
    assert r.get("roger") == "hello roger"


def test_can_manipulate_key_before_registering():
    class ModifyKey(persisting_theory.Registry):
        def prepare_name(self, data, key=None):
            return "custom_key " + data.first_name

    r = ModifyKey()

    class N:
        def __init__(self, first_name):
            self.first_name = first_name

    n1 = N(first_name="agate")
    n2 = N(first_name="alain")
    r.register(n1)
    r.register(n2)

    assert r.get("custom_key agate") == n1
    assert r.get("custom_key alain") == n2


def test_can_post_register_triggers_correctly():
    class PostRegisterException(Exception):
        pass

    class PostRegister(persisting_theory.Registry):
        def post_register(self, data, name):
            raise PostRegisterException("Post register triggered")

    r = PostRegister()

    with pytest.raises(PostRegisterException):
        r.register("hello", name="world")


class FakeObject(object):
    def __init__(self, name, **kwargs):
        self.name = name
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        return self.name


@pytest.fixture
def parents():
    return [
        FakeObject(name="parent_1"),
        FakeObject(name="parent_2"),
    ]


@pytest.fixture
def objects(parents):
    return [
        FakeObject(name="test_1", order=2, a=1, parent=parents[0]),
        FakeObject(name="test_2", order=3, a=1, parent=parents[0]),
        FakeObject(name="test_3", order=1, a=2, parent=parents[1]),
        FakeObject(name="test_4", order=4, a=2, parent=parents[1]),
    ]


@pytest.fixture
def registry(objects):
    class R(persisting_theory.Registry):
        def prepare_name(self, data, key=None):
            return data.name

    registry = R()
    for o in objects:
        registry.register(o)

    return registry


def test_default_order(registry, objects):
    assert list(registry.objects.all()) == objects


def test_can_get_using_attribute(registry, objects):
    assert registry.objects.get(name="test_1") == objects[0]


def test_can_filter(registry, objects):
    assert registry.objects.filter(a=1) == objects[:2]


def test_can_combine_filters(registry, objects):
    assert registry.objects.filter(a=1, name="test_2") == objects[1:2]
    assert registry.objects.filter(a=1).filter(name="test_2") == objects[1:2]


def test_related_lookups(registry, objects):
    assert registry.objects.filter(parent__name="parent_1") == objects[:2]
    assert registry.objects.exclude(parent__name="parent_1") == objects[2:]
    assert registry.objects.get(parent__name="parent_1", order=2) == objects[0]


def test_can_exclude(registry, objects):
    assert registry.objects.exclude(a=1) == objects[2:]


def test_can_combine_exclude(registry, objects):
    assert registry.objects.exclude(a=1).exclude(name="test_4") == objects[2:3]
    assert registry.objects.exclude(a=2, name="test_4") == objects[:3]


def test_can_count(registry):
    assert registry.objects.filter(a=1).count() == 2


def test_first(registry):
    assert registry.objects.filter(a=123).first() is None
    assert registry.objects.filter(a=1).first() is not None


def test_ordering(registry, objects):
    assert registry.objects.order_by("order")[:2] == [objects[2], objects[0]]
    assert registry.objects.order_by("-order")[:2] == [objects[3], objects[1]]


def test_last(registry):
    assert registry.objects.filter(a=123).last() is None
    assert registry.objects.filter(a=1).last() is not None


def test_exists(registry):
    assert registry.objects.filter(a=123).exists() is False
    assert registry.objects.filter(a=1).exists() is True


def test_get_raise_exception_on_multiple_objects_returned(registry):
    with pytest.raises(persisting_theory.MultipleObjectsReturned):
        registry.objects.get(a=1)


def test_get_raise_exception_on_does_not_exist(registry):
    with pytest.raises(persisting_theory.DoesNotExist):
        registry.objects.get(a=123)
