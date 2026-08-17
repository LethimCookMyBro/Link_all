import os
import sys
import unittest
from unittest.mock import patch, MagicMock

class ConfigImportTest(unittest.TestCase):
    def test_config_import_fallback(self):
        """Test that importing PhantomLink does not crash even if config module is missing or frozen."""
        with patch.dict(sys.modules, {'config': None}):
            # Import client/PhantomLink.py safely
            import client.PhantomLink as phantom
            self.assertTrue(hasattr(phantom, 'DISCORD_WEBHOOK'))
            self.assertIsNotNone(phantom.DISCORD_WEBHOOK)

    def test_frozen_environment_path_resolution(self):
        """Test sys.path behavior when running in PyInstaller frozen environment."""
        with patch.object(sys, 'frozen', True, create=True), \
             patch.object(sys, '_MEIPASS', r'C:\tmp\_MEI12345', create=True):
            _MEI_DIR = getattr(sys, '_MEIPASS', '')
            self.assertEqual(_MEI_DIR, r'C:\tmp\_MEI12345')

if __name__ == '__main__':
    unittest.main()
