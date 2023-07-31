import os, sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from persisting_theory import Registry


class CallbacksRegistry(Registry):
    """
    Allow your apps to register callbacks
    """

    # the package where the registry will try to find callbacks in each app
    look_into = "callbacks_registry"


callbacks_registry = CallbacksRegistry()

APPS = (
    "app1",
    "app2",
)
# Trigger autodiscovering process
callbacks_registry.autodiscover(APPS)
