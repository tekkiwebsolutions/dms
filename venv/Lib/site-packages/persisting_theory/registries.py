from collections import OrderedDict
import inspect
import operator

import importlib


class DoesNotExist(ValueError):
    pass


class MultipleObjectsReturned(ValueError):
    pass


class QuerySet(object):
    def __init__(self, values):
        self.values = list(values)

    def __iter__(self):
        for value in self.values:
            yield value

    def __len__(self):
        return len(self.values)

    def __repr__(self):
        return "<{0}: {1}>".format(self.__class__.__name__, str(list(self.values)))

    def __getitem__(self, i):
        return self.values[i]

    def __eq__(self, other):
        return self.values == list(other)

    def _clone(self, new_values):
        return self.__class__(new_values)

    def _build_filter(self, **kwargs):
        """build a single filter function used to match arbitrary object"""

        def object_filter(obj):
            for key, value in kwargs.items():
                # we replace dango-like lookup by dots, so attrgetter can do his job
                key = key.replace("__", ".")

                getter = operator.attrgetter(key)
                if not getter(obj) == value:
                    return False
            return True

        return object_filter

    def exists(self):
        return len(self) > 0

    def order_by(self, key):
        reverse = False
        if key.startswith("-"):
            reverse = True
            key = key[1:]

        return self._clone(
            sorted(self.values, key=operator.attrgetter(key), reverse=reverse)
        )

    def all(self):
        return self._clone(self.values)

    def count(self):
        return len(self)

    def first(self):
        try:
            return self.values[0]
        except IndexError:
            return None

    def last(self):
        try:
            return self.values[-1]
        except IndexError:
            return None

    def filter(self, **kwargs):
        _filter = self._build_filter(**kwargs)
        return self._clone(filter(_filter, self.values))

    def exclude(self, **kwargs):
        _filter = self._build_filter(**kwargs)
        return self._clone(filter(lambda v: not _filter(v), self.values))

    def get(self, **kwargs):

        matches = self.filter(**kwargs)
        if len(matches) == 0:
            raise DoesNotExist()
        if len(matches) > 1:
            raise MultipleObjectsReturned()
        return matches[0]


class Manager(object):
    """Used to retrieve / order / filter preferences pretty much as django's ORM managers"""

    def __init__(self, registry, queryset_class):
        self.registry = registry
        self.queryset_class = queryset_class

    def get_queryset(self):
        return self.queryset_class(self.registry.values())

    def all(self):
        return self.get_queryset().all()

    def __getattr__(self, attr):
        try:
            return super().__getattr__(attr)
        except AttributeError:
            # Try to proxy on queryset if possible
            return getattr(self.get_queryset(), attr)


class Registry(OrderedDict):
    manager_class = Manager
    queryset_class = QuerySet

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.objects = self.manager_class(self, self.queryset_class)

    def register_decorator_factory(self, **kwargs):
        """
        Return an actual decorator for registering objects into registry
        """
        name = kwargs.get("name")

        def decorator(decorated):
            self.register_func(data=decorated, name=name)
            return decorated

        return decorator

    def register(self, data=None, name=None, **kwargs):
        """
        Use this method as a decorator on class/function you want to register:

        @registry.register(name="test")
        class Test:
            pass

        :param:data: Something to register in the registry
        :param:name: The unique name that will identify registered data.
        If None, by default, registry will try to deduce name from class name (if object is a class or an object).
        You can change this behaviour by overriding :py::method:`prepare_name`

        """
        if data is None:
            return self.register_decorator_factory(data=data, name=name, **kwargs)
        else:
            self.register_func(data=data, name=name, **kwargs)
            return data

    def get_object_name(self, data):
        """
        Return a name from an element (object, class, function...)
        """
        if callable(data):
            return data.__name__

        elif inspect.isclass(data):
            return data.__class__.__name__

        else:
            raise ValueError(
                "Cannot deduce name from given object ({0}). Please user registry.register() with a 'name' argument.".format(
                    data
                )
            )

    def validate(self, data):
        """
        Called before registering a new value into the registry
        Override this method if you want to restrict what type of data cna be registered
        """
        return True

    def prepare_name(self, data, name=None):
        if name is None:
            return self.get_object_name(data)
        return name

    def register_func(self, data, name=None, **kwargs):
        """
        Register abritrary data into the registry
        """
        if self.validate(data):
            o = self.prepare_data(data)
            n = self.prepare_name(data, name)
            self[n] = o
            self.post_register(data=0, name=n)
        else:
            raise ValueError(
                "{0} (type: {0.__class__}) is not a valid value for {1} registry".format(
                    data, self.__class__
                )
            )

    def post_register(self, data, name):
        """
        Will be triggered each time a new element is successfully registered.
        Feel free to override this method
        """
        pass

    def prepare_data(self, data):
        """
        Override this methode if you want to manipulate data before registering it
        You MUST return a value to register
        """
        return data

    def autodiscover(self, apps, force_reload=False):
        """
        Iterate throught every installed apps, trying to import `look_into` package
        :param apps: an iterable of string, refering to python modules the registry will try to import via autodiscover
        """
        for app in apps:
            app_package = __import__(app)
            try:

                package = "{0}.{1}".format(
                    app, self.look_into
                )  # try to import self.package inside current app
                # print(package)
                module = __import__(package)
                if force_reload:
                    importlib.reload(module)
            except ImportError as exc:
                # From django's syncdb
                # This is slightly hackish. We want to ignore ImportErrors
                # if the module itself is missing -- but we don't
                # want to ignore the exception if the module exists
                # but raises an ImportError for some reason. The only way we
                # can do this is to check the text of the exception. Note that
                # we're a bit broad in how we check the text, because different
                # Python implementations may not use the same text.
                # CPython uses the text "No module named"
                # PyPy uses "No module named myproject.myapp"
                msg = exc.args[0]
                if not msg.startswith("No module named") or self.look_into not in msg:
                    raise


class MetaRegistry(Registry):
    """
    Keep a reference to all registries
    """

    look_into = "registries"

    def autodiscover(self, apps, cascade=True, **kwargs):
        """
        :param cascade: If true, will trigger autodiscover on discovered registries
        """
        super().autodiscover(apps, **kwargs)
        if cascade:
            self.autodiscover_registries(apps)

    def autodiscover_registries(self, apps):
        for key, registry in self.items():
            registry.autodiscover(apps)


meta_registry = MetaRegistry()
