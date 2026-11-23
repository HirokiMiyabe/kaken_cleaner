"""KAKEN XML を一件ずつ、辞書・JSON Lines に変換する。

使い方:
    from Kaken_Cleaner_fast import KakenToTable
    KakenToTable("KAKEN_2000_2005.xml").to_jsonlines("output.jsonl")

辞書参照の削減、共通処理の集約で効率化する。
日付は created_date / start_date / end_date のすべてを 'YYYY-MM-DD' に揃え、
年だけの値を補った行、作成日で代用した行は is_date_comp = 1 とする。
年月だけの値など、解釈できない日付は None にして件数を数える。
XML の辞書化には従来と同じ xmltodict を使い、属性・空要素・繰り返し
要素の解釈を維持する。XML 全体や全レコードをメモリには保持しない。

研究者の補完条件と save=False の扱いは従来版を維持する。
成果物・報告書は単独でも複数でも同じ方法で処理し、XML 上の件数を
出力に残す。report_list の各要素は parent_id と text の固定キーを持つ。
変換失敗は TSV に記録し、処理件数を最後に表示する。
分野名 field と対応する field_nii に加え、field_list に全階層・全分類表を残す。
saimoku_list は細目表の sequence ごとの最深要素であり、分科の判定は行わない。
Kaken_Cleaner.py 自体には依存しない。

保存先 save_path は必須の引数とする（v0.1.0 までは省略すると修論の
config.py の DATA を参照していた）。

分野の段（細目・分科・部）について:
    field_list・saimoku_list には XML の niiCode と path を（nii は先頭の0を
    除いた文字列にそろえて）残し、分科・部などの段の名前は、ここでは付けない。
    parent_nii・ancestor_niis を、修正済みの分類表（Layer1 の fields_snapshot）
    に nii で結合して決める。KAKEN の path は分類表 XML と同じ誤り（例：nii 3044
    の段数、看護学の親）を含むため、path の位置から段を決めると誤りをそのまま
    持ち込む。saimoku_list の parent_nii は path 上の一つ上の要素であり、
    分科とは限らない。
    従来の field（最後の field 要素の名前）は互換のため残す。細目を2つ持つ
    課題では第2の細目、ほかの表があればその名前になる点に注意する。
    （この段落は「Kaken_Cleaner_fast (2).py」(2026-11-18) の説明から移した）
"""

import csv
from datetime import date
import json
from pathlib import Path
import re

from lxml import etree
import pandas as pd
from tqdm import tqdm
import xmltodict


_AUTHOR_SEPARATOR = re.compile(r"[・、,，]")
# 日付部分だけを取り出す。後ろに時刻が付いていても(例: 2020-04-01T00:00:00Z)日付部分だけを
# 使い、時刻やタイムゾーンは変換しない（書かれている日付をそのまま採用する）。
_DATE_RE = re.compile(r"(\d{4})(?:-(\d{2})-(\d{2}))?(?=$|[T ])")
_PARAGRAPH_TYPES = (
    "outline_of_research_initial",
    "outline_of_research_performance",
    "abstract",
    "outline_of_research_achievement",
    "significance_of_research_achievement",
    "progress",
    "planning_scheme",
    "plan_of_carry_over",
    "planning_budget_expenditure",
)


def _xml_items(value):
    """xmltodict の欠損・単独要素・複数要素をリストとして扱う。"""
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def _xml_count(value):
    if not value:
        return 0
    return len(value) if isinstance(value, list) else 1


def _norm_nii(value):
    """NII コードを文字列に揃える。数字の先頭ゼロだけを取り除く。"""
    if value is None or value == "":
        return None
    value = str(value)
    return (value.lstrip("0") or "0") if value.isascii() and value.isdigit() else value


def _path_niis(path):
    """6桁単位の階層パスを展開する。不正・欠損は None とする。"""
    if not isinstance(path, str) or not path or len(path) % 6:
        return None
    if not path.isascii() or not path.isdigit():
        return None
    return [_norm_nii(path[i:i + 6]) for i in range(0, len(path), 6)]


def _sequence_key(value):
    """数値の sequence は数値順、その他と欠損はその後に並べる。"""
    if value is None:
        return (2, "")
    return (0, int(value)) if str(value).isdigit() else (1, str(value))


