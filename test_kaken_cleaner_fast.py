"""分野コードの抽出と JSON Lines への保存を検証する。"""

import json
from pathlib import Path
import tempfile
import unittest

import xmltodict

from Kaken_Cleaner_fast import KakenToTable, _field_codes, _norm_nii, _path_niis


def summary(fields):
    return xmltodict.parse(f'<summary xml:lang="ja">{fields}</summary>')["summary"]


def field(nii, path, seq="1", table="saimoku"):
    return (f'<field niiCode="{nii}" path="{path}" sequence="{seq}" '
            f'fieldTable="{table}">分野{nii}</field>')


class FieldCodesTests(unittest.TestCase):
    def test_three_levels_and_string_codes(self):
        result = _field_codes(summary(
            field("000079", "000079") + field("414", "000079000414")
            + field("415", "000079000414000415")))
        self.assertEqual(result["saimoku_list"], [{
            "seq": "1", "saimoku_nii": "415", "saimoku_name": "分野415",
            "path": "000079000414000415", "depth": 3, "parent_nii": "414",
            "ancestor_niis": ["79", "414"], "chain_ok": True}])
        self.assertEqual(result["field_nii"], "415")
        self.assertTrue(all(item["path_ok"] for item in result["field_list"]))

    def test_two_saimoku_numeric_sequence_order_and_other_table(self):
        fields = ""
        for seq, codes in (("10", (1, 2, 3, 4)), ("2", (1, 5, 6, 7))):
            for depth, nii in enumerate(codes, 1):
                fields += field(nii, "".join(f"{n:06}" for n in codes[:depth]), seq)
        fields += field("1894", "001893001894", table="tokubetsu_kenkyu")
        result = _field_codes(summary(fields))
        self.assertEqual(result["n_saimoku"], 2)
        self.assertEqual(result["multi_saimoku"], 1)
        self.assertEqual(result["saimoku_nii_all"], ["7", "4"])
        self.assertEqual(result["saimoku_nii"], "7")
        self.assertEqual(result["field_nii"], "1894")
        self.assertEqual(len(result["field_list"]), 9)
        self.assertTrue(all(item["depth"] == 4 and item["chain_ok"]
                            for item in result["saimoku_list"]))

    def test_two_levels_no_bunka_inference(self):
        result = _field_codes(summary(field("79", "000079")
                                     + field("3044", "000079003044")))
        self.assertEqual(result["saimoku_list"][0]["parent_nii"], "79")
        self.assertEqual(result["saimoku_list"][0]["depth"], 2)
        self.assertTrue(result["saimoku_list"][0]["chain_ok"])
        self.assertNotIn("bunka_nii", result)

    def test_invalid_paths_missing_codes_and_incomplete_chain(self):
        for path in (None, "", "0000791", "abcdef", "１２３４５６"):
            self.assertIsNone(_path_niis(path))
        for xml, expected in (
            (field("415", "000079000414000415"), True),
            (field("416", "000079000414000415"), False),
            (field("415", "0000791"), None),
            ('<field fieldTable="saimoku"/>', None),
        ):
            result = _field_codes(summary(xml))
            self.assertIs(result["field_list"][0]["path_ok"], expected)
            self.assertFalse(result["saimoku_list"][0]["chain_ok"])
        self.assertEqual(_norm_nii("M0001"), "M0001")
        self.assertEqual(_norm_nii("000000"), "0")
        self.assertIsNone(_norm_nii(None))

    def test_absent_fields_and_preserved_attributes(self):
        result = _field_codes(summary(
            '<category niiCode="000072" path="000072">基盤研究(C)</category>'
            '<review_section niiCode="000767" sequence="2" tableType="review_section_tokken">審査区分</review_section>'))
        self.assertEqual(result["field_list"], [])
        self.assertEqual(result["saimoku_list"], [])
        self.assertIsNone(result["field_nii"])
        self.assertEqual(result["n_saimoku"], 0)
        self.assertEqual(result["shumoku_nii"], "72")
        self.assertEqual(result["summary_lang"], "ja")
        self.assertEqual(result["review_section_list"][0]["nii"], "767")
        self.assertEqual(result["review_section_list"][0]["attrs"]["tableType"],
                         "review_section_tokken")
        result = _field_codes(summary('<field niiCode="1350" custom="値">分野</field>'))
        self.assertEqual(result["field_list"][0]["attrs"], {"custom": "値"})
        self.assertEqual(result["field_nii"], "1350")
        self.assertEqual(result["n_saimoku"], 0)

    def test_jsonlines_preserves_field_name_and_code(self):
        xml = ('<grantAwards><grantAward awardNumber="17K18091">'
               '<urlList><url>https://example.test/</url></urlList>'
               '<summary xml:lang="ja"><title>研究</title>'
               + field("79", "000079") + field("3044", "000079003044")
               + '<member sequence="1" role="principal_investigator">'
               '<personalName><fullName>研究者</fullName></personalName></member>'
               '</summary><summary xml:lang="en"><title>Research</title></summary>'
               '</grantAward></grantAwards>')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.xml"
            output = Path(directory) / "output.jsonl"
            source.write_text(xml, encoding="utf-8")
            cleaner = KakenToTable(source)
            cleaner.to_jsonlines(output)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual((cleaner.seen, cleaner.written, cleaner.failed), (1, 1, 0))
            self.assertEqual(result["field"], "分野3044")
            self.assertEqual(result["field_nii"], "3044")
            self.assertEqual(result["saimoku_nii_all"], ["3044"])


