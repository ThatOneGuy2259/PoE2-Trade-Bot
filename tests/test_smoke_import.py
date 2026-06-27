def test_package_imports():
    import poe2bot
    assert hasattr(poe2bot, "__version__")