def _as_element(item):
    """xmltodict の要素を辞書にそろえる。

    属性のない要素（<field>名前</field>）は文字列、空の要素は None になる。
    その場合も課題の変換を失敗させず、文字列は {"#text": 文字列} として扱う。
    属性のある要素（辞書）はそのまま返すので、従来の値は変わらない。
    """
    if isinstance(item, dict):
        return item
    if item is None:
        return {}
    return {"#text": item}


def _field_codes(summary):
    """全分野と細目系列を取り出す。path の親は点検用で、分科とは限らない。"""
    fields = []
    groups = {}
    for item in map(_as_element, _xml_items(summary.get("field"))):
        nii = _norm_nii(item.get("@niiCode"))
        path = item.get("@path")
        chain = _path_niis(path)
        field = {
            "field_table": item.get("@fieldTable"),
            "nii": nii,
            "name": item.get("#text"),
            "path": path,
            "sequence": item.get("@sequence"),
            "depth": len(chain) if chain is not None else None,
            "path_ok": chain[-1] == nii if chain is not None and nii is not None else None,
            "attrs": {key[1:]: value for key, value in item.items()
                      if key.startswith("@") and key not in
                      ("@fieldTable", "@niiCode", "@path", "@sequence")},
        }
        fields.append(field)
        if field["field_table"] == "saimoku":
            groups.setdefault(field["sequence"], []).append(field)

    saimoku = []
    for seq in sorted(groups, key=_sequence_key):
        group = groups[seq]
        # 同じ深さの場合は XML の最後の要素を採用し、不整合はフラグに残す。
        deepest = max(enumerate(group), key=lambda pair: (pair[1]["depth"] or 0, pair[0]))[1]
        chain = _path_niis(deepest["path"])
        saimoku.append({
            "seq": seq,
            "saimoku_nii": deepest["nii"],
            "saimoku_name": deepest["name"],
            "path": deepest["path"],
            "depth": deepest["depth"],
            "parent_nii": chain[-2] if chain is not None and len(chain) > 1 else None,
            "ancestor_niis": chain[:-1] if chain is not None else None,
            "chain_ok": (chain is not None
                         and all(field["path_ok"] is True for field in group)
                         and {field["nii"] for field in group} == set(chain)),
        })
    category = summary.get("category") or {}
    return {
        "field_nii": fields[-1]["nii"] if fields else None,
        "field_list": fields,
        "saimoku_list": saimoku,
        "saimoku_nii": saimoku[0]["saimoku_nii"] if saimoku else None,
        "saimoku_nii_all": [item["saimoku_nii"] for item in saimoku],
        "n_saimoku": len(saimoku),
        "multi_saimoku": int(len(saimoku) > 1),
        "shumoku_nii": _norm_nii(category.get("@niiCode")),
        "shumoku_path": category.get("@path"),
        "review_section_list": [
            {"nii": _norm_nii(item.get("@niiCode")), "name": item.get("#text"),
             "attrs": {key[1:]: value for key, value in item.items() if key.startswith("@")}}
            for item in _xml_items(summary.get("review_section"))
        ],
        "summary_lang": summary.get("@xml:lang"),
    }


