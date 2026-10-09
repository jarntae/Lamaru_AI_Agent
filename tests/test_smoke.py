import lamaru


def test_package_import():
    assert lamaru.__version__ == "0.1.0"


def test_environment_smoke():
    assert True
