"""Installer helper dispatch must never start the environment coordinator."""
import unittest
from unittest.mock import patch

from envmanager_v3 import __main__ as launch


class UpgradeDispatchTests(unittest.TestCase):
    def test_helper_dispatch_preserves_arguments_and_exit_status(self):
        arguments = ["status", "--session-dir", "session path"]
        with patch("envmanager_v3.upgrade.main", return_value=7) as helper, \
                patch.object(launch, "WindowsEnvironment") as windows, \
                patch.object(launch, "FileEnvironment") as file, \
                patch.object(launch, "Service") as service, \
                patch.object(launch, "create_server") as server:
            self.assertEqual(launch.main(["--upgrade-helper", *arguments]), 7)
        helper.assert_called_once_with(arguments)
        for factory in (windows, file, service, server):
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
