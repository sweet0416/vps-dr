import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('latest', ROOT/'scripts/latest.py')
latest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latest)


class LatestTests(unittest.TestCase):
    def test_download_main_even_from_an_installed_checkout(self):
        script = '#!/usr/bin/env bash\necho isolated-test\n'
        with patch.object(latest.os, 'geteuid', return_value=0), \
                patch.object(latest, 'fetch', side_effect=['1.2.0\n', script]) as fetch, \
                patch.object(latest.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                patch.dict(os.environ, {'VPS_DR_REF':'v1.1.0'}):
            self.assertEqual(latest.management(), 0)
        self.assertEqual(fetch.call_args_list[0].args[0], 'VERSION')
        self.assertEqual(fetch.call_args_list[1].args[0], 'bootstrap.sh')
        self.assertEqual(run.call_args.args[0], ['bash','-s','--','--upgrade-management'])
        self.assertEqual(run.call_args.kwargs['input'], script)
        self.assertEqual(run.call_args.kwargs['env']['VPS_DR_REF'], 'main')

    def test_remote_failure_does_not_execute_any_script(self):
        for value in ('unknown','1.2.0\nmalicious'):
            with self.subTest(value=value), patch.object(latest.os,'geteuid',return_value=0), \
                    patch.object(latest,'fetch',return_value=value), patch.object(latest.subprocess,'run') as run:
                with self.assertRaises(ValueError):
                    latest.management()
                run.assert_not_called()

    def test_upgrade_error_is_returned(self):
        with patch.object(latest.os,'geteuid',return_value=0), \
                patch.object(latest,'fetch',side_effect=['1.2.0','#!/usr/bin/env bash\n']), \
                patch.object(latest.subprocess,'run',return_value=subprocess.CompletedProcess([],7)):
            self.assertEqual(latest.management(),7)

    def test_fetch_size_limit_and_invalid_bootstrap(self):
        from unittest.mock import MagicMock
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'oversized'
        with patch.object(latest.urllib.request,'urlopen',return_value=response):
            with self.assertRaises(ValueError): latest.fetch('VERSION',2)
        with patch.object(latest.os,'geteuid',return_value=0), \
                patch.object(latest,'fetch',side_effect=['1.2.0','invalid script']), \
                patch.object(latest.subprocess,'run') as run:
            with self.assertRaises(ValueError): latest.management()
            run.assert_not_called()
