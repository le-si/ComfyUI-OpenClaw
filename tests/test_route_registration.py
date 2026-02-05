import unittest
from unittest.mock import MagicMock

# from aiohttp import web # Removed for CI compatibility (no extra deps)
from api.routes import register_dual_route


class TestRouteRegistration(unittest.TestCase):
    def test_dual_registration(self):
        # Mock Server
        server = MagicMock()
        server.routes = MagicMock()
        server.app = MagicMock()  # Mock the whole app
        server.app.router = MagicMock()
        server.app.router.add_route = MagicMock()

        # Test Handler (dummy)
        async def handler(req):
            return "response"

        # Call function
        register_dual_route(server, "GET", "/moltbot/test", handler)

        # 1. Verify standard PromptServer.routes usage
        server.routes.get.assert_called_with("/moltbot/test")

        # 2. Verify fallback app.router usage (legacy + shim)
        # Note: add_route calls might happen in any order
        calls = server.app.router.add_route.call_args_list
        # Expect ("GET", "/moltbot/test", handler) and ("GET", "/api/moltbot/test", handler)

        paths_registered = [c.args[1] for c in calls]
        self.assertIn("/moltbot/test", paths_registered)
        self.assertIn("/api/moltbot/test", paths_registered)


if __name__ == "__main__":
    unittest.main()