class KakenToTable:
    """従来版と同じクラス名・公開メソッド名を持つ変換器。"""

    def __init__(self, kaken_xml_path=None):
        self.kaken_xml_path = kaken_xml_path
        self.count = 0
        self.unparsed_dates = 0  # 日付として解釈できず None にした値の数
        self.seen = 0
        self.written = 0
        self.failed = 0
        self.error_path = None

    def _iso_date(self, value, is_end=False):
        """日付を 'YYYY-MM-DD' に揃える。戻り値は (日付 or None, 補完したか)。

        - 'YYYY-MM-DD': そのまま使う。後ろに時刻が付いていても日付部分だけを使い、
          タイムゾーンの変換はしない（補完なし）。
        - 'YYYY': 開始日は 'YYYY-04-01'、終了日は 'YYYY-03-31' にする（補完あり）。
          終了日は引き継ぎ文書のルールどおり、同じ年の 3 月 31 日とする。
        - 空: (None, False)
        - 上記以外('YYYY-MM' など): 解釈できない値として (None, False)。
          件数を self.unparsed_dates に数える。
        """
        if isinstance(value, dict):
            value = value.get("#text")
        if value is None or value == "":
            return None, False
        match = _DATE_RE.match(value.strip()) if isinstance(value, str) else None
        if match:
            year, month, day = (int(part) if part else None for part in match.groups())
            try:
                if day is not None:
                    return date(year, month, day).isoformat(), False
                if is_end:
                    return date(year, 3, 31).isoformat(), True
                return date(year, 4, 1).isoformat(), True
            except ValueError:
                pass
        self.unparsed_dates += 1
        return None, False

    def parse_iter(self):
        """grantAward を一件ずつ返し、処理済みの XML 要素を解放する。"""
        # 従来版の recover=True の XMLParser は iterparse に渡されていない。
        # ここでも実際に使用されていた iterparse の設定を維持する。
        context = etree.iterparse(
            self.kaken_xml_path,
            events=("end",),
            tag="grantAward",
            encoding="utf-8",
        )
        for _, elem in context:
            try:
                yield etree.tostring(elem, encoding="utf-8")
            except Exception as exc:
                print(f"Skipping broken element: {exc}")
            finally:
                elem.clear()
                parent = elem.getparent()
                while elem.getprevious() is not None:
                    del parent[0]

    def making_dict(self, member, lst=True):
        """研究者の出力項目を抽出する。lst は既存呼び出しとの互換用。"""
        name = member.get("personalName")
        if name:
            full_name = (
                name.get("fullName")
                if type(name) != list
                else name[0]["fullName"]
            )
        else:
            full_name = None
        return {
            "@eradCode": member.get("@eradCode"),
            "institution": member.get("institution"),
            "department": member.get("department"),
            "jobTitle": member.get("jobTitle"),
            "fullName": full_name,
        }

    def repmenber_info2(self, ja_members, lst=True):
        """代表者を抽出する。従来のメソッド名の綴りも維持する。"""
        member = self.making_dict(ja_members[0] if lst else ja_members)
        return (
            1 if lst else 0,
            member["@eradCode"],
            member["institution"],
            member["department"],
            member["jobTitle"],
            member["fullName"],
        )

    def reseacher_info(self, kaken_dict):
        """日英の研究者情報を、従来と同じ条件・順序で補完する。"""
        summaries = kaken_dict["summary"]
        ja_members = summaries[0]["member"]
        en_members = summaries[1].get("member")
        foreign_colab = 0

        if en_members:
            if isinstance(en_members, dict):
                # 日英とも単独の辞書の場合、従来版は補完しない。
                if not isinstance(ja_members, dict):
                    sequences = [member["@sequence"] for member in ja_members]
                    en_sequence = en_members["@sequence"]
                    if en_sequence in sequences:
                        index, member = next(
                            (i, member)
                            for i, member in enumerate(ja_members)
                            if member.get("@sequence") == en_sequence
                        )
                        # この分岐ではキーの有無ではなく、値の真偽を調べる。
                        if not member.get("personalName"):
                            ja_members[index]["personalName"] = {
                                "fullName": en_members.get("personalName", {}).get(
                                    "fullName"
                                )
                            }
                            foreign_colab = 1
                    else:
                        if en_sequence == "1":
                            ja_members.insert(0, en_members)
                        else:
                            ja_members.append(en_members)
                        foreign_colab = 1
            else:
                if isinstance(ja_members, dict):
                    ja_members = [ja_members]
                ja_indices = {
                    member["@sequence"]: i for i, member in enumerate(ja_members)
                }
                en_indices = {
                    member["@sequence"]: i for i, member in enumerate(en_members)
                }
                ja_sequences = set(ja_indices)
                en_sequences = set(en_indices)
                missing = en_sequences - ja_sequences
                if missing:
                    foreign_colab = 1

                # 英語側だけの研究者は sequence 番号の順に足す（v0.1.0 までは set の
                # 走査順で、実行ごとに並びが変わりえた）。追加前のインデックスを
                # 使う挙動は維持する。
                for sequence in sorted(missing, key=_sequence_key):
                    member = en_members[en_indices[sequence]]
                    if sequence == "1":
                        ja_members.insert(0, member)
                    else:
                        ja_members.append(member)

                for sequence in ja_sequences & en_sequences:
                    member = ja_members[ja_indices[sequence]]
                    # 英語がリストの場合は、空値でもキーがあれば補完しない。
                    if "personalName" not in member:
                        en_name = en_members[en_indices[sequence]].get(
                            "personalName", {}
                        )
                        if en_name:
                            name = (
                                en_name[0].get("fullName", "")
                                if isinstance(en_name, list)
                                else en_name.get("fullName", "")
                            )
                        else:
                            name = ""
                        member["personalName"] = {"fullName": name}
                        foreign_colab = 1

        is_list = not isinstance(ja_members, dict)
        team, erad, institution, department, job_title, name = self.repmenber_info2(
            ja_members, lst=is_list
        )
        members = ja_members if is_list else [ja_members]
        return (
            team,
            foreign_colab,
            erad,
            institution,
            department,
            job_title,
            name,
            [self.making_dict(member) for member in members],
        )

    @staticmethod
    def _research_paragraphs(paragraphs):
        """研究概要を種類ごとに連結し、未記載の種類は空文字にする。"""
        result = dict.fromkeys(_PARAGRAPH_TYPES, "")
        if paragraphs:
            blocks = paragraphs if type(paragraphs) == list else [paragraphs]
            for block in blocks:
                kind = block["@type"]
                texts = block["paragraph"]
                if type(texts) == list:
                    if texts:
                        result[kind] += "".join(text["#text"] for text in texts)
                else:
                    result[kind] += texts["#text"]
        return result

    def _award_dates(self, summary, created_date):
        """年代別の交付期間タグから開始日・終了日と補完フラグを求める。

        created_date は 'YYYY-MM-DD' に変換済みの値を渡す。
        戻り値: (start_date, end_date, use_created, is_comp)
        is_comp は、開始日・終了日のどちらかを年だけの値から補った、
        または作成日で代用したときに True になる。
        """
        start_date = None
        end_date = None
        use_created = 0
        is_comp = False
        period = summary.get("periodOfAward")
        if period:
            if period.get("startDate"):
                start_date, comp = self._iso_date(period["startDate"])
                is_comp = is_comp or comp
            else:
                candidates = []
                if period.get("@searchStartFiscalYear"):
                    candidates.append(period["@searchStartFiscalYear"])
                if period.get("startFiscalYear"):
                    fiscal_year = period["startFiscalYear"]
                    if isinstance(fiscal_year, dict):
                        fiscal_year = fiscal_year.get("#text")
                    candidates.append(fiscal_year)
                candidates = list(set(value for value in candidates if value))
                if candidates:
                    start_date, comp = self._iso_date(min(candidates))
                    is_comp = is_comp or comp
                elif created_date:
                    start_date = created_date
                    use_created = 1
                    is_comp = True

            if period.get("endDate"):
                end_date, comp = self._iso_date(period["endDate"], is_end=True)
                is_comp = is_comp or comp
            else:
                candidates = []
                if period.get("@searchEndFiscalYear"):
                    candidates.append(period["@searchEndFiscalYear"])
                if period.get("endFiscalYear"):
                    fiscal_year = period["endFiscalYear"]
                    if isinstance(fiscal_year, dict):
                        if "#text" in fiscal_year:
                            candidates.append(fiscal_year["#text"])
                    else:
                        candidates.append(fiscal_year)
                candidates = list(set(value for value in candidates if value))
                if candidates:
                    # 終了年も従来版と同じ min を使う。
                    end_date, comp = self._iso_date(min(candidates), is_end=True)
                    is_comp = is_comp or comp
        elif created_date:
            start_date = created_date
            use_created = 1
            is_comp = True
        return start_date, end_date, use_created, is_comp

    @staticmethod
    def _product_record(product, single=False):
        """成果物一件を変換する。single は既存呼び出しとの互換用。"""
        product_type = product["@type"]
        year = None
        if "year" in product:
            value = product["year"]
            if isinstance(value, dict):
                year = int(value["#text"]) if "#text" in value else None
            else:
                year = int(value)

        author = product.get("author")
        authors = []
        if author:
            for value in _xml_items(author):
                text = value["#text"] if isinstance(value, dict) else value
                authors.extend(
                    _AUTHOR_SEPARATOR.split(text.replace("\u3000", " "))
                )
        return {
            "type": product_type,
            "year": year,
            "author": authors,
            "title": product.get("title"),
            "journal": product.get("journalTitle"),
            "organizer": product.get("organizer"),
            "reviewed": product.get("reviewed"),
            "invited": product.get("invited"),
        }

    def _products(self, product_list):
        if not product_list:
            return []
        return [
            self._product_record(product)
            for product in _xml_items(product_list["product"])
        ]

    @staticmethod
    def _report_text(paragraphs):
        parts = []
        if isinstance(paragraphs, list):
            for text in paragraphs:
                if isinstance(text, dict) and "#text" in text:
                    parts.append(text["#text"])
                elif isinstance(text, str):
                    parts.append(text)
        elif isinstance(paragraphs, dict) and "#text" in paragraphs:
            parts.append(paragraphs["#text"])
        elif isinstance(paragraphs, str):
            parts.append(paragraphs)
        return "".join(parts)

    def _reports(self, reports):
        """親 ID と本文を固定キーに格納し、順序・重複・空の本文を維持する。"""
        result = []
        for report in _xml_items(reports):
            if not isinstance(report, dict):
                raise TypeError("report entries must be mappings")
            # 空の報告書も ID と空文字を残し、「存在しない」と区別する。
            result.append(
                {
                    "parent_id": report.get("@parentId"),
                    "text": self._report_text(report.get("paragraph")),
                }
            )
        return result

    def kaken_to_table(self, kaken_dict):
        """一件を変換する。研究者の補完条件などは従来版を維持する。

        日付は 'YYYY-MM-DD' に統一し、補完した行を示す is_date_comp を
        use_created の直後に追加している。
        """
        summary = kaken_dict["summary"][0]
        # 英語情報の補完で member が増える前に、日本語 summary の件数を数える。
        n_members_in_xml = _xml_count(summary.get("member"))
        created_date = kaken_dict.get("created")
        if isinstance(created_date, dict):
            created_date = created_date.get("#text")
        # 1987 年以前の作成日は一律 1987 年になっているため年度で補正する。
        # 得られるのは年度(例 "1985")だけなので、下の変換で 'YYYY-04-01' に補う。
        if isinstance(created_date, str) and created_date[:4] == "1987":
            created_date = (summary.get("projectStatus") or {}).get("@fiscalYear")
        # 日付は最初に 'YYYY-MM-DD' へ揃える。以降の処理はすべてこの形で扱う。
        created_date, is_date_comp = self._iso_date(created_date)

        # field は XML の最後の分野名。第2細目や細目表以外の名前の場合もある。
        field_codes = _field_codes(summary)
        field = summary.get("field")
        if field:
            last = field[-1] if isinstance(field, list) else field
            field = _as_element(last).get("#text")
        else:
            field = None

        sections = summary.get("review_section")
        if sections:
            if isinstance(sections, list):
                goudou = 1
                sinsa_kubun = [section["#text"] for section in sections]
            else:
                goudou = 0
                sinsa_kubun = [sections["#text"]]
        else:
            goudou, sinsa_kubun = 0, []

        institutions = summary.get("institution")
        if institutions:
            if isinstance(institutions, list):
                base_institution = [item["#text"] for item in institutions]
            else:
                base_institution = [institutions.get("#text")]
        else:
            members = summary["member"]
            representative = members[0] if isinstance(members, list) else members
            base_institution = [representative.get("institution")]

        # 所属機関の補完を先に行う。研究者補完による member の変更より前。
        team, foreign, erad, institution, department, job, name, members = (
            self.reseacher_info(kaken_dict)
        )
        status = summary.get("projectStatus")
        state = status["@statusCode"] if status else None
        keyword_list = summary.get("keywordList")
        if keyword_list:
            items = keyword_list["keyword"]
            keywords = (
                [item["#text"] for item in items]
                if type(items) == list
                else [items["#text"]]
            )
        else:
            keywords = []

        paragraphs = self._research_paragraphs(summary.get("paragraphList"))
        start_date, end_date, use_created, dates_comp = self._award_dates(
            summary, created_date
        )
        is_date_comp = int(is_date_comp or dates_comp)
        costs = summary.get("overallAwardAmount", {})
        direct_cost, indirect_cost, total_cost = [
            int(costs.get(key)) if costs.get(key) else None
            for key in ("directCost", "indirectCost", "totalCost")
        ]
        product_source = kaken_dict.get("productList")
        report_source = kaken_dict.get("paragraphList")
        products = self._products(product_source)
        reports = self._reports(report_source)

        result = {
            "awardNumber": kaken_dict.get("@awardNumber"),
            "created_date": created_date,
            "start_date": start_date,
            "use_created": use_created,
            "is_date_comp": is_date_comp,
            "url": kaken_dict["urlList"]["url"],
            "title": summary.get("title") or kaken_dict["summary"][1].get("title"),
            "shumoku": summary.get("category", {}).get("#text"),
            "field": field,
            "goudou": goudou,
            "sinsa_kubun": sinsa_kubun,
            "base_institution": base_institution,
            "team": team,
            "foreign_colab": foreign,
            "erad_number": erad,
            "institution": institution,
            "department": department,
            "jobTitle": job,
            "fullName": name,
            "all_members": members,
            "n_members_in_xml": n_members_in_xml,
            "state": state,
            "keywords": keywords,
            "end_date": end_date,
            "directCost": direct_cost,
            "indirectCost": indirect_cost,
            "totalCost": total_cost,
            "product_list": products,
            "n_products_in_xml": _xml_count((product_source or {}).get("product")),
            "report_list": reports,
            "n_reports_in_xml": _xml_count(report_source),
        }
        result.update(paragraphs)
        result.update(field_codes)
        return result

    def to_jsonlines(self, save_path, save=True, error_path=None):
        """一件ずつ変換・保存し、変換できないレコードは表示して継続する。

        save_path は必須。保存先のフォルダは作らないので、先に用意する。
        失敗は errors/<出力ファイル名>.errors.tsv に記録する。
        error_path で保存先を変更できる。データと別フォルダに置くことで、
        JSONL を列挙する既存ノートブックにエラーログを混入させない。
        seen / written / failed / unparsed_dates は呼び出しごとにリセットし、
        count は従来どおり累積件数とする。保存の I/O エラーと XML 全体の
        構文エラーは呼び出し元へ伝え、正常終了として扱わない。

        save=False も従来どおり、出力ファイルを開いたうえで最初の
        変換成功レコードだけをリストで返す。全件を返す仕様には変えない。
        """
        if save_path is None:
            raise TypeError("save_path is required (the config.py default was removed)")
        encode = json.JSONEncoder(ensure_ascii=False).encode
        parse = xmltodict.parse
        convert = self.kaken_to_table
        self.unparsed_dates = 0
        self.seen = self.written = self.failed = 0
        destination = Path(save_path)
        self.error_path = (
            Path(error_path)
            if error_path is not None
            else destination.parent / "errors" / f"{destination.name}.errors.tsv"
        )
        if self.error_path.resolve() == destination.resolve():
            raise ValueError("error_path and save_path must be different files")
        self.error_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with (
                open(save_path, "w", encoding="utf-8") as output,
                self.error_path.open("w", encoding="utf-8", newline="") as errors,
            ):
                error_writer = csv.writer(errors, delimiter="\t")
                error_writer.writerow(("row", "awardNumber", "error_type", "error"))
                write = output.write
                for grant_xml in tqdm(self.parse_iter(), desc="Processing"):
                    self.count += 1
                    self.seen += 1
                    grant_dict = {}
                    try:
                        grant_dict = parse(grant_xml)["grantAward"]
                        processed = convert(grant_dict)
                        # シリアライズ失敗も課題番号とともに記録する。
                        line = encode(processed) + "\n" if save else None
                    except Exception as exc:
                        self.failed += 1
                        award_number = (
                            grant_dict.get("@awardNumber")
                            if isinstance(grant_dict, dict)
                            else None
                        )
                        if not award_number:
                            # 辞書化自体が失敗した場合も、取得できれば番号を残す。
                            try:
                                award_number = etree.fromstring(grant_xml).get(
                                    "awardNumber"
                                )
                            except (etree.XMLSyntaxError, ValueError, TypeError):
                                pass
                        error_writer.writerow(
                            (self.seen, award_number, type(exc).__name__, str(exc))
                        )
                        errors.flush()
                        print(f"Error while processing grantAward: {exc}")
                        print(f"row: {self.count}")
                        continue
                    if save:
                        # 書き込み失敗は変換失敗として飲み込まず、その場で停止する。
                        write(line)
                        self.written += 1
                    else:
                        return [processed]
        finally:
            print(
                f"seen={self.seen} written={self.written} failed={self.failed} "
                f"errors={self.error_path}"
            )
            if self.unparsed_dates:
                print(f"日付として解釈できず None にした値: {self.unparsed_dates} 件")

    def to_pd(self):
        return pd.DataFrame(self.dict_list)
