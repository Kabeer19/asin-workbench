"""UI tests with synthetic inputs; no network calls or report uploads."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest
from test_engine import fixture


class SingleUITests(unittest.TestCase):
    def test_bulk_uploads_still_work_alongside_single_checker(self):
        app = AppTest.from_file(str(Path(__file__).with_name('app.py')), default_timeout=30).run()
        for widget_key, frame in zip(['research', 'us', 'au', 'fees', 'details'], fixture()):
            app.file_uploader(key=widget_key).set_value((f'{widget_key}.csv', frame.to_csv(index=False).encode(), 'text/csv'))
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual([m.value for m in app.metric], ['3', '3', '3', '0'])
        app.multiselect(key='selected_asins').set_value(['B000000001']).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['selected_asins'], ['B000000001'])

    def test_no_upload_calculation_validation_refresh_and_bulk_independence(self):
        app = AppTest.from_file(str(Path(__file__).with_name('app.py')), default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertIn('Single ASIN checker', [tab.label for tab in app.tabs])
        self.assertEqual(len(app.file_uploader), 5)
        self.assertTrue(all(widget.value is None for widget in app.file_uploader))
        app.text_input(key='single_asin').set_value('B000000001')
        app.selectbox(key='single_category').set_value('kitchen')
        app.selectbox(key='single_subcategory').set_value('food_slicer')
        app.selectbox(key='single_fulfilment').set_value('Amazon')
        for key, value in [('single_us_price', 20.0), ('single_au_price', 66.0),
                           ('single_weight', 750.0), ('single_fba_fee', 8.88), ('single_closing_fee', 0.0)]:
            app.number_input(key=key).set_value(value)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.metric), 4)
        self.assertEqual(app.metric[0].value, 'AUD 65.00')
        self.assertTrue(any(d.key == 'single_download_excel' for d in app.download_button))
        original_profit = app.metric[2].value
        next(w for w in app.sidebar.number_input if w.label == 'USD → AUD exchange rate').set_value(1.6).run()
        self.assertFalse(app.exception)
        self.assertNotEqual(app.metric[2].value, original_profit)
        # Editing and submitting another ASIN never leaves the old result visible.
        app.text_input(key='single_asin').set_value('bad')
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.metric), 0)
        self.assertTrue(app.error)
        self.assertFalse(any(d.key == 'single_download_excel' for d in app.download_button))


if __name__ == '__main__':
    unittest.main()
