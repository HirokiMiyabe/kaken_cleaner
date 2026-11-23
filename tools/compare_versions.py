"""2つの版の Kaken_Cleaner_fast.py を同じ XML にかけ、旧版の全列が新版で一致するか確かめる。

使い方:
    python tools/compare_versions.py v0.1.0:Kaken_Cleaner_fast.py Kaken_Cleaner_fast.py --synthetic 5000
    python tools/compare_versions.py v0.1.0:Kaken_Cleaner_fast.py Kaken_Cleaner_fast.py --xml KAKEN_a.xml.gz KAKEN_b.xml.gz

版は、ファイルの場所か「Git の版:ファイル名」（例 v0.1.0:Kaken_Cleaner_fast.py）で指定する。
後者は git show で取り出す（リポジトリのフォルダで実行する）。
--synthetic N は乱数（seed 0）で合成した N 課題の XML を使う。KAKEN の実データは
リポジトリに入れないので、実データでの確認は --xml で手元のファイルを指定する。
出力は一時フォルダに書いて消す。旧版の列が新版にすべてあり、値が一致すれば mismatched cells: 0 と表示する。
"""
import argparse, gzip, importlib.util, json, random, subprocess, tempfile
from pathlib import Path

def load(name, path):
    if not Path(path).exists() and ":" in path:
        code = subprocess.run(["git", "show", path], capture_output=True, check=True).stdout
        tmp = Path(tempfile.mkdtemp()) / f"{name}.py"
        tmp.write_bytes(code)
        path = tmp
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

rng = random.Random(0)

def member(seq, lang, name=True):
    n = f'<personalName><fullName>人{seq}{lang}</fullName></personalName>' if name else ''
    return (f'<member sequence="{seq}" eradCode="{rng.randint(1,9999)}">{n}'
            f'<institution>機関{seq}</institution><department>部局</department>'
            f'<jobTitle>教授</jobTitle></member>')

def field(nii, path, seq, table="saimoku"):
    return f'<field niiCode="{nii}" path="{path}" sequence="{seq}" fieldTable="{table}">分野{int(nii)}</field>'

def grant(i):
    r = rng.random
    fields = ""
    kind = rng.choice(["none", "2", "3", "4x2", "other", "one"])
    if kind == "2": fields = field("000079","000079","1") + field("003044","000079003044","1")
    elif kind == "3": fields = field("000079","000079","1")+field("000414","000079000414","1")+field("000415","000079000414000415","1")
    elif kind == "4x2":
        for seq, codes in (("1",(1,2,3,4)),("2",(1,5,6,7))):
            for d in range(1,5): fields += field(f"{codes[d-1]:06}", "".join(f"{c:06}" for c in codes[:d]), seq)
    elif kind == "other": fields = field("001894","001893001894","1","tokubetsu_kenkyu")
    elif kind == "one": fields = field("001350","001350","1")
    nja = rng.choice([1,1,2,3]); nen = rng.choice([0,1,2,3])
    ja = "".join(member(s, "ja", name=r() > .2) for s in range(1, nja+1))
    en = "".join(member(s, "en") for s in range(1, nen+1))
    dates = rng.choice([
        '<periodOfAward><startDate>2001-04-01</startDate><endDate>2004-03-31</endDate></periodOfAward>',
        '<periodOfAward searchStartFiscalYear="1990" searchEndFiscalYear="1992"><startFiscalYear>1990</startFiscalYear><endFiscalYear>1992</endFiscalYear></periodOfAward>',
        '<periodOfAward><startDate>2001-04</startDate></periodOfAward>', ''])
    created = rng.choice(['2003-05-01', '1987-01-01T00:00:00Z', ''])
    review = rng.choice(['', '<review_section niiCode="000767">区分A</review_section>',
                         '<review_section niiCode="1">A</review_section><review_section niiCode="2">B</review_section>'])
    kw = rng.choice(['', '<keywordList><keyword xml:lang="ja">k1</keyword></keywordList>', '<keywordList><keyword xml:lang="ja">k1</keyword><keyword xml:lang="ja">k2</keyword></keywordList>'])
    para = rng.choice(['', '<paragraphList type="abstract"><paragraph xml:lang="ja">概要</paragraph></paragraphList>',
                       '<paragraphList type="outline_of_research_initial"><paragraph xml:lang="ja">a</paragraph><paragraph xml:lang="ja">b</paragraph></paragraphList>'])
    cost = rng.choice(['', '<overallAwardAmount><directCost>3000</directCost><indirectCost>900</indirectCost><totalCost>3900</totalCost></overallAwardAmount>'])
    inst = rng.choice(['', '<institution niiCode="12601">大学A</institution>', '<institution niiCode="12601">大学A</institution><institution niiCode="12601">大学B</institution>'])
    prods = rng.choice(['', '<productList><product type="journal_article"><year>2002</year><author xml:lang="ja">甲・乙、丙</author><title>T</title></product></productList>',
                        '<productList><product type="presentation"><author xml:lang="ja">A,B</author></product><product type="book"><year>2003</year></product></productList>'])
    reps = rng.choice(['', '<paragraphList parentId="r1"><paragraph xml:lang="ja">報告</paragraph></paragraphList>'])
    status = rng.choice(['', '<projectStatus statusCode="project_closed" fiscalYear="1985"/>'])
    created_xml = f'<created>{created}</created>' if created else ''
    return (f'<grantAward awardNumber="{i:02}K{i:05}">{created_xml}<urlList><url>https://x/{i}</url></urlList>'
            f'<summary xml:lang="ja"><title>題{i}</title><category niiCode="000072" path="000072">基盤研究(C)</category>'
            f'{fields}{review}{inst}{ja}{status}{kw}{para}{dates}{cost}</summary>'
            f'<summary xml:lang="en"><title>T{i}</title>{en}</summary>{prods}{reps}</grantAward>')


