"""An installed app can detect a release and request its fresh entry assets."""
from pathlib import Path
import re
import tempfile
import unittest
from app.app_release import release_snapshot
import test_job_planning as fixture


class AppReleaseTests(unittest.TestCase):
    setUp=fixture.JobPlanningTests.setUp
    staff=fixture.JobPlanningTests.staff

    def test_release_matches_document_and_asset_urls_without_private_data(self):
        release=self.owner.get('/api/app-release')
        self.assertEqual(release.status_code,200)
        self.assertEqual(release.headers['cache-control'],'no-store')
        data=release.json();self.assertEqual(set(data),{'revision','version'})
        self.assertRegex(data['revision'],r'^[a-f0-9]{24}$')
        document=self.owner.get('/')
        self.assertIn('text/html',document.headers['content-type'])
        self.assertEqual(document.headers['cache-control'],'no-store')
        self.assertIn('content="'+data['revision']+'"',document.text)
        versions=re.findall(r'[?&]v=([^"\s&<>]+)',document.text)
        self.assertGreater(len(versions),20)
        self.assertEqual(set(versions),{data['revision']})
        self.assertNotIn('__CROWNOPS_RELEASE__',document.text)

    def test_update_assets_are_revalidated_for_every_role(self):
        for user in ('owner','sales','crew','dispatch','accounting'):
            self.assertEqual(self.staff(user).get('/api/app-release').status_code,200)
        for path in ('/static/app_relaunch.js','/static/device_support.css','/sw.js'):
            response=self.owner.get(path)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['cache-control'],'no-cache')

    def test_source_change_changes_identity_but_mtime_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);static=root/'app'/'static';static.mkdir(parents=True)
            (static/'index.html').write_text('<meta name="crownops-release" content="__CROWNOPS_RELEASE__"><script src="/static/test.js?v=1"></script>')
            script=static/'test.js';script.write_text('one')
            first,_=release_snapshot(root);script.touch()
            self.assertEqual(release_snapshot(root)[0],first)
            script.write_text('two');second,html=release_snapshot(root)
            self.assertNotEqual(first,second);self.assertIn('?v='+second,html)
            (root/'app'/'backend.py').write_text('changed API')
            self.assertNotEqual(release_snapshot(root)[0],second)


if __name__=='__main__':unittest.main()
