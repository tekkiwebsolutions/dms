from registries import callbacks_registry


@callbacks_registry.register
def dog():
    print("Wouf")