def _open_source(path):
    """.gz は展開しながら読む（KakenToTable はファイル名の .gz を展開しないため）。"""
    path = Path(path)
    return gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")


def _rows(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def compare(old, new, src, workdir):
    counts = {}
    for label, mod in (("old", old), ("new", new)):
        out = Path(workdir) / f"{label}.jsonl"
        with _open_source(src) as source:
            c = mod.KakenToTable(source)
            c.to_jsonlines(out, error_path=Path(workdir) / f"{label}.errors.tsv")
        counts[label] = (c.written, c.failed)
    print(f"old written={counts['old'][0]} failed={counts['old'][1]}; "
          f"new written={counts['new'][0]} failed={counts['new'][1]}")
    # 出力は XML の順。新版は旧版が落とした課題を含みうるので、新版を進めながら合わせる。
    mism, only_new, only_old, compared = {}, 0, 0, 0
    new_rows = _rows(Path(workdir) / "new.jsonl")
    for a in _rows(Path(workdir) / "old.jsonl"):
        for b in new_rows:
            if b["awardNumber"] == a["awardNumber"]:
                break
            only_new += 1
        else:
            only_old += 1
            continue
        compared += 1
        for col, value in a.items():
            if b.get(col, object()) != value:
                mism[col] = mism.get(col, 0) + 1
    only_new += sum(1 for _ in new_rows)
    print(f"compared rows={compared}; rows only in old: {only_old}; rows only in new: {only_new}")
    print(f"mismatched cells: {sum(mism.values())} {mism if mism else ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old"); ap.add_argument("new")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--xml", nargs="+", help=".xml または .xml.gz。複数指定できる")
    g.add_argument("--synthetic", type=int)
    args = ap.parse_args()
    old, new = load("old_cleaner", args.old), load("new_cleaner", args.new)
    with tempfile.TemporaryDirectory() as d:
        if args.synthetic:
            src = Path(d) / "in.xml"
            src.write_text("<grantAwards>" + "".join(grant(i) for i in range(args.synthetic))
                           + "</grantAwards>", encoding="utf-8")
            compare(old, new, src, d)
        else:
            for src in args.xml:
                print(f"== {src}")
                compare(old, new, src, d)

if __name__ == "__main__":
    main()
