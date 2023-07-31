from registries import callbacks_registry

APPS = (
    "app1",
    "app2",
)

# Trigger autodiscovering process
callbacks_registry.autodiscover(APPS)

for callback in callbacks_registry.values():
    callback()

    # Wouf
    # Meow