def grant(fields, number="17K18091"):
    return (f'<grantAward awardNumber="{number}">'
            '<urlList><url>https://example.test/</url></urlList>'
            '<summary xml:lang="ja"><title>研究</title>' + fields
            + '<member sequence="1" role="principal_investigator">'
            '<personalName><fullName>研究者</fullName></personalName></member>'
            '<overallAwardAmount><directCost>1000</directCost></overallAwardAmount>'
            '</summary><summary xml:lang="en"><title>Research</title></summary>'
            '</grantAward>')


def convert(xml):
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "input.xml"
        output = Path(directory) / "output.jsonl"
        source.write_text(f"<grantAwards>{xml}</grantAwards>", encoding="utf-8")
        cleaner = KakenToTable(source)
        cleaner.to_jsonlines(output)
        rows = [json.loads(line) for line in
                output.read_text(encoding="utf-8").splitlines()]
        return cleaner, rows


class PlainFieldTests(unittest.TestCase):
    """属性のない分野の要素でも、課題を出力から落とさない。"""

    def test_single_plain_field(self):
        cleaner, rows = convert(grant("<field>航空宇宙工学</field>"))
        self.assertEqual((cleaner.seen, cleaner.written, cleaner.failed), (1, 1, 0))
        row = rows[0]
        self.assertEqual(row["field"], "航空宇宙工学")
        self.assertIsNone(row["field_nii"])
        self.assertEqual(row["field_list"][0]["name"], "航空宇宙工学")
        self.assertEqual(row["n_saimoku"], 0)
        self.assertEqual(row["directCost"], 1000)

    def test_plain_field_mixed_with_attributed_fields(self):
        cleaner, rows = convert(
            grant(field("79", "000079") + "<field>名前だけ</field>"
                  + field("3044", "000079003044"))
            + grant("<field>最後が名前だけ</field><field/>", "63850161"))
        self.assertEqual((cleaner.written, cleaner.failed), (2, 0))
        self.assertEqual(rows[0]["field"], "分野3044")
        self.assertEqual(rows[0]["field_nii"], "3044")
        self.assertEqual(len(rows[0]["field_list"]), 3)
        self.assertEqual(rows[0]["saimoku_nii_all"], ["3044"])
        self.assertIsNone(rows[1]["field"])
        self.assertEqual(rows[1]["field_list"][0]["name"], "最後が名前だけ")


class SavePathTests(unittest.TestCase):
    def test_save_path_is_required(self):
        with self.assertRaises(TypeError):
            KakenToTable("input.xml").to_jsonlines()
        with self.assertRaises(TypeError):
            KakenToTable("input.xml").to_jsonlines(None)


class MemberOrderTests(unittest.TestCase):
    """英語の summary にだけいる研究者を、実行ごとに変わらない順で足す。"""

    XML = ('<grantAwards><grantAward awardNumber="1">'
           '<urlList><url>u</url></urlList><summary xml:lang="ja"><title>t</title>'
           '<member sequence="1"><personalName><fullName>代表</fullName></personalName></member>'
           '</summary><summary xml:lang="en">'
           + "".join(f'<member sequence="{n}"><personalName><fullName>en{n}</fullName>'
                     '</personalName></member>' for n in (1, 12, 3, 10, 2, 5, 4))
           + '</summary></grantAward></grantAwards>')

    def test_sequence_order_regardless_of_hash_seed(self):
        import os, subprocess, sys
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.xml"
            source.write_text(self.XML, encoding="utf-8")
            results = set()
            for seed in ("0", "1", "2", "3", "4"):
                output = Path(directory) / f"out_{seed}.jsonl"
                subprocess.run(
                    [sys.executable, "-c",
                     "import sys; from Kaken_Cleaner_fast import KakenToTable; "
                     "KakenToTable(sys.argv[1]).to_jsonlines(sys.argv[2])",
                     str(source), str(output)],
                    check=True, capture_output=True,
                    cwd=Path(__file__).resolve().parent,
                    env={**os.environ, "PYTHONHASHSEED": seed})
                row = json.loads(output.read_text(encoding="utf-8"))
                results.add(tuple(m["fullName"] for m in row["all_members"]))
            self.assertEqual(results, {("代表", "en2", "en3", "en4", "en5", "en10", "en12")})


if __name__ == "__main__":
    unittest.main()
