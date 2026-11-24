import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('registry', Path(__file__).resolve().parents[1] / 'tools/registry.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def package(name='example/tool', version='1.0.0'):
    return {'name': name, 'description': 'A package', 'repository': 'https://github.com/example/tool',
            'versions': {version: {'source': {'type': 'git', 'url': 'https://github.com/example/tool', 'revision': 'a' * 40},
                                   'integrity': 'b' * 64, 'entry': 'src/main.luau', 'dependencies': {}}}}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='registry-tests-')
        self.root = Path(self.temp.name).resolve()
        (self.root / 'index.json').write_bytes(r.pretty({'registry_version': 1, 'packages': {}}))
        (self.root / 'submissions').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, data, name='new.json'):
        (self.root / 'submissions' / name).write_bytes(r.pretty(data))

    def apply(self):
        index, packages, queued = r.plan(self.root, True)
        return r.apply(self.root, index, packages, queued)

    def test_new_package_index_and_pretty_json(self):
        self.submit(package())
        self.assertEqual(self.apply(), 3)
        index, packages = r.load(self.root)
        self.assertEqual(index['packages']['example/tool']['path'], 'packages/example/tool.json')
        self.assertIn('1.0.0', packages['example/tool']['versions'])
        self.assertFalse((self.root / 'submissions/new.json').exists())
        self.assertTrue((self.root / 'packages/example/tool.json').read_bytes().endswith(b'\n'))
        self.assertEqual(self.apply(), 0)

    def test_add_version_preserves_old_record_and_metadata(self):
        self.submit(package()); self.apply()
        before = r.load(self.root)[1]
        newer = package(version='1.1.0')
        newer['description'] = 'Updated description'
        self.submit(newer); self.apply()
        after = r.load(self.root)[1]
        r.immutable(before, after)
        self.assertEqual(after['example/tool']['versions']['1.0.0'], before['example/tool']['versions']['1.0.0'])
        self.assertEqual(len(after['example/tool']['versions']), 2)

    def test_contradictory_version_is_rejected_without_writes(self):
        self.submit(package()); self.apply()
        modified = package(); modified['versions']['1.0.0']['source']['revision'] = 'c' * 40
        self.submit(modified)
        before = (self.root / 'index.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.apply()
        self.assertEqual((self.root / 'index.json').read_bytes(), before)
        self.assertTrue((self.root / 'submissions/new.json').exists())

    def test_duplicate_submission_is_idempotent(self):
        self.submit(package()); self.apply()
        self.submit(package()); self.assertEqual(self.apply(), 1)

    def test_queue_conflict_is_rejected(self):
        self.submit(package(), 'a.json')
        bad = package(); bad['versions']['1.0.0']['integrity'] = 'c' * 64
        self.submit(bad, 'b.json')
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.apply()

    def test_invalid_identities(self):
        for name in ('../evil', 'Example/tool', 'a/b/c', 'con', 'example/nul'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                r.metadata(package(name=name))

    def test_versions_and_ranges(self):
        for version in ('1.2.3', '1.2.3-beta.1+build', '0.0.0'):
            r.version(version)
        for version in ('01.2.3', '1.2', '1.2.3-01', 'v1.0.0', '1.2.3+'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                r.version(version)
        for value in ('*', '^1.0.0', '~1.0.0', '>=1.0.0 <2.0.0', '1.2.x', '1.2.*'):
            r.version_range(value)
        for value in ('path:../foo', 'git+https://example.com/a#' + 'a' * 40, '^1.2.x', '>= 1.0.0', '', '\t*'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                r.version_range(value)

    def test_unsafe_paths_sources_and_native_capabilities(self):
        for mutate in (
            lambda v: v.update(entry='../escape.luau'),
            lambda v: v.update(entry='C:/escape.luau'),
            lambda v: v['source'].update(revision='main'),
            lambda v: v['source'].update(url='http://example.com/repo'),
            lambda v: v['source'].update(url='https://token@example.com/repo'),
            lambda v: v.update(integrity='invalid'),
            lambda v: v.update(native={}),
        ):
            value = package(); mutate(value['versions']['1.0.0'])
            with self.assertRaises(ValueError):
                r.metadata(value)

    def test_xtpkg_identity_is_reserved(self):
        with self.assertRaisesRegex(ValueError, 'official'):
            r.metadata(package(name='xtpkg'))

    def test_duplicate_keys_oversize_and_deep_json(self):
        target = self.root / 'submissions/new.json'
        for text in ('{"name":"a","name":"b"}', '{"a":NaN}', '[' * 50 + '0' + ']' * 50, ' ' * (r.MAX_BYTES + 1)):
            target.write_text(text)
            with self.assertRaises((ValueError, RecursionError)):
                r.read(self.root, 'submissions/new.json')

    def test_immutable_removal_and_changes(self):
        before = {'example/tool': package()}
        with self.assertRaisesRegex(ValueError, 'removed'):
            r.immutable(before, {})
        after = copy.deepcopy(before); after['example/tool']['versions']['1.0.0']['entry'] = 'new.luau'
        with self.assertRaisesRegex(ValueError, 'immutable'):
            r.immutable(before, after)

    def test_rollback_on_failed_file_replacement(self):
        self.submit(package())
        before = (self.root / 'index.json').read_bytes()
        original = r.os.replace
        def fail_package(source, destination):
            if str(destination).endswith('tool.json'):
                raise OSError('injected write failure')
            return original(source, destination)
        with patch.object(r.os, 'replace', fail_package), self.assertRaisesRegex(OSError, 'injected'):
            self.apply()
        self.assertEqual((self.root / 'index.json').read_bytes(), before)
        self.assertTrue((self.root / 'submissions/new.json').exists())
        self.assertFalse((self.root / 'packages/example/tool.json').exists())

    def test_path_traversal_and_unindexed_records(self):
        self.submit(package()); self.apply()
        index = json.loads((self.root / 'index.json').read_text())
        index['packages']['example/tool']['path'] = '../escape.json'
        (self.root / 'index.json').write_text(json.dumps(index))
        with self.assertRaises(ValueError):
            r.load(self.root)

    def test_unindexed_record_is_rejected(self):
        (self.root / 'packages').mkdir()
        (self.root / 'packages/a.json').write_bytes(r.pretty(package(name='a')))
        with self.assertRaisesRegex(ValueError, 'missing from index'):
            r.load(self.root)

    def test_symlink_is_rejected(self):
        outside = self.root / 'outside.json'; outside.write_bytes(r.pretty(package()))
        link = self.root / 'submissions/link.json'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('symlink permission unavailable on this Windows host')
        with self.assertRaisesRegex(ValueError, 'link'):
            r.pending(self.root)


if __name__ == '__main__':
    unittest.main()
