"""Navigation errors stay friendly; API and asset errors retain their contract."""
import unittest
from fastapi import HTTPException
import test_job_planning as fixture

class BrowserRecoveryTests(unittest.TestCase):
    setUp=fixture.JobPlanningTests.setUp
    staff=fixture.JobPlanningTests.staff

    def test_unknown_browser_pages_are_branded_private_and_not_cached(self):
        for path in ('/quote','/help','/inquiry','/contact','/unknown?token=PRIVATE_TOKEN'):
            r=self.owner.get(path,headers={'accept':'text/html'})
            self.assertEqual(r.status_code,404)
            self.assertIn('text/html',r.headers['content-type'])
            self.assertEqual(r.headers['cache-control'],'no-store')
            self.assertIn('tel:+15146064030',r.text)
            self.assertIn('latest Crown Movers email',r.text)
            self.assertNotIn('PRIVATE_TOKEN',r.text)
            self.assertNotIn('__TITLE__',r.text)

    def test_french_and_expired_browser_links(self):
        @self.app.get('/expired-test')
        def expired():raise HTTPException(410,'PRIVATE DETAIL')
        r=self.owner.get('/expired-test',headers={'accept':'text/html','accept-language':'fr-CA,fr;q=0.9,en;q=0.8'})
        self.assertEqual(r.status_code,410)
        self.assertIn('lang="fr"',r.text)
        self.assertIn('Retrouvons votre lien',r.text)
        self.assertNotIn('PRIVATE DETAIL',r.text)
        r=self.owner.get('/expired-test?lang=en',headers={'accept':'text/html','accept-language':'fr'})
        self.assertIn('lang="en"',r.text)

    def test_api_assets_and_non_navigation_errors_keep_json(self):
        for path in ('/api/not-a-route','/api','/static/missing.js'):
            r=self.owner.get(path,headers={'accept':'text/html'})
            self.assertEqual(r.status_code,404)
            self.assertIn('application/json',r.headers['content-type'])
        r=self.owner.get('/unknown',headers={'accept':'application/json'})
        self.assertEqual(r.json(),{'detail':'Not Found'})
        self.assertEqual(self.owner.post('/unknown',headers={'accept':'text/html'}).json(),{'detail':'Not Found'})
        @self.app.get('/forbidden-test')
        def forbidden():raise HTTPException(403,'Forbidden')
        self.assertEqual(self.owner.get('/forbidden-test',headers={'accept':'text/html'}).json(),{'detail':'Forbidden'})

    def test_display_version_matches_health_and_release(self):
        data=self.owner.get('/api/app-release').json()
        self.assertEqual(self.owner.get('/api/health').json()['version'],data['version'])
        html=self.owner.get('/').text
        self.assertIn('name="crownops-version" content="'+data['version']+'"',html)
        self.assertNotIn('__CROWNOPS_VERSION__',html)
