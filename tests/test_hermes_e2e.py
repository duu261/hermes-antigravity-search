import importlib.util
import unittest

HERMES_AVAILABLE = importlib.util.find_spec("hermes_cli") is not None

try:
    from .hermes_e2e_runner import main
except ImportError:
    from hermes_e2e_runner import main


class HermesRuntimeE2ETest(unittest.TestCase):
    @unittest.skipUnless(HERMES_AVAILABLE, "Hermes runtime is not installed")
    def test_native_web_search_dispatch_uses_plugin(self):
        main()


if __name__ == "__main__":
    unittest.main()
