from registries import callbacks_registry


@callbacks_registry.register
def cat():
    print("Meow")
