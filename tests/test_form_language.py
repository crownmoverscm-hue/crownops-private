"""Language inference uses explicit source evidence, not a client's name."""
import unittest
from app.gmail_sync import _quote_fields


class FormLanguageTests(unittest.TestCase):
    def test_english_default_does_not_guess_from_a_french_name(self):
        self.assertEqual(_quote_fields('Client Name: François Test\nEmail: test@example.invalid','New Quote')['language'],'English')

    def test_french_form_marker_with_machine_english_labels(self):
        value=_quote_fields('Form: QuickQuoteFormFR\nClient Name: TEST MUSE\nEmail: test@example.invalid\nMoving Date: 2026-11-09','New Crown Movers Quote Request')
        self.assertEqual(value['language'],'French');self.assertEqual(value['move_date'],'2026-11-09')

    def test_french_calculator_source_and_explicit_preference(self):
        body='Source: https://www.crownmovers.ca/fr/calculateur-cout-demenagement-montreal/\nClient Name: TEST MUSE'
        self.assertEqual(_quote_fields(body,'New Instant Moving Estimate Request')['language'],'French')
        self.assertEqual(_quote_fields(body+'\nLanguage: English','New Instant Moving Estimate Request')['language'],'English')

    def test_french_fields_preserve_details_without_guessing_prices(self):
        body='''Nom du client: TEST MUSE
Courriel: test@example.invalid
Téléphone: 5146064030
Date du déménagement: 2026-11-09
Adresse de départ: H2X 1A1
Adresse de destination: H4N 2B2
Taille du logement: 2 chambres
Nombre approximatif de boîtes: 20
Accès au départ: Ascenseur
Accès à destination: Rez-de-chaussée
Distance de marche au départ: 10 m
Distance de marche à destination: 20 m
Déménagement dans le Grand Montréal: Oui
Lien photos / vidéos: https://example.invalid/photos
'''
        value=_quote_fields(body,'TEST demande de soumission')
        self.assertEqual(value['language'],'French');self.assertEqual(value['client_name'],'TEST MUSE')
        self.assertEqual(value['email'],'test@example.invalid');self.assertEqual(value['phone'],'5146064030')
        self.assertEqual(value['pickup'],'H2X 1A1');self.assertEqual(value['dropoff'],'H4N 2B2')
        self.assertEqual(value['boxes'],'20');self.assertEqual(value['intake']['local_area'],'Yes')
        self.assertEqual(value['photo_link'],'https://example.invalid/photos')
