from types import SimpleNamespace
import unittest

from niro.catalog import distinct_values, report_filename


class CatalogTests(unittest.TestCase):
    def test_distinct_values_are_column_specific_and_ignore_whitespace(self):
        view = SimpleNamespace(
            snapshot=SimpleNamespace(columns=[SimpleNamespace(key="city"), SimpleNamespace(key="id")]),
            records=[
                SimpleNamespace(values=("تهران ", "۱۰۰")),
                SimpleNamespace(values=("شیراز", "100")),
                SimpleNamespace(values=("تهران", "101")),
                SimpleNamespace(values=("", " ")),
            ],
        )
        choices = distinct_values(view)
        self.assertEqual(set(choices["city"]), {"تهران", "شیراز"})
        self.assertEqual(choices["id"], ("100", "101"))

    def test_report_filename_uses_user_title_and_keeps_it_in_the_save_folder(self):
        self.assertEqual(report_filename("گزارش پرسنل", ".xlsx", "Niro-Report"), "گزارش پرسنل.xlsx")
        self.assertEqual(report_filename("گزارش.XLSX", ".xlsx", "Niro-Report"), "گزارش.xlsx")
        self.assertEqual(report_filename("تهران/مرکزی: ۱۴۰۵", ".docx", "Niro-Report"), "تهران_مرکزی_ ۱۴۰۵.docx")
        self.assertEqual(report_filename("CON", ".pptx", "Niro-Report"), "_CON.pptx")
